"""Scripted provider for deterministic agent tests.

Testing the orchestrator against a real model would be slow, costly and flaky. This
replays a fixed script of responses, which lets us assert the exact behaviour of the
validate-repair loop, including paths a real model reaches only occasionally.
"""

from __future__ import annotations

from isoforge.agent.providers.base import (
    CompletionResult,
    LLMProvider,
    Message,
    ToolCall,
    ToolSpec,
)


class FakeProvider(LLMProvider):
    """Returns queued CompletionResults in order, recording what it was asked."""

    name = "fake"

    def __init__(self, script: list[CompletionResult | Exception], model: str = "fake-1"):
        super().__init__(model)
        self.script = list(script)
        self.calls: list[list[Message]] = []
        self.tools_offered: list[list[ToolSpec]] = []

    def available(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        self.tools_offered.append(list(tools))
        if not self.script:
            raise AssertionError("FakeProvider script exhausted: unexpected extra call")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_system_prompt(self) -> str:
        for message in self.calls[-1]:
            if message.role == "system":
                return message.content
        return ""


def set_scene_result(scene: dict, summary: str = "set scene", text: str = "") -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[
            ToolCall(
                name="set_scene",
                arguments={"scene": scene, "summary": summary},
                call_id="call_set",
            )
        ],
        model="fake-1",
    )


def patch_scene_result(
    ops: list[dict], summary: str = "patch scene", text: str = ""
) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[
            ToolCall(
                name="patch_scene",
                arguments={"ops": ops, "summary": summary},
                call_id="call_patch",
            )
        ],
        model="fake-1",
    )


def save_theme_result(name: str, colors: dict | None = None) -> CompletionResult:
    args: dict = {"name": name}
    if colors is not None:
        args["colors"] = colors
    return CompletionResult(
        text=f"Saved theme {name}.",
        tool_calls=[ToolCall(name="save_theme", arguments=args, call_id="call_theme")],
        model="fake-1",
    )


def text_result(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="fake-1")
