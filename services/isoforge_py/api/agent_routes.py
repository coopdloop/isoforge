"""agent_orchestrator routes (merged into the single Python app)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent.orchestrator import Conversation, Orchestrator, TurnError
from ..agent.providers import build_provider, detect_available
from ..agent.tools import describe_tools
from ..isodsl import validate
from ..isodsl.canonical import scene_hash

router = APIRouter(tags=["chat"])

#: Local single-user tool: an in-process dict is the right amount of machinery.
_conversations: dict[str, Conversation] = {}


class CreateConversationRequest(BaseModel):
    project_id: str = ""
    title: str = ""
    provider: str | None = None
    model: str | None = None
    scene: dict[str, Any] | None = None


class TurnRequest(BaseModel):
    message: str = Field(min_length=1)
    model_override: str | None = None
    provider_override: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class ReloadRequest(BaseModel):
    scene: dict[str, Any]


class ThemeLockRequest(BaseModel):
    locked: bool = True


def _get(conversation_id: str) -> Conversation:
    conversation = _conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail={"error": "conversation not found"})
    return conversation


@router.get("/providers", summary="List configured LLM providers")
def list_providers() -> dict:
    available = detect_available()
    return {
        "available": available,
        "default": available[0] if available else None,
        "all": ["openrouter", "anthropic", "ollama"],
    }


@router.get("/tools", summary="Introspect the tool schemas offered to the model")
def get_tools() -> dict:
    return {"tools": describe_tools()}


@router.post("/conversations", status_code=201, summary="Start a conversation")
def create_conversation(req: CreateConversationRequest) -> dict:
    if req.scene is not None:
        result = validate(req.scene)
        if not result.valid:
            raise HTTPException(
                status_code=400,
                detail={"error": "seed scene failed validation", **result.to_dict()},
            )

    provider = build_provider(req.provider, req.model)
    conversation = Conversation(
        project_id=req.project_id,
        title=req.title,
        model_provider=provider.name,
        model_name=provider.model,
        scene=req.scene,
    )
    _conversations[conversation.id] = conversation
    return conversation.to_dict()


@router.get("/conversations/{conversation_id}", summary="Conversation state")
def get_conversation(conversation_id: str) -> dict:
    conversation = _get(conversation_id)
    return {**conversation.to_dict(), "scene": conversation.scene}


@router.delete("/conversations/{conversation_id}", status_code=204, summary="End a conversation")
def delete_conversation(conversation_id: str) -> None:
    _get(conversation_id)
    _conversations.pop(conversation_id, None)


@router.post("/conversations/{conversation_id}/turns", summary="Process one chat turn")
async def run_turn(conversation_id: str, req: TurnRequest) -> dict:
    conversation = _get(conversation_id)
    provider = build_provider(
        req.provider_override or conversation.model_provider,
        req.model_override or conversation.model_name,
    )
    try:
        result = await Orchestrator(provider).run_turn(
            conversation, req.message, temperature=req.temperature
        )
    except TurnError as exc:
        # 422: the request was well-formed but the model could not satisfy it.
        raise HTTPException(
            status_code=422, detail={"error": str(exc), "errors": exc.errors}
        ) from exc
    finally:
        await provider.aclose()
    return result.to_dict()


@router.post("/conversations/{conversation_id}/reload", summary="Reload scene into context")
def reload_scene(conversation_id: str, req: ReloadRequest) -> dict:
    """Round-trip entry point for `isoforge chat --continue scene.isoforge.json`."""
    conversation = _get(conversation_id)
    result = validate(req.scene)
    if not result.valid:
        raise HTTPException(
            status_code=400,
            detail={"error": "scene failed validation", **result.to_dict()},
        )
    conversation.scene = req.scene
    return {**conversation.to_dict(), "scene_hash": scene_hash(req.scene)}


@router.post("/conversations/{conversation_id}/theme-lock", summary="Lock or unlock the palette")
def set_theme_lock(conversation_id: str, req: ThemeLockRequest) -> dict:
    conversation = _get(conversation_id)
    conversation.theme_locked = req.locked
    if conversation.scene is not None:
        conversation.scene.setdefault("palette", {})["locked"] = req.locked
    return {"conversation_id": conversation.id, "theme_locked": req.locked}
