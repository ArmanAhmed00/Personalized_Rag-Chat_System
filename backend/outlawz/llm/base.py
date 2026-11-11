"""The LLM provider interface: one small async streaming method.

``stream()`` yields :class:`TextDelta` pieces of the answer as they arrive and, at the end, one
:class:`Usage` with token counts. Errors are raised as :class:`LLMError` with a plain-language
message the UI can show as is.

To add a provider: subclass :class:`HttpProvider` (or implement :class:`LLM`), parse the
provider's streaming format in ``stream()``, add it to ``make_llm`` below, and add one test that
feeds a recorded response through ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from outlawz.config import Settings

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class TextDelta:
    text: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


Event = TextDelta | Usage


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class CheckResult:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)


class LLMError(Exception):
    def __init__(self, message: str, code: str = "llm_error", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class LLM(Protocol):
    provider: str
    model: str
    cloud: bool

    def stream(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Event]: ...

    async def check(self) -> CheckResult: ...


async def complete(
    llm: LLM, messages: list[Message], *, max_tokens: int, temperature: float = 0.0
) -> Completion:
    """Collect a whole (non-streamed) answer."""
    parts: list[str] = []
    usage = Usage()
    async for event in llm.stream(messages, max_tokens=max_tokens, temperature=temperature):
        if isinstance(event, TextDelta):
            parts.append(event.text)
        else:
            usage = event
    return Completion("".join(parts), usage)


def shown_url(url: str) -> str:
    """A URL safe to show: no user name, password or query string (they can hold keys)."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class HttpProvider:
    """Shared plumbing for HTTP providers: client, timeouts and error translation."""

    provider = "http"
    label = "The model server"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        cloud: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.cloud = cloud

    def client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(self.timeout, connect=10.0)
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    def translate(self, exc: Exception) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, httpx.ConnectError):
            return LLMError(
                f"{self.label} isn't answering at {shown_url(self.base_url)}. Is it running?",
                "unreachable",
                retryable=True,
            )
        if isinstance(exc, httpx.TimeoutException):
            return LLMError(
                "The model took too long to answer. Try again, or choose a smaller "
                "model in Settings.",
                "timeout",
                retryable=True,
            )
        if isinstance(exc, httpx.HTTPError):
            return LLMError(
                f"{self.label} closed the connection unexpectedly.", "network", retryable=True
            )
        return LLMError("The model's answer couldn't be read.", "bad_response")

    def status_error(self, status: int, body: str) -> LLMError:
        if status in (401, 403):
            return LLMError("The API key was refused. Check it in Settings.", "auth")
        if status == 404:
            return LLMError(
                f"The model “{self.model}” wasn't found. Check the model name in Settings.",
                "model_missing",
            )
        if status == 429:
            return LLMError(
                "The model provider is limiting requests right now. Try again in a moment.",
                "rate_limited",
                retryable=True,
            )
        if status >= 500:
            return LLMError(
                f"{self.label} had an internal problem ({status}). Try again.",
                "server_error",
                retryable=True,
            )
        detail = _error_detail(body)
        return LLMError(
            f"{self.label} refused the request ({status}){': ' + detail if detail else ''}.",
            "refused",
        )


def _error_detail(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return str(error)[:200] if error else ""


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Parse a Server-Sent Events stream into ``(event, data)`` pairs."""
    event = "message"
    data: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


def make_llm(settings: Settings) -> LLM:
    if settings.llm == "fake":
        from outlawz.llm.fake import FakeLLM

        return FakeLLM()
    if settings.llm == "anthropic":
        from outlawz.llm.anthropic import AnthropicProvider

        return AnthropicProvider(
            settings.anthropic_key, settings.llm_model, timeout=settings.llm_timeout
        )
    if settings.llm == "openai":
        from outlawz.llm.openai import OpenAICompatible

        return OpenAICompatible(
            settings.openai_url,
            settings.llm_model,
            settings.openai_key,
            timeout=settings.llm_timeout,
            cloud=settings.is_cloud_llm,
        )
    from outlawz.llm.ollama import OllamaProvider

    return OllamaProvider(
        settings.ollama_url,
        settings.llm_model,
        timeout=settings.llm_timeout,
        cloud=settings.is_cloud_llm,
    )


def llm_key(settings: Settings) -> tuple[Any, ...]:
    """Settings that require a new provider object when they change."""
    return (
        settings.llm,
        settings.llm_model,
        settings.ollama_url,
        settings.openai_url,
        settings.openai_key,
        settings.anthropic_key,
        settings.llm_timeout,
    )
