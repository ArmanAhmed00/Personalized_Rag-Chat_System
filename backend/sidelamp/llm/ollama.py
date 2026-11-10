"""Ollama (the default local model server). Streams newline-delimited JSON from ``/api/chat``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from sidelamp.llm.base import CheckResult, Event, HttpProvider, LLMError, Message, TextDelta, Usage


class OllamaProvider(HttpProvider):
    provider = "ollama"
    label = "Ollama"

    async def stream(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Event]:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            async with (
                self.client() as client,
                client.stream("POST", f"{self.base_url}/api/chat", json=body) as response,
            ):
                if response.status_code == 404:
                    await response.aread()
                    raise LLMError(
                        f"Ollama doesn't have the model “{self.model}”. Install it "
                        f"with: ollama pull {self.model}",
                        "model_missing",
                    )
                if response.status_code >= 400:
                    raise self.status_error(
                        response.status_code, (await response.aread()).decode(errors="replace")
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LLMError(f"Ollama: {str(data['error'])[:200]}", "refused")
                    text = (data.get("message") or {}).get("content") or ""
                    if text:
                        yield TextDelta(text)
                    if data.get("done"):
                        yield Usage(
                            int(data.get("prompt_eval_count") or 0),
                            int(data.get("eval_count") or 0),
                        )
                        return
        except (httpx.HTTPError, ValueError) as exc:
            raise self.translate(exc) from None

    async def check(self) -> CheckResult:
        try:
            async with self.client() as client:
                response = await client.get(f"{self.base_url}/api/tags")
        except httpx.HTTPError:
            return CheckResult(
                False,
                f"Ollama isn't answering at {self.base_url}. Install it "
                f"from ollama.com and start it.",
            )
        if response.status_code != 200:
            return CheckResult(False, f"Ollama answered with an error ({response.status_code}).")
        models = [m.get("name", "") for m in response.json().get("models", [])]
        if not any(name == self.model or name.split(":")[0] == self.model for name in models):
            return CheckResult(
                False,
                f"Ollama is running, but the model “{self.model}” isn't "
                f"installed. Run: ollama pull {self.model}",
                models,
            )
        return CheckResult(True, f"Connected to Ollama. “{self.model}” is ready.", models)
