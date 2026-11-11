"""Wires the app's hooks and events to metrics, traces and the activity log.

Everything recorded here is a number, an id or a short code. Nothing a user typed or a document
contains is passed in: the hooks receive timings and ids, and the activity log keeps ids only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from outlawz.middleware import request_id_var
from outlawz.obs.activity import ActivityLog
from outlawz.obs.metrics import Metrics
from outlawz.obs.traces import Tracer, spans_in_order

if TYPE_CHECKING:
    from outlawz.analysis.service import Answer
    from outlawz.chat.chats import ChatMessage
    from outlawz.search.service import SearchResult
    from outlawz.state import AppState

log = logging.getLogger("outlawz")

FLUSH_SECONDS = 2.0
INGEST_STEPS = ("reuse", "extract", "chunk", "embed", "write")


def _request_id() -> str | None:
    value = request_id_var.get()
    return value or None


class Observability:
    def __init__(self, state: AppState) -> None:
        self.state = state
        folder = state.lib.logs_dir
        self.metrics = Metrics(folder)
        self.tracer = Tracer(
            folder,
            sample=lambda: state.settings.trace_sample,
            slow_ms=lambda: state.settings.slow_trace_ms,
        )
        self.activity = ActivityLog(folder)

    # ------------------------------------------------------------ wiring

    def attach(self) -> None:
        state = self.state
        state.on_request = self.metrics.request
        state.on_search = self.search
        state.on_answer = self.answer
        state.on_analysis = self.analysis
        state.on_analysis_step = lambda kind, ms: self.metrics.step(f"analysis.{kind}", ms)
        if state.queue is not None:
            state.queue.on_step = self.ingest
        on = state.events.on
        on(
            "document_added",
            lambda doc, source="upload", **_: self.record(
                "document_added", doc_id=doc.id, type=doc.type, source=source
            ),
        )
        on("document_removed", self._removed)
        on("document_reread", lambda doc_id, **_: self.record("document_reread", doc_id=doc_id))
        on(
            "library_rebuilt",
            lambda count=None, **_: self.record("library_rebuilt", documents=count),
        )
        on("samples_added", lambda count=0, **_: self.record("samples_added", documents=count))
        on("chat_deleted", lambda chat_id, **_: self.record("chat_deleted", chat_id=chat_id))
        on(
            "preferences_changed",
            lambda action, item_id=None, count=None, **_: self.record(
                "preferences_changed", change=action, item_id=item_id, count=count
            ),
        )
        on("settings_changed", lambda keys, **_: self.record("settings_changed", keys=list(keys)))
        for action in ("backup_created", "backup_verified", "backup_restored", "export_created"):
            on(action, lambda _action=action, **ids: self.record(_action, **ids))

    def _removed(self, doc: Any, reason: str = "file_gone", **_: Any) -> None:
        action = "document_deleted" if reason == "deleted" else "document_removed"
        self.record(action, doc_id=doc.id, type=doc.type)

    def record(self, action: str, **ids: Any) -> None:
        try:
            self.activity.record(action, **ids)
        except OSError:
            log.warning("The activity log couldn't be written.")

    # ------------------------------------------------------------ hooks

    def search(self, result: SearchResult) -> None:
        t = result.timings
        total = float(t.get("total_ms", 0))
        self.metrics.step(
            "search.total", total, hits=len(result.hits), passages=result.searched_passages
        )
        for key in ("embed", "vector", "keyword", "rerank"):
            if f"{key}_ms" in t:
                self.metrics.step(f"search.{key}", float(t[f"{key}_ms"]))
        spans = spans_in_order(
            [
                ("embed question", t.get("embed_ms")),
                ("meaning search", t.get("vector_ms")),
                ("keyword search", t.get("keyword_ms")),
                ("fusion", t.get("fusion_ms")),
                ("rerank", t.get("rerank_ms")),
            ]
        )
        self.tracer.record(
            "search",
            total,
            spans,
            trace_id=_request_id(),
            counts={"hits": len(result.hits), "passages": result.searched_passages},
        )

    def answer(self, msg: ChatMessage) -> None:
        t = msg.timings
        total = float(t.get("total_ms", 0))
        self.metrics.step("chat.answer", total, status=msg.status)
        for key in ("rewrite", "retrieval", "first_word"):
            if f"{key}_ms" in t:
                self.metrics.step(f"chat.{key}", float(t[f"{key}_ms"]))
        if msg.usage:
            self.metrics.step(
                "chat.tokens",
                0.0,
                prompt_tokens=msg.usage.get("prompt_tokens", 0),
                completion_tokens=msg.usage.get("completion_tokens", 0),
            )
        rewrite = t.get("rewrite_ms") or 0.0
        if msg.computed:
            spans = spans_in_order(
                [
                    ("rewrite question", t.get("rewrite_ms")),
                    ("plan", sum(v for k, v in t.items() if k.startswith("plan_"))),
                    ("compute", t.get("compute_ms")),
                    ("summary", t.get("summary_ms")),
                ]
            )
        else:
            before_model = rewrite + (t.get("retrieval_ms") or 0.0)
            first = t.get("first_word_ms")
            spans = spans_in_order(
                [
                    ("rewrite question", t.get("rewrite_ms")),
                    ("retrieval", t.get("retrieval_ms")),
                    ("model: first word", (first - before_model) if first is not None else None),
                    ("model: rest of answer", (total - first) if first is not None else None),
                ]
            )
        status = {"complete": "ok", "streaming": "ok"}.get(msg.status, msg.status)
        self.tracer.record(
            "answer",
            total,
            spans,
            trace_id=_request_id(),
            status=status,
            code=(msg.error or {}).get("code"),
            ids={"message_id": msg.id},
            counts={
                "sources": len(msg.sources),
                "cited": len(msg.cited),
                **(msg.usage or {}),
            },
        )

    def analysis(self, answer: Answer) -> None:
        t = answer.timings
        total = float(t.get("total_ms", 0))
        self.metrics.step("analysis.ask", total, op=answer.plan.op, attempts=answer.attempts)
        plan_ms = sum(v for k, v in t.items() if k.startswith("plan_"))
        self.metrics.step("analysis.plan", plan_ms)
        for key in ("compute", "summary"):
            if f"{key}_ms" in t:
                self.metrics.step(f"analysis.{key}", float(t[f"{key}_ms"]))
        spans = spans_in_order(
            [(f"plan (try {k[5]})", v) for k, v in sorted(t.items()) if k.startswith("plan_")]
            + [("compute", t.get("compute_ms")), ("summary", t.get("summary_ms"))]
        )
        self.tracer.record(
            "analysis",
            total,
            spans,
            trace_id=_request_id(),
            counts={"rows": answer.result.rows_used, "attempts": answer.attempts},
        )

    def ingest(self, name: str, ms: float, meta: dict[str, Any]) -> None:
        steps: dict[str, float] = meta.get("steps") or {}
        outcome = str(meta.get("outcome", "ready"))
        doc_id = str(meta.get("doc_id", ""))
        self.metrics.step("ingest.document", ms, outcome=outcome)
        for step in INGEST_STEPS:
            if step in steps:
                self.metrics.step(f"ingest.{step}", steps[step])
        doc = self.state.documents.get(doc_id) if self.state.documents else None
        # Reading a document is rare and worth seeing in full, so these traces are always kept.
        self.tracer.record(
            "ingest",
            ms,
            spans_in_order([(s, steps.get(s)) for s in INGEST_STEPS]),
            status="ok" if outcome == "ready" else outcome,
            code=doc.error.code if doc is not None and doc.error else None,
            ids={"doc_id": doc_id},
            counts={"passages": doc.chunks, "pages": doc.pages or 0} if doc is not None else None,
            always=True,
        )

    # ------------------------------------------------------------ housekeeping

    def flush(self) -> None:
        with contextlib.suppress(OSError):
            self.metrics.flush()
        with contextlib.suppress(OSError):
            self.tracer.flush()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            await asyncio.to_thread(self.flush)

    def prune(self, log_days: int, activity_days: int) -> int:
        removed = len(self.metrics.log.prune(log_days)) + len(self.tracer.log.prune(log_days))
        return removed + self.activity.prune(activity_days)
