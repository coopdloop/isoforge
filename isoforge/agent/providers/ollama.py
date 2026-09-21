"""Ollama adapter for local/offline inference.

ADR-001 requires an "equivalent constrained-output shim" for local models. Modern Ollama
builds support native tool calling, but coverage varies sharply by model, so this adapter
tries native tools first and falls back to JSON-schema-constrained generation, presenting
the result as a normal ToolCall either way. The orchestrator above cannot tell which path
was taken, which keeps the trust boundary identical for local and hosted models.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .base import CompletionResult, LLMProvider, Message, ProviderError, ToolCall, ToolSpec

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        host: str | None = None,
        timeout: float = 300.0,
        force_constrained: bool = False,
    ) -> None:
        # Local models are slower; the default timeout is deliberately generous.
        super().__init__(model, timeout=timeout)
        self.host = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
        #: Skip the native-tools attempt entirely (useful for models known to fake it).
        self.force_constrained = force_constrained
        self._client: httpx.AsyncClient | None = None
        self._supports_tools: bool | None = None

    def available(self) -> bool:
        """Ollama needs no key; treat a reachable daemon as available."""
        try:
            with httpx.Client(timeout=2.0) as client:
                return client.get(f"{self.host}/api/tags").status_code == 200
        except httpx.RequestError:
            return False

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.host, timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _encode_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "content": m.content or ""})
                continue
            entry: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in m.tool_calls
                ]
            out.append(entry)
        return out

    @staticmethod
    def _constrained_envelope(tools: list[ToolSpec]) -> dict[str, Any]:
        """A JSON Schema that forces the model to pick exactly one tool and fill it in.

        Ollama's `format` parameter constrains decoding to this schema, which gives us
        the same guarantee native tool calling does: parseable, well-typed output or
        nothing at all.
        """
        return {
            "type": "object",
            "required": ["tool", "arguments"],
            "properties": {
                "tool": {"type": "string", "enum": [t.name for t in tools]},
                "arguments": {"type": "object"},
                "reply": {"type": "string"},
            },
        }

    def _constrained_instructions(self, tools: list[ToolSpec]) -> str:
        lines = [
            "You must respond with a single JSON object and nothing else.",
            'Shape: {"tool": "<name>", "arguments": {...}, "reply": "<short message>"}',
            "",
            "Available tools:",
        ]
        for t in tools:
            lines.append(f"- {t.name}: {t.description}")
            lines.append(f"  arguments schema: {json.dumps(t.parameters)}")
        return "\n".join(lines)

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http().post("/api/chat", json=payload)
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Ollama unreachable at {self.host}: {exc}", retryable=True
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"Ollama returned {response.status_code}: {response.text[:400]}",
                retryable=response.status_code >= 500,
            )
        return response.json()

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        encoded = self._encode_messages(messages)
        options = {"temperature": temperature}

        # Path 1: native tool calling, when the model supports it.
        if tools and not self.force_constrained and self._supports_tools is not False:
            payload = {
                "model": self.model,
                "messages": encoded,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                    for t in tools
                ],
                "stream": False,
                "options": options,
            }
            try:
                data = await self._post_chat(payload)
                message = data.get("message") or {}
                raw_calls = message.get("tool_calls") or []
                if raw_calls:
                    self._supports_tools = True
                    return CompletionResult(
                        text=message.get("content") or "",
                        tool_calls=[
                            ToolCall.from_json(
                                name=(c.get("function") or {}).get("name", ""),
                                raw=(c.get("function") or {}).get("arguments", {}),
                            )
                            for c in raw_calls
                        ],
                        model=self.model,
                        finish_reason=data.get("done_reason", ""),
                    )
                # Model answered in prose despite tools being offered: fall through.
            except ProviderError:
                if self._supports_tools is True:
                    raise
                self._supports_tools = False

        if not tools:
            data = await self._post_chat(
                {"model": self.model, "messages": encoded, "stream": False, "options": options}
            )
            return CompletionResult(
                text=(data.get("message") or {}).get("content", ""), model=self.model
            )

        # Path 2: constrained decoding shim.
        shim_messages = [
            {"role": "system", "content": self._constrained_instructions(tools)},
            *encoded,
        ]
        data = await self._post_chat(
            {
                "model": self.model,
                "messages": shim_messages,
                "stream": False,
                "format": self._constrained_envelope(tools),
                "options": options,
            }
        )

        content = (data.get("message") or {}).get("content", "")
        try:
            envelope = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"constrained output was not valid JSON: {content[:300]}") from exc

        tool_name = envelope.get("tool")
        if not tool_name:
            return CompletionResult(text=envelope.get("reply", content), model=self.model)

        return CompletionResult(
            text=envelope.get("reply", ""),
            tool_calls=[ToolCall(name=tool_name, arguments=envelope.get("arguments") or {})],
            model=self.model,
            finish_reason=data.get("done_reason", ""),
        )
