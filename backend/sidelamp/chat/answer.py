"""Retrieval-augmented answering::

    question → follow-up rewrite → hybrid retrieval (top 8) → numbered sources
             → model (streamed) → citation check → saved

Events, in order: ``start``, ``sources``, ``delta`` (many), then ``done`` or ``error``. If the
browser disconnects mid-answer, :meth:`AnswerRun.interrupt` saves what arrived so far with the
status ``interrupted``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sidelamp.chat.chats import ChatMessage, SavedSource
from sidelamp.chat.citations import check_citations
from sidelamp.chat.preferences import detect_suggestion
from sidelamp.chat.prompts import NO_SOURCES_ANSWER, Source, answer_messages, fit_sources
from sidelamp.chat.rewrite import needs_rewrite, rewrite_follow_up
from sidelamp.llm.base import LLMError, TextDelta, Usage

if TYPE_CHECKING:
    from sidelamp.chat.chats import Chat
    from sidelamp.state import AppState

# (chat, question, standalone question) -> a finished assistant message, or None to fall back
DataHook = Callable[["Chat", str, str, ChatMessage], Awaitable[bool]]


@dataclass
class AskEvent:
    name: str
    data: dict[str, Any]


@dataclass
class AnswerRun:
    state: AppState
    chat_id: str
    question: str
    assistant: ChatMessage = field(
        default_factory=lambda: ChatMessage(role="assistant", status="streaming")
    )
    parts: list[str] = field(default_factory=list)
    finished: bool = False
    started: float = field(default_factory=time.perf_counter)

    def _elapsed(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def _save(self) -> None:
        self.state.chats.replace_message(self.chat_id, self.assistant)
        self.finished = True

    def interrupt(self) -> None:
        """Called when the browser goes away before the answer is complete."""
        if self.finished:
            return
        self.assistant.status = "interrupted"
        partial = "".join(self.parts)
        valid = {s.n for s in self.assistant.sources}
        check = check_citations(partial, valid)
        self.assistant.content = check.text
        self.assistant.cited = check.cited
        self.assistant.flags = [*check.flags, "interrupted"]
        self.assistant.timings["total_ms"] = self._elapsed()
        self._save()
        hook = self.state.on_answer
        if hook is not None:
            hook(self.assistant)

    async def events(self) -> AsyncIterator[AskEvent]:
        try:
            async for event in self._run():
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # The browser went away (or the app is stopping): keep what arrived so far.
            self.interrupt()
            raise
        except Exception as exc:
            if self.finished:
                raise
            # Type only: the message could contain document text.
            import logging

            logging.getLogger("sidelamp").error("Answering failed: %s", type(exc).__name__)
            msg = self.assistant
            msg.status = "error"
            msg.error = {
                "code": "internal_error",
                "message": "Something went wrong while answering. Try again.",
            }
            msg.content = "".join(self.parts)
            msg.timings["total_ms"] = self._elapsed()
            self._save()
            if self.state.on_answer is not None:
                self.state.on_answer(msg)
            yield AskEvent("error", {**msg.error, "message_id": msg.id})

    async def _run(self) -> AsyncIterator[AskEvent]:
        state = self.state
        settings = state.settings
        chat = state.chats.get(self.chat_id)
        history = [
            (m.role, m.content) for m in chat.messages if m.status == "complete" and m.content
        ][-settings.history_messages :]
        user = ChatMessage(role="user", content=self.question)
        state.chats.append(self.chat_id, user)
        msg = self.assistant
        yield AskEvent(
            "start", {"chat_id": self.chat_id, "message_id": msg.id, "user_message_id": user.id}
        )

        suggestion = detect_suggestion(self.question)
        if suggestion is not None:
            saved = state.preferences.suggest(suggestion, self.chat_id)
            msg.content = (
                f"Do you want me to remember this? “{saved.text}” It's only used "
                f"after you choose Save."
            )
            msg.suggestion = saved.model_dump(mode="json")
            yield self._done()
            return

        standalone = self.question
        if settings.history_messages and needs_rewrite(history, self.question):
            t = time.perf_counter()
            standalone = await rewrite_follow_up(state.llm(), history, self.question)
            msg.timings["rewrite_ms"] = round((time.perf_counter() - t) * 1000, 1)
        msg.query = standalone

        hook = state.data_hook
        if hook is not None and await hook(chat, self.question, standalone, msg):
            yield AskEvent("sources", {"sources": [], "computed": True})
            yield self._done()
            return

        t = time.perf_counter()
        result = await asyncio.to_thread(
            state.search.search,
            standalone,
            mode="best",
            limit=settings.top_k,
            doc_ids=chat.documents,
        )
        msg.timings["retrieval_ms"] = round((time.perf_counter() - t) * 1000, 1)
        sources: list[Source] = []
        for hit in result.hits:
            doc = state.docs.get(hit.doc_id)
            if doc is not None:
                sources.append(Source(len(sources) + 1, hit.doc_id, doc.name, hit.passage))
        sources = fit_sources(sources, settings.context_tokens)
        msg.sources = [_saved(s) for s in sources]
        yield AskEvent(
            "sources",
            {
                "sources": [s.model_dump(mode="json") for s in msg.sources],
                "query": standalone,
                "notes": result.notes,
            },
        )

        if not sources:
            msg.content = NO_SOURCES_ANSWER
            msg.flags = ["no_sources"]
            yield self._done()
            return

        preferences = state.preferences.active() if settings.preferences else []
        messages = answer_messages(
            self.question, sources, standalone=standalone, preferences=preferences
        )
        usage = Usage()
        try:
            llm = state.llm()
            async for event in llm.stream(
                messages, max_tokens=settings.max_answer_tokens, temperature=settings.temperature
            ):
                if isinstance(event, TextDelta):
                    if not self.parts:
                        msg.timings["first_word_ms"] = self._elapsed()
                    self.parts.append(event.text)
                    yield AskEvent("delta", {"text": event.text})
                else:
                    usage = event
        except LLMError as exc:
            msg.status = "error"
            msg.error = {"code": exc.code, "message": exc.message}
            msg.content = "".join(self.parts)
            msg.timings["total_ms"] = self._elapsed()
            self._save()
            if state.on_answer is not None:
                state.on_answer(msg)
            yield AskEvent(
                "error", {"code": exc.code, "message": exc.message, "message_id": msg.id}
            )
            return

        check = check_citations("".join(self.parts), {s.n for s in sources})
        msg.content = check.text
        msg.cited = check.cited
        msg.flags = check.flags
        msg.usage = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        }
        yield self._done()

    def _done(self) -> AskEvent:
        self.assistant.status = "complete"
        self.assistant.timings["total_ms"] = self._elapsed()
        self._save()
        if self.state.on_answer is not None:
            self.state.on_answer(self.assistant)
        return AskEvent("done", {"message": self.assistant.model_dump(mode="json")})


def _saved(source: Source) -> SavedSource:
    p = source.passage
    rows = p.get("rows")
    return SavedSource(
        n=source.n,
        doc_id=source.doc_id,
        document=source.document,
        i=int(p.get("i", 0)),
        label=source.label,
        text=str(p.get("text", "")),
        page=p.get("page"),
        page_end=p.get("page_end"),
        section=p.get("section"),
        chapter=p.get("chapter"),
        rows=list(rows) if rows else None,
        kind=str(p.get("kind", "text")),
    )
