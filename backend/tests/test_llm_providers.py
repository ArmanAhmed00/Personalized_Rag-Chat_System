"""Each provider's real response parsing, fed through ``httpx.MockTransport`` (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from sidelamp.config import Settings
from sidelamp.llm.anthropic import AnthropicProvider
from sidelamp.llm.base import LLMError, Message, TextDelta, Usage, complete, iter_sse, make_llm
from sidelamp.llm.fake import FakeLLM
from sidelamp.llm.ollama import OllamaProvider
from sidelamp.llm.openai import OpenAICompatible

pytestmark = pytest.mark.anyio

MESSAGES = [Message("system", "Be brief."), Message("user", "Hi")]


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def collect(llm: object) -> tuple[str, Usage]:
    parts: list[str] = []
    usage = Usage()
    async for event in llm.stream(MESSAGES, max_tokens=50, temperature=0.1):  # type: ignore[attr-defined]
        if isinstance(event, TextDelta):
            parts.append(event.text)
        else:
            usage = event
    return "".join(parts), usage


# ---------------------------------------------------------------- Ollama


async def test_ollama_streams_ndjson() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        lines = [
            {"message": {"role": "assistant", "content": "The window "}, "done": False},
            {"message": {"role": "assistant", "content": "is 30 days [1]."}, "done": False},
            {"done": True, "prompt_eval_count": 42, "eval_count": 9},
        ]
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines) + "\n")

    llm = OllamaProvider("http://127.0.0.1:11434", "llama3.1:8b", transport=transport(handler))
    text, usage = await collect(llm)
    assert text == "The window is 30 days [1]."
    assert (usage.prompt_tokens, usage.completion_tokens) == (42, 9)
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream"] is True and body["options"]["num_predict"] == 50
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}


async def test_ollama_missing_model_says_how_to_install() -> None:
    llm = OllamaProvider(
        "http://x:1",
        "llama3.1:8b",
        transport=transport(lambda r: httpx.Response(404, json={"error": "model not found"})),
    )
    with pytest.raises(LLMError) as err:
        await collect(llm)
    assert err.value.code == "model_missing"
    assert "ollama pull llama3.1:8b" in err.value.message


async def test_ollama_error_line_in_stream() -> None:
    llm = OllamaProvider(
        "http://x:1",
        "m",
        transport=transport(lambda r: httpx.Response(200, text='{"error": "out of memory"}\n')),
    )
    with pytest.raises(LLMError, match="out of memory"):
        await collect(llm)


async def test_ollama_unreachable_is_plain_and_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    llm = OllamaProvider("http://127.0.0.1:11434", "m", transport=transport(handler))
    with pytest.raises(LLMError) as err:
        await collect(llm)
    assert err.value.code == "unreachable" and err.value.retryable
    assert "Is it running?" in err.value.message


async def test_ollama_check_lists_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}, {"name": "qwen"}]})

    ok = await OllamaProvider("http://x:1", "llama3.1:8b", transport=transport(handler)).check()
    assert ok.ok and ok.models == ["llama3.1:8b", "qwen"]
    missing = await OllamaProvider("http://x:1", "mistral", transport=transport(handler)).check()
    assert not missing.ok and "ollama pull mistral" in missing.message


# ---------------------------------------------------------------- OpenAI-compatible


def sse(*events: dict[str, object] | str) -> str:
    out = []
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event)
        out.append(f"data: {data}\n\n")
    return "".join(out)


async def test_openai_compatible_streams_sse_with_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        body = sse(
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Thirty "}}]},
            {"choices": [{"delta": {"content": "days [1]."}}]},
            {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 5}},
            "[DONE]",
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    llm = OpenAICompatible(
        "http://127.0.0.1:1234/v1", "local", "sk-test", transport=transport(handler)
    )
    text, usage = await collect(llm)
    assert text == "Thirty days [1]."
    assert (usage.prompt_tokens, usage.completion_tokens) == (30, 5)
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict) and body["stream_options"] == {"include_usage": True}


async def test_openai_without_key_sends_no_auth_header() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "x"}}]}, "[DONE]"))

    await collect(OpenAICompatible("http://x:1/v1", "m", transport=transport(handler)))
    assert seen["auth"] is None


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "auth"), (404, "model_missing"), (429, "rate_limited"), (503, "server_error")],
)
async def test_openai_status_errors(status: int, code: str) -> None:
    llm = OpenAICompatible(
        "http://x:1/v1",
        "m",
        "bad",
        transport=transport(lambda r: httpx.Response(status, json={"error": {"message": "no"}})),
    )
    with pytest.raises(LLMError) as err:
        await collect(llm)
    assert err.value.code == code
    assert "bad" not in err.value.message  # the key never appears in an error


async def test_openai_check_reports_missing_model() -> None:
    handler = lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})  # noqa: E731
    result = await OpenAICompatible("http://x:1/v1", "c", transport=transport(handler)).check()
    assert not result.ok and "a, b" in result.message


# ---------------------------------------------------------------- Anthropic


async def test_anthropic_streams_events() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        events = [
            (
                "message_start",
                {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
            ),
            ("content_block_start", {"type": "content_block_start", "index": 0}),
            (
                "content_block_delta",
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Yes "}},
            ),
            ("ping", {"type": "ping"}),
            (
                "content_block_delta",
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "[2]."}},
            ),
            ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 4}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        text = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)
        return httpx.Response(200, text=text)

    llm = AnthropicProvider("key-123", "claude-test", transport=transport(handler))
    text, usage = await collect(llm)
    assert text == "Yes [2]."
    assert (usage.prompt_tokens, usage.completion_tokens) == (11, 4)
    headers = seen["headers"]
    assert isinstance(headers, dict) and headers["x-api-key"] == "key-123"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["system"] == "Be brief."  # system prompt goes in its own field
    assert body["messages"] == [{"role": "user", "content": "Hi"}]


async def test_anthropic_needs_a_key() -> None:
    with pytest.raises(LLMError) as err:
        await collect(AnthropicProvider("", "m"))
    assert err.value.code == "auth"


async def test_anthropic_error_event() -> None:
    body = 'event: error\ndata: {"type": "error", "error": {"message": "Overloaded"}}\n\n'
    llm = AnthropicProvider("k", "m", transport=transport(lambda r: httpx.Response(200, text=body)))
    with pytest.raises(LLMError, match="Overloaded"):
        await collect(llm)


# ---------------------------------------------------------------- shared pieces


async def test_sse_parser_handles_multiline_data_and_comments() -> None:
    raw = ": keep-alive\n\nevent: a\ndata: one\ndata: two\n\ndata: three\n\n"
    response = httpx.Response(200, text=raw)
    assert [x async for x in iter_sse(response)] == [("a", "one\ntwo"), ("message", "three")]


async def test_complete_collects_text_and_usage() -> None:
    result = await complete(FakeLLM(lambda m: "hello world"), MESSAGES, max_tokens=10)
    assert result.text == "hello world" and result.usage.completion_tokens >= 1


def test_make_llm_picks_provider_and_cloud_flag(tmp_path: object) -> None:
    base = {"library": "/tmp/lib", "data_dir": "/tmp/data", "docker": False}
    assert make_llm(Settings(**base, llm="ollama")).provider == "ollama"
    assert make_llm(Settings(**base, llm="fake")).provider == "fake"
    local = make_llm(Settings(**base, llm="openai", openai_url="http://localhost:1234/v1"))
    assert local.cloud is False
    cloud = make_llm(Settings(**base, llm="openai", openai_url="https://api.example.com/v1"))
    assert cloud.cloud is True
    assert make_llm(Settings(**base, llm="anthropic", anthropic_key="k")).cloud is True
