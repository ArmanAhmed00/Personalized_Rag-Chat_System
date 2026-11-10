"""Any OpenAI-compatible server: LM Studio, llama.cpp server, vLLM, or a cloud API.

Streams Server-Sent Events from ``/chat/completions`` and asks for a final usage chunk.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from sidelamp.llm.base import (
    CheckResult,
    Event,
    HttpProvider,
    LLMError,
    Message,
    TextDelta,
    Usage,
    iter_sse,
)


class OpenAICompatible(HttpProvider):
    provider = "openai"
    label = "The OpenAI-compatible server"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        cloud: bool = False,
    ) -> None:
        super().__init__(base_url, model, timeout=timeout, transport=transport, cloud=cloud)
        self.api_key = api_key

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def stream(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Event]:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream_options": {"include_usage": True},
        }
        usage = Usage()
        try:
            async with (
                self.client() as client,
                client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=body, headers=self.headers()
                ) as response,
            ):
                if response.status_code >= 400:
                    raise self.status_error(
                        response.status_code, (await response.aread()).decode(errors="replace")
                    )
                async for _, data in iter_sse(response):
                    if data.strip() == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        raise LLMError(
                            f"The server reported an error: {str(chunk['error'])[:200]}", "refused"
                        )
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content") or ""
                        if text:
                            yield TextDelta(text)
                    if chunk.get("usage"):
                        usage = Usage(
                            int(chunk["usage"].get("prompt_tokens") or 0),
                            int(chunk["usage"].get("completion_tokens") or 0),
                        )
        except (httpx.HTTPError, ValueError) as exc:
            raise self.translate(exc) from None
        yield usage

    async def check(self) -> CheckResult:
        try:
            async with self.client() as client:
                response = await client.get(f"{self.base_url}/models", headers=self.headers())
        except httpx.HTTPError:
            return CheckResult(
                False,
                f"Nothing is answering at {self.base_url}. Check the "
                f"address and that the server is running.",
            )
        if response.status_code in (401, 403):
            return CheckResult(False, "The server refused the API key.")
        if response.status_code != 200:
            return CheckResult(
                False, f"The server answered with an error ({response.status_code})."
            )
        try:
            models = [m.get("id", "") for m in response.json().get("data", [])]
        except ValueError:
            models = []
        if models and self.model not in models:
            return CheckResult(
                False,
                f"Connected, but the model “{self.model}” isn't offered. "
                f"Available: {', '.join(models[:8])}.",
                models,
            )
        return CheckResult(True, f"Connected. “{self.model}” is ready.", models)
