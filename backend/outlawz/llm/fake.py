"""A deterministic fake model for tests and offline demos.

By default it behaves like a well-behaved grounded model:

* asked to rewrite a follow-up, it joins the previous question and the follow-up;
* asked to answer from numbered sources, it repeats the first sentence of source [1] and cites it;
* with no sources it says the documents don't cover the question.

Tests can pass ``responder`` to script exact replies.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable

from outlawz.llm.base import CheckResult, Event, LLMError, Message, TextDelta, Usage

Responder = Callable[[list[Message]], str]

_SOURCE = re.compile(r"^\[(\d+)\][^\n]*\n(.+?)(?=\n\n\[\d+\]|\n\nQUESTION|\Z)", re.S | re.M)


def default_responder(messages: list[Message]) -> str:
    system = messages[0].content if messages and messages[0].role == "system" else ""
    last = messages[-1].content if messages else ""
    if "standalone search query" in system:
        history = [m.content for m in messages[1:-1] if m.role == "user"]
        follow_up = last.split("FOLLOW-UP:", 1)[-1].strip()
        previous = re.findall(r"^User: (.+)$", last, re.M)
        if previous:
            return f"{previous[-1].strip()} {follow_up}".strip()
        return (" ".join([*history[-1:], follow_up])).strip()
    if "JSON plan" in system:
        return '{"op": "summary"}'
    if "short summary" in system:
        found = re.search(r"^COMPUTED SUMMARY: (.+)$", last, re.M)
        return found.group(1) if found else "Here is the computed result."
    sources = _SOURCE.findall(last)
    if not sources:
        return "Your documents don't cover this. Try other words, or add a document about it."
    number, text = sources[0]
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return f"{sentence} [{number}]"


class FakeLLM:
    provider = "fake"
    cloud = False

    def __init__(
        self,
        responder: Responder | None = None,
        *,
        model: str = "fake-model",
        chunk: int = 12,
        delay: float = 0.0,
        error: LLMError | None = None,
    ) -> None:
        self.responder = responder or default_responder
        self.model = model
        self.chunk = chunk
        self.delay = delay
        self.error = error
        self.calls: list[list[Message]] = []

    async def stream(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Event]:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        text = self.responder(messages)
        for start in range(0, len(text), self.chunk):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield TextDelta(text[start : start + self.chunk])
        prompt = sum(len(m.content) for m in messages)
        yield Usage(prompt_tokens=prompt // 4, completion_tokens=max(1, len(text) // 4))

    async def check(self) -> CheckResult:
        return CheckResult(True, "The test model is ready.", [self.model])
