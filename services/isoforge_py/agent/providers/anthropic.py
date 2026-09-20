"""Anthropic Messages API adapter.

Differs from the OpenAI format in three ways that matter here: the system prompt is a
top-level field rather than a message, content is a list of typed blocks, and tool
results are user-role blocks rather than a dedicated role.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import CompletionResult, LLMProvider, Message, ProviderError, ToolCall, ToolSpec

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_MODEL = "claude-sonnet-4-5"
API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        max_tokens: int = 8192,
    ) -> None:
        super().__init__(model, timeout=timeout)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
        self.base_url = (
            base_url or os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.max_tokens = max_tokens
        self._client: httpx.AsyncClient | None = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system" and m.content)
        return system, [m for m in messages if m.role != "system"]

    @staticmethod
    def _encode_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                # Tool results come back as a user turn containing a result block.
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content or "",
                }
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue

            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for i, tc in enumerate(m.tool_calls):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.call_id or f"call_{i}",
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
                continue

            out.append({"role": m.role, "content": m.content or ""})
        return out

    @staticmethod
    def _encode_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        if not self.available():
            raise ProviderError("ANTHROPIC_API_KEY is not set")

        system, rest = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "messages": self._encode_messages(rest),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._encode_tools(tools)
            payload["tool_choice"] = (
                {"type": "tool", "name": tool_choice} if tool_choice else {"type": "auto"}
            )

        try:
            response = await self._http().post("/messages", json=payload)
        except httpx.RequestError as exc:
            raise ProviderError(f"Anthropic unreachable: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            retryable = response.status_code in (429, 529) or response.status_code >= 500
            raise ProviderError(
                f"Anthropic returned {response.status_code}: {response.text[:400]}",
                retryable=retryable,
            )

        data = response.json()
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                        call_id=block.get("id", ""),
                    )
                )

        usage = data.get("usage") or {}
        return CompletionResult(
            text="".join(text_parts),
            tool_calls=calls,
            model=data.get("model", self.model),
            finish_reason=data.get("stop_reason", ""),
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
        )
