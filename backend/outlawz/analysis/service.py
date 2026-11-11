"""Data questions end to end::

    question → model returns a JSON plan from the fixed menu → validated against the real columns
             → fixed pandas routine on every row → validated chart spec → short summary

An invalid plan goes back to the model once, with the exact error. The model's summary must use
only computed numbers; if it doesn't, the computed summary is shown instead.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from outlawz.analysis.numbers import unmatched_numbers
from outlawz.analysis.plans import NoPlan, Plan, PlanError, menu_text, parse_plan
from outlawz.analysis.run import Result, run_plan
from outlawz.analysis.validate import validate_plan
from outlawz.errors import AppError
from outlawz.library.documents import Document
from outlawz.llm.base import LLM, LLMError, Message, complete
from outlawz.tabular import ColumnKind, fmt_number, load_csv, typed_frame

if TYPE_CHECKING:
    from outlawz.chat.chats import Chat, ChatMessage
    from outlawz.state import AppState

PLAN_PROMPT = """You turn a question about a table into a JSON plan for a fixed menu of analyses.
You never write code. Reply with ONE JSON object and nothing else.

Menu (the "op" field picks one):
{menu}

Use the exact column names listed below. If the question can't be answered with this menu, or \
isn't about this table's values, reply {{"op": "none"}}."""

RETRY_PROMPT = (
    "That plan can't run: {problems} Reply with a corrected JSON object only, or "
    '{{"op": "none"}} if the question doesn\'t fit the table.'
)

SUMMARY_PROMPT = """You write a short summary (one to three sentences) of a computed result, \
answering the user's question.
Use only numbers that appear in the result, copied exactly as shown. Don't invent or estimate \
numbers. No citations, no headings. Reply in the same language as the question."""

_DATA_WORDS = re.compile(
    r"\b(how many|how much|total|sum|average|avg|mean|median|count|number of|maximum|minimum|"
    r"max|min|highest|lowest|largest|smallest|biggest|top \d+|bottom \d+|rank|ranking|compare|"
    r"comparison|differ|difference|trend|over time|per (day|week|month|quarter|year)|monthly|"
    r"weekly|yearly|quarterly|distribution|correlat\w*|relationship|outliers?|unusual|missing|"
    r"empty|percent(age)?|share|proportion|ratio|growth|chart|plot|graph|histogram|std|"
    r"standard deviation|spread|breakdown|split|crosstab|statistic\w*|"
    r"summar\w+ of the (table|data))\b|%",
    re.IGNORECASE,
)


def looks_like_data_question(question: str, columns: list[str]) -> bool:
    if _DATA_WORDS.search(question):
        return True
    lowered = question.lower()
    mentions = any(c.lower() in lowered for c in columns if len(c) > 2)
    return mentions and bool(re.search(r"\d", question))


def describe_columns(frame: pd.DataFrame, kinds: dict[str, ColumnKind]) -> str:
    lines = []
    for name in frame.columns:
        kind = kinds[name]
        values = frame[name].dropna()
        if kind == "number" and len(values):
            hint = f"{fmt_number(values.min())} to {fmt_number(values.max())}"
        elif kind == "date" and len(values):
            hint = f"{values.min().date()} to {values.max().date()}"
        elif len(values):
            top = values.astype(str).value_counts().head(5).index.tolist()
            hint = "e.g. " + ", ".join(t[:40] for t in top)
        else:
            hint = "empty"
        lines.append(f"- {name} ({kind}): {hint}")
    return "\n".join(lines)


def plan_messages(
    name: str, frame: pd.DataFrame, kinds: dict[str, ColumnKind], question: str
) -> list[Message]:
    table = f"TABLE {name}: {len(frame):,} rows.\nCOLUMNS:\n{describe_columns(frame, kinds)}"
    return [
        Message("system", PLAN_PROMPT.format(menu=menu_text())),
        Message("user", f"{table}\n\nQUESTION: {question.strip()}"),
    ]


def result_text(result: Result, max_rows: int = 30) -> str:
    header = " | ".join(c["name"] for c in result.columns)
    lines = [result.title, header]
    for row in result.rows[:max_rows]:
        lines.append(" | ".join(_cell(v) for v in row))
    if len(result.rows) > max_rows:
        lines.append(f"(+ {len(result.rows) - max_rows} more rows)")
    if result.stats:
        lines.append(
            "Statistics: " + ", ".join(f"{k} = {_cell(v)}" for k, v in result.stats.items())
        )
    lines.append(f"Rows used: {result.rows_used:,} of {result.rows_total:,}")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if isinstance(value, float | int) and not isinstance(value, bool):
        return fmt_number(value)
    return "" if value is None else str(value)


def summary_messages(question: str, result: Result) -> list[Message]:
    body = (
        f"QUESTION: {question.strip()}\n\nRESULT:\n{result_text(result)}\n\n"
        f"COMPUTED SUMMARY: {result.summary}"
    )
    return [Message("system", SUMMARY_PROMPT), Message("user", body)]


@dataclass
class Answer:
    plan: Plan
    result: Result
    summary: str
    summary_source: str  # "model" or "computed"
    rejected_numbers: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    attempts: int = 1
    timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.model_dump(mode="json"),
            "result": self.result.to_dict(),
            "summary": self.summary,
            "summary_source": self.summary_source,
            "rejected_numbers": self.rejected_numbers,
            "fixes": self.fixes,
            "attempts": self.attempts,
            "timings": self.timings,
        }


class FrameCache:
    """The last few tables read, keyed by document id and content hash."""

    def __init__(self, size: int = 3) -> None:
        self.size = size
        self._items: OrderedDict[tuple[str, str], tuple[pd.DataFrame, dict[str, ColumnKind]]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def get(self, doc: Document, path: Path) -> tuple[pd.DataFrame, dict[str, ColumnKind]]:
        key = (doc.id, doc.sha256)
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        raw, _ = load_csv(path)
        frame, kinds = typed_frame(raw)
        with self._lock:
            self._items[key] = (frame, kinds)
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)
        return frame, kinds

    def forget(self, doc_id: str) -> None:
        with self._lock:
            for key in [k for k in self._items if k[0] == doc_id]:
                del self._items[key]


class DataAnalyst:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.frames = FrameCache()

    # ------------------------------------------------------------ tables

    def csv_document(self, doc_id: str) -> Document:
        doc = self.state.require_document(doc_id)
        if doc.type != "csv":
            raise AppError(
                "not_a_table", f"“{doc.name}” isn't a CSV file, so it can't be analysed.", 422
            )
        return doc

    def load(self, doc: Document) -> tuple[pd.DataFrame, dict[str, ColumnKind]]:
        path = self.state.document_file(doc)
        if not path.is_file():
            raise AppError("not_found", "The file is no longer in the library folder.", 404)
        try:
            return self.frames.get(doc, path)
        except Exception as exc:
            message = getattr(exc, "message", None) or "This CSV file can't be read."
            raise AppError("unreadable", str(message), 422) from None

    async def load_async(self, doc: Document) -> tuple[pd.DataFrame, dict[str, ColumnKind]]:
        return await asyncio.to_thread(self.load, doc)

    def run(self, doc: Document, plan: Plan) -> tuple[Result, list[str]]:
        frame, kinds = self.load(doc)
        checked, fixes = validate_plan(plan, kinds)
        result = run_plan(checked, frame, kinds, name=doc.name)
        if fixes:
            result.notes = [*fixes, *result.notes]
        return result, fixes

    # ------------------------------------------------------------ the model

    async def ask(self, doc: Document, question: str, llm: LLM | None = None) -> Answer | None:
        """Answer a question about one CSV with computed results. ``None`` means the question
        doesn't fit the table (the caller falls back to cited passages)."""
        frame, kinds = await self.load_async(doc)
        answer = await self.ask_frame(doc.name, frame, kinds, question, llm)
        hook = getattr(self.state, "on_analysis", None)
        if answer is not None and hook is not None:
            hook(answer)
        return answer

    async def ask_frame(
        self,
        name: str,
        frame: pd.DataFrame,
        kinds: dict[str, ColumnKind],
        question: str,
        llm: LLM | None = None,
    ) -> Answer | None:
        """The same flow for a table that isn't a library document (the evaluation benchmark)."""
        llm = llm or self.state.llm()
        started = time.perf_counter()
        messages = plan_messages(name, frame, kinds, question)
        attempts = 0
        plan: Plan | None = None
        fixes: list[str] = []
        timings: dict[str, float] = {}
        for attempt in range(2):
            attempts = attempt + 1
            t = time.perf_counter()
            reply = await complete(llm, messages, max_tokens=400, temperature=0.0)
            timings[f"plan_{attempts}_ms"] = _ms(t)
            try:
                plan, fixes = validate_plan(parse_plan(reply.text), kinds)
                break
            except NoPlan:
                return None
            except PlanError as err:
                if attempt == 1:
                    return None
                messages = [
                    *messages,
                    Message("assistant", reply.text[:2000]),
                    Message("user", RETRY_PROMPT.format(problems=" ".join(err.problems))),
                ]
        assert plan is not None
        t = time.perf_counter()
        try:
            result = await asyncio.to_thread(run_plan, plan, frame, kinds, name=name)
        except PlanError:
            return None
        timings["compute_ms"] = _ms(t)
        if fixes:
            result.notes = [*fixes, *result.notes]

        t = time.perf_counter()
        summary, source, rejected = await self.summarise(llm, question, result)
        timings["summary_ms"] = _ms(t)
        timings["total_ms"] = _ms(started)
        return Answer(plan, result, summary, source, rejected, fixes, attempts, timings)

    async def summarise(
        self, llm: LLM, question: str, result: Result
    ) -> tuple[str, str, list[str]]:
        try:
            reply = await complete(
                llm, summary_messages(question, result), max_tokens=220, temperature=0.0
            )
        except LLMError:
            return result.summary, "computed", []
        text = re.sub(r"\s*\[\d+\]", "", reply.text).strip()
        if not text or len(text) > 1500:
            return result.summary, "computed", []
        missing = unmatched_numbers(text, result.numbers())
        if missing:
            return result.summary, "computed", missing
        return text, "model", []

    # ------------------------------------------------------------ chat

    def pick_document(self, chat: Chat, question: str) -> Document | None:
        """The CSV a chat question is about, if any."""
        docs = self.state.docs
        if chat.documents is not None:
            candidates = [d for d in (docs.get(i) for i in chat.documents) if d and d.type == "csv"]
            explicit = True
        else:
            candidates = [d for d in docs.all() if d.type == "csv" and d.status.value == "ready"]
            explicit = False
        if not candidates:
            return None
        lowered = question.lower()

        def score(doc: Document) -> int:
            stem = Path(doc.name).stem.lower().replace("_", " ")
            points = (
                3
                if stem and (stem in lowered.replace("_", " ") or doc.name.lower() in lowered)
                else 0
            )
            try:
                frame, _ = self.load(doc)
            except AppError:
                return -1
            points += sum(1 for c in frame.columns if len(c) > 2 and c.lower() in lowered)
            return points

        scored = sorted(((score(d), d) for d in candidates), key=lambda x: -x[0])
        best_score, best = scored[0]
        if best_score < 0:
            return None
        if not explicit and best_score == 0:
            return None  # all documents searched and the question doesn't name this table
        return best

    async def chat_hook(self, chat: Chat, question: str, standalone: str, msg: ChatMessage) -> bool:
        doc = await asyncio.to_thread(self.pick_document, chat, standalone)
        if doc is None:
            return False
        frame, _ = await self.load_async(doc)
        if not looks_like_data_question(standalone, list(frame.columns)):
            return False
        try:
            answer = await self.ask(doc, standalone)
        except (AppError, LLMError):
            return False
        if answer is None:
            return False
        msg.content = answer.summary
        msg.computed = {"doc_id": doc.id, "document": doc.name, **answer.to_dict()}
        msg.flags = ["computed"] + (["summary_replaced"] if answer.rejected_numbers else [])
        msg.timings.update({k: v for k, v in answer.timings.items() if k.endswith("_ms")})
        return True


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
