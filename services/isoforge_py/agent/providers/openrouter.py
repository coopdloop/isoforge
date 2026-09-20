"""OpenRouter adapter (default provider).

OpenRouter speaks the OpenAI chat-completions wire format, so this adapter also covers
plain OpenAI and any other OpenAI-compatible endpoint by overriding base_url.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import CompletionResult, LLMProvider, Message, ProviderError, ToolCall, ToolSpec

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(model, timeout=timeout)
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
        self.base_url = (
            base_url or os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    # OpenRouter uses these for attribution on its dashboard.
                    "HTTP-Referer": "https://github.com/coopdloop/isoforge",
                    "X-Title": "IsoForge",
                },
            )
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
                out.append(
                    {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
                )
                continue
            entry: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.call_id or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": __import__("json").dumps(tc.arguments),
                        },
                    }
                    for i, tc in enumerate(m.tool_calls)
                ]
            out.append(entry)
        return out

    @staticmethod
    def _encode_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
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
            raise ProviderError("OPENROUTER_API_KEY is not set")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._encode_messages(messages),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = self._encode_tools(tools)
            payload["tool_choice"] = (
                {"type": "function", "function": {"name": tool_choice}}
                if tool_choice
                else "auto"
            )

        try:
            response = await self._http().post("/chat/completions", json=payload)
        except httpx.RequestError as exc:
            raise ProviderError(f"OpenRouter unreachable: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            # 429 and 5xx are worth retrying; 4xx auth/validation errors are not.
            retryable = response.status_code == 429 or response.status_code >= 500
            raise ProviderError(
                f"OpenRouter returned {response.status_code}: {response.text[:400]}",
                retryable=retryable,
            )

        data = response.json()
        if "choices" not in data:
            raise ProviderError(f"unexpected OpenRouter response: {str(data)[:400]}")

        choice = data["choices"][0]
        message = choice.get("message") or {}
        calls = [
            ToolCall.from_json(
                name=(tc.get("function") or {}).get("name", ""),
                raw=(tc.get("function") or {}).get("arguments", "{}"),
                call_id=tc.get("id", ""),
            )
            for tc in (message.get("tool_calls") or [])
        ]

        return CompletionResult(
            text=message.get("content") or "",
            tool_calls=calls,
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason", ""),
            usage=data.get("usage") or {},
        )
