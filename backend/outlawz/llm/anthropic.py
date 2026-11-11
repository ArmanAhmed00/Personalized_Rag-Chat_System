"""Anthropic's Messages API (optional, cloud). Streams Server-Sent Events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from outlawz.llm.base import (
    CheckResult,
    Event,
    HttpProvider,
    LLMError,
    Message,
    TextDelta,
    Usage,
    iter_sse,
)

API_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


class AnthropicProvider(HttpProvider):
    provider = "anthropic"
    label = "Anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = API_URL,
    ) -> None:
        super().__init__(base_url, model, timeout=timeout, transport=transport, cloud=True)
        self.api_key = api_key

    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": API_VERSION}

    async def stream(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Event]:
        if not self.api_key:
            raise LLMError("Add an Anthropic API key in Settings to use this provider.", "auth")
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        body: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": turns,
            "stream": True,
        }
        if system:
            body["system"] = system
        usage = Usage()
        try:
            async with (
                self.client() as client,
                client.stream(
                    "POST", f"{self.base_url}/messages", json=body, headers=self.headers()
                ) as response,
            ):
                if response.status_code >= 400:
                    raise self.status_error(
                        response.status_code, (await response.aread()).decode(errors="replace")
                    )
                async for event, data in iter_sse(response):
                    payload = json.loads(data) if data else {}
                    kind = payload.get("type", event)
                    if kind == "message_start":
                        start = (payload.get("message") or {}).get("usage") or {}
                        usage.prompt_tokens = int(start.get("input_tokens") or 0)
                    elif kind == "content_block_delta":
                        delta = payload.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield TextDelta(delta["text"])
                    elif kind == "message_delta":
                        out = payload.get("usage") or {}
                        usage.completion_tokens = int(out.get("output_tokens") or 0)
                    elif kind == "error":
                        message = (payload.get("error") or {}).get("message", "unknown error")
                        raise LLMError(
                            f"Anthropic reported an error: {str(message)[:200]}",
                            "refused",
                            retryable=True,
                        )
                    elif kind == "message_stop":
                        break
        except (httpx.HTTPError, ValueError) as exc:
            raise self.translate(exc) from None
        yield usage

    async def check(self) -> CheckResult:
        if not self.api_key:
            return CheckResult(False, "Add an Anthropic API key first.")
        try:
            async with self.client() as client:
                response = await client.get(f"{self.base_url}/models", headers=self.headers())
        except httpx.HTTPError:
            return CheckResult(False, "Couldn't reach Anthropic. Check your internet connection.")
        if response.status_code in (401, 403):
            return CheckResult(False, "Anthropic refused the API key.")
        if response.status_code != 200:
            return CheckResult(False, f"Anthropic answered with an error ({response.status_code}).")
        models = [m.get("id", "") for m in response.json().get("data", [])]
        return CheckResult(True, f"Connected to Anthropic. Using “{self.model}”.", models)
