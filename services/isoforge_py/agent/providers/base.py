"""Provider-agnostic LLM tool-calling interface.

ADR-001 requires that design data arrive only as structured tool calls, never as parsed
prose. Every provider adapter therefore normalises to the same ToolCall shape, and the
orchestrator refuses to look at anything else.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    """One normalised tool invocation from the model."""

    name: str
    arguments: dict[str, Any]
    call_id: str = ""

    @classmethod
    def from_json(cls, name: str, raw: str | dict, call_id: str = "") -> "ToolCall":
        """Parse arguments, tolerating the string-encoded JSON most providers emit."""
        if isinstance(raw, dict):
            return cls(name=name, arguments=raw, call_id=call_id)
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ProviderError(f"tool '{name}' returned unparseable arguments: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderError(f"tool '{name}' arguments must be an object")
        return cls(name=name, arguments=parsed, call_id=call_id)


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: Set on tool-result messages to correlate with the originating call.
    tool_call_id: str = ""
    name: str = ""


@dataclass(slots=True)
class ToolSpec:
    """A function the model may call. Parameters are a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class CompletionResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ProviderError(RuntimeError):
    """Transport, auth, or protocol failure talking to a model backend."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(ABC):
    """Common surface across OpenRouter, Anthropic and Ollama."""

    #: Stable identifier used in config, CLI flags and the conversations table.
    name: str = "base"

    def __init__(self, model: str, *, timeout: float = 120.0) -> None:
        self.model = model
        self.timeout = timeout

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        """Run one turn. Implementations must not raise on refusals, only on transport."""

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider is configured well enough to attempt a call."""

    async def aclose(self) -> None:
        """Release transport resources."""

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "available": self.available()}
