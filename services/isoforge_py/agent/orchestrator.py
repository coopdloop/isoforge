"""Conversation orchestration and the validate-repair loop.

This module is where ADR-001 is actually enforced: a turn can only change the design by
emitting a tool call whose payload survives full IsoDSL validation. Invalid payloads are
never persisted, never broadcast, and never silently patched up; they are fed back to the
model as structured errors for a bounded number of retries.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import jsonpatch

from ..isodsl import validate
from ..isodsl.canonical import scene_hash
from .prompts import build_system_prompt, repair_prompt
from .providers.base import LLMProvider, Message, ProviderError, ToolCall
from .tools import PATCH_SCENE, SAVE_THEME, SET_SCENE, tools_for

#: How many times the model may be asked to fix its own invalid output.
MAX_REPAIR_ATTEMPTS = 2


class TurnError(RuntimeError):
    """A turn failed in a way the user needs to know about."""

    def __init__(self, message: str, *, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


@dataclass(slots=True)
class TurnResult:
    """Outcome of one chat turn."""

    reply: str
    scene: dict | None = None
    patch: list[dict] | None = None
    is_full_scene: bool = False
    summary: str = ""
    scene_hash: str = ""
    changed: bool = False
    repair_attempts: int = 0
    theme_saved: dict | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "scene": self.scene,
            "patch": self.patch,
            "is_full_scene": self.is_full_scene,
            "summary": self.summary,
            "scene_hash": self.scene_hash,
            "changed": self.changed,
            "repair_attempts": self.repair_attempts,
            "theme_saved": self.theme_saved,
            "usage": self.usage,
        }


@dataclass
class Conversation:
    """In-memory conversation state.

    Durable history lives in scene_store; this holds the working transcript the model
    sees, which is deliberately narrower than the full audit log.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str = ""
    title: str = ""
    model_provider: str = ""
    model_name: str = ""
    scene: dict | None = None
    theme_locked: bool = False
    messages: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "theme_locked": self.theme_locked,
            "has_scene": self.scene is not None,
            "scene_hash": scene_hash(self.scene) if self.scene else "",
            "message_count": len(self.messages),
        }


def apply_patch(scene: dict, ops: list[dict]) -> dict:
    """Apply an RFC-6902 patch to a copy of the scene."""
    try:
        return jsonpatch.apply_patch(copy.deepcopy(scene), ops)
    except jsonpatch.JsonPatchException as exc:
        raise TurnError(f"patch could not be applied: {exc}") from exc
    except Exception as exc:  # jsonpointer raises its own error types
        raise TurnError(f"patch referenced an invalid path: {exc}") from exc


def _clamp_to_palette(scene: dict) -> dict:
    """Under theme lock, rewrite stray literal colors back to palette references.

    The model is told not to do this, but a cheap deterministic fix is better than
    burning a repair round-trip on a trivial mistake.
    """
    palette = (scene.get("palette") or {}).get("colors") or {}
    if not palette:
        return scene
    literal_to_ref = {v.upper(): f"@palette.{k}" for k, v in palette.items()}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str) and node.startswith("#"):
            return literal_to_ref.get(node.upper(), node)
        return node

    out = copy.deepcopy(scene)
    out["shapes"] = walk(out.get("shapes") or [])
    if "effects" in out:
        out["effects"] = walk(out["effects"])
    return out


class Orchestrator:
    """Runs chat turns against a provider and guards the design document."""

    def __init__(self, provider: LLMProvider, *, max_repair_attempts: int = MAX_REPAIR_ATTEMPTS):
        self.provider = provider
        self.max_repair_attempts = max_repair_attempts

    def _candidate_scene(self, call: ToolCall, current: dict | None) -> tuple[dict, list | None, str]:
        """Turn a tool call into a proposed scene. Returns (scene, patch_ops, summary)."""
        args = call.arguments
        summary = str(args.get("summary") or "")

        if call.name == SET_SCENE:
            scene = args.get("scene")
            if not isinstance(scene, dict):
                raise TurnError("set_scene was called without a scene object")
            return scene, None, summary

        if call.name == PATCH_SCENE:
            if current is None:
                raise TurnError("patch_scene was called before any scene exists")
            ops = args.get("ops")
            if not isinstance(ops, list) or not ops:
                raise TurnError("patch_scene was called without operations")
            return apply_patch(current, ops), ops, summary

        raise TurnError(f"unknown tool '{call.name}'")

    async def run_turn(
        self, conversation: Conversation, user_message: str, *, temperature: float = 0.2
    ) -> TurnResult:
        """Execute one user turn, including validation and bounded repair."""
        conversation.messages.append(Message(role="user", content=user_message))

        working: list[Message] = [
            Message(
                role="system",
                content=build_system_prompt(
                    conversation.scene, theme_locked=conversation.theme_locked
                ),
            ),
            *conversation.messages,
        ]

        last_errors: list[dict] = []
        theme_saved: dict | None = None
        usage: dict[str, int] = {}

        for attempt in range(self.max_repair_attempts + 1):
            try:
                result = await self.provider.complete(
                    working,
                    tools_for(conversation.scene is not None),
                    temperature=temperature,
                )
            except ProviderError as exc:
                raise TurnError(f"model call failed: {exc}") from exc

            usage = result.usage or usage

            # Side-effect-only tool; does not touch the design document.
            design_calls = []
            for call in result.tool_calls:
                if call.name == SAVE_THEME:
                    theme_saved = {
                        "name": call.arguments.get("name", "untitled"),
                        "colors": call.arguments.get("colors")
                        or ((conversation.scene or {}).get("palette") or {}).get("colors")
                        or {},
                    }
                else:
                    design_calls.append(call)

            if not design_calls:
                # Pure conversation: no design change, nothing to validate.
                reply = result.text or "(no response)"
                conversation.messages.append(Message(role="assistant", content=reply))
                return TurnResult(
                    reply=reply,
                    scene=conversation.scene,
                    scene_hash=scene_hash(conversation.scene) if conversation.scene else "",
                    changed=False,
                    repair_attempts=attempt,
                    theme_saved=theme_saved,
                    usage=usage,
                )

            call = design_calls[0]
            try:
                candidate, ops, summary = self._candidate_scene(call, conversation.scene)
            except TurnError as exc:
                last_errors = [{"code": "TOOL", "message": str(exc)}]
                if attempt >= self.max_repair_attempts:
                    raise
                working.append(Message(role="assistant", content="", tool_calls=[call]))
                working.append(
                    Message(
                        role="tool",
                        content=str(exc),
                        tool_call_id=call.call_id,
                        name=call.name,
                    )
                )
                working.append(
                    Message(
                        role="user",
                        content=repair_prompt(str(exc), attempt + 1, self.max_repair_attempts),
                    )
                )
                continue

            if conversation.theme_locked:
                candidate = _clamp_to_palette(candidate)

            verdict = validate(candidate)
            if verdict.valid:
                conversation.scene = candidate
                reply = result.text or summary or "Updated the design."
                conversation.messages.append(
                    Message(role="assistant", content=reply, tool_calls=[call])
                )
                return TurnResult(
                    reply=reply,
                    scene=candidate,
                    patch=ops,
                    is_full_scene=ops is None,
                    summary=summary,
                    scene_hash=scene_hash(candidate),
                    changed=True,
                    repair_attempts=attempt,
                    theme_saved=theme_saved,
                    usage=usage,
                )

            # Invalid: feed the structured errors back and try again.
            last_errors = [e.to_dict() for e in verdict.errors]
            if attempt >= self.max_repair_attempts:
                break

            working.append(Message(role="assistant", content=result.text, tool_calls=[call]))
            working.append(
                Message(
                    role="tool",
                    content=json.dumps(verdict.to_dict()),
                    tool_call_id=call.call_id,
                    name=call.name,
                )
            )
            working.append(
                Message(
                    role="user",
                    content=repair_prompt(
                        verdict.as_prompt(), attempt + 1, self.max_repair_attempts
                    ),
                )
            )

        raise TurnError(
            f"model could not produce a valid scene after {self.max_repair_attempts + 1} "
            "attempts; the design was left unchanged",
            errors=last_errors,
        )
