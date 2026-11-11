"""Runs the evaluation suites and keeps the results.

The benchmark is processed into its own hidden area (``.outlawz/evals/bench-v1``), never shown
in the library, with the same reading and search settings as the library. Each run is saved
(``.outlawz/evals/runs/<id>.json``) with the settings it used, and compared with the previous
run: every number gets ▲ or ▼ and whether that's better or worse.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from outlawz.evals import suites
from outlawz.evals.benchmark import BENCH_VERSION, CSV_NAME, QUESTIONS, write_files
from outlawz.ingest.pipeline import PipelineContext, process, read_passages, read_vectors
from outlawz.library.documents import Document, DocumentIndex, doc_type_for
from outlawz.library.uploads import sha256_file
from outlawz.paths import LibraryPaths
from outlawz.search.index import SearchIndex
from outlawz.search.service import SearchService
from outlawz.store.files import read_json, write_json
from outlawz.tabular import load_csv, typed_frame

if TYPE_CHECKING:
    from outlawz.state import AppState

SUITES = ("chunking", "retrieval", "answers", "data")
LOWER_IS_BETTER = ("latency", "duplicates", "wrong_refusals", "made_up_numbers", "model_errors")
Progress = Callable[[str], None]


def flatten(data: Any, prefix: str = "") -> dict[str, float]:
    """``{"retrieval": {"hybrid": {"mrr": 0.8}}}`` → ``{"retrieval.hybrid.mrr": 0.8}``."""
    out: dict[str, float] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            out.update(flatten(value, f"{prefix}{key}."))
    elif isinstance(data, int | float) and not isinstance(data, bool):
        out[prefix.rstrip(".")] = float(data)
    return out


def compare(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    if previous is None:
        return []
    now, before = flatten(current), flatten(previous)
    changes = []
    for key, value in now.items():
        if key not in before or value == before[key]:
            continue
        old = before[key]
        lower_better = any(word in key for word in LOWER_IS_BETTER)
        up = value > old
        changes.append(
            {
                "metric": key,
                "before": old,
                "after": value,
                "change": round(value - old, 4),
                "arrow": "▲" if up else "▼",
                "better": up != lower_better,
            }
        )
    return changes


def settings_snapshot(state: AppState) -> dict[str, Any]:
    s = state.settings
    keys = (
        "embedder",
        "embed_model",
        "reranker",
        "rerank_model",
        "llm",
        "llm_model",
        "chunk_tokens",
        "chunk_overlap",
        "chunk_min",
        "vector_k",
        "keyword_k",
        "rerank_k",
        "top_k",
        "rrf_k",
        "vector_weight",
        "keyword_weight",
        "context_tokens",
        "temperature",
    )
    return {k: getattr(s, k) for k in keys}


class EvalRunner:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.active: dict[str, dict[str, Any]] = {}

    @property
    def root(self) -> Path:
        return self.state.lib.evals_dir

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def bench(self) -> LibraryPaths:
        return LibraryPaths(self.root / f"bench-v{BENCH_VERSION}")

    # ------------------------------------------------------------ preparation

    def prepare(self, progress: Progress) -> tuple[DocumentIndex, SearchService, dict[str, str]]:
        """Write and process the benchmark files (reusing earlier output when nothing changed)."""
        bench = self.bench
        bench.ensure()
        paths = write_files(bench.root)
        docs = DocumentIndex(bench.documents_file)
        for path in paths:
            doc_id = "bench-" + path.stem.replace("_", "-")
            sha = sha256_file(path)
            existing = docs.get(doc_id)
            if existing is None or existing.sha256 != sha:
                stat = path.stat()
                docs.add(
                    Document(
                        id=doc_id,
                        path=path.name,
                        sha256=sha,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        type=doc_type_for(path.name) or "pdf",
                    )
                )
        docs.save()
        state = self.state
        base = state.pipeline_context()
        ctx = PipelineContext(
            lib=bench,
            docs=docs,
            embedder=base.embedder,
            chunking=base.chunking,
            options=base.options,
        )
        index = SearchIndex()
        index.model_id = base.embedder.model_id
        for doc in docs.all():
            progress(f"Preparing {doc.name}")
            process(doc.id, ctx)
            folder = bench.doc_dir(doc.id)
            passages, vectors = read_passages(folder), read_vectors(folder)
            if vectors is not None:
                index.add(doc.id, passages, vectors, base.embedder.model_id)
        search = SearchService(index, lambda: base.embedder, state.reranker, state.search_options)
        names = {d.id: d.name for d in docs.all()}
        return docs, search, names

    # ------------------------------------------------------------ running

    def start(self, chosen: list[str] | None = None) -> str:
        """Start a run in the background (API). Returns its id."""
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        record = {"id": run_id, "status": "running", "started": time.time(), "progress": "Starting"}
        self.active[run_id] = record
        self.state.spawn(self._background(run_id, chosen), f"eval:{run_id}")
        return run_id

    async def _background(self, run_id: str, chosen: list[str] | None) -> None:
        record = self.active[run_id]
        try:
            await self.run(
                chosen, run_id=run_id, progress=lambda m: record.__setitem__("progress", m)
            )
        except Exception as exc:  # the run is reported as failed; nothing else depends on it
            record.update(status="failed", error=type(exc).__name__, finished=time.time())
            write_json(self.runs_dir / f"{run_id}.json", record)
        finally:
            self.active.pop(run_id, None)

    async def run(
        self,
        chosen: list[str] | None = None,
        *,
        run_id: str | None = None,
        progress: Progress | None = None,
        save: bool = True,
    ) -> dict[str, Any]:
        say = progress or (lambda message: None)
        run_suites = [s for s in SUITES if chosen is None or s in chosen]
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        started = time.time()
        docs, search, names = await asyncio.to_thread(self.prepare, say)
        text_docs = [d.id for d in docs.all() if d.type != "csv"]
        s = self.state.settings
        results: dict[str, Any] = {}
        details: list[dict[str, Any]] = []

        if "chunking" in run_suites:
            say("Checking passages")
            passages = [p for d in text_docs for p in read_passages(self.bench.doc_dir(d))]
            results["chunking"] = suites.chunking_suite(
                passages, target=s.chunk_tokens, minimum=s.chunk_min, overlap=s.chunk_overlap
            )

        if "retrieval" in run_suites:
            say("Comparing search methods")
            counts = {}
            all_passages = [p for d in text_docs for p in read_passages(self.bench.doc_dir(d))]
            for q in QUESTIONS:
                counts[q.id] = sum(1 for p in all_passages if suites.relevant(str(p["text"]), q))
            results["retrieval"], more = await asyncio.to_thread(
                suites.retrieval_suite, search, text_docs, lambda q: counts.get(q.id, 0)
            )
            details += more

        if "answers" in run_suites:
            say("Asking the model")
            results["answers"], more = await suites.answers_suite(
                search,
                self.state.llm(),
                text_docs,
                top_k=s.top_k,
                context_tokens=s.context_tokens,
                max_tokens=s.max_answer_tokens,
                temperature=s.temperature,
                document_name=lambda i: names.get(i, i),
            )
            details += more

        if "data" in run_suites:
            say("Asking data questions")
            raw, _ = await asyncio.to_thread(load_csv, self.bench.root / CSV_NAME)
            frame, kinds = typed_frame(raw)
            analyst = self.state.analyst

            async def ask(question: str) -> Any:
                return await analyst.ask_frame(CSV_NAME, frame, kinds, question)

            results["data"], more = await suites.data_suite(ask)
            details += more

        previous = self.latest()
        record = {
            "id": run_id,
            "status": "done",
            "started": started,
            "finished": time.time(),
            "seconds": round(time.time() - started, 2),
            "suites": run_suites,
            "settings": settings_snapshot(self.state),
            "benchmark": {"version": BENCH_VERSION, "questions": len(QUESTIONS)},
            "results": results,
            "compare_to": previous["id"] if previous else None,
            "changes": compare(results, previous.get("results") if previous else None),
            "details": details,
        }
        if save:
            write_json(self.runs_dir / f"{run_id}.json", record)
        return record

    # ------------------------------------------------------------ saved runs

    def runs(self) -> list[dict[str, Any]]:
        items = []
        if self.runs_dir.exists():
            for path in self.runs_dir.glob("*.json"):
                data = read_json(path, None)
                if isinstance(data, dict):
                    items.append(data)
        items.sort(key=lambda r: -float(r.get("started", 0)))
        return items

    def latest(self) -> dict[str, Any] | None:
        for run in self.runs():
            if run.get("status") == "done":
                return run
        return None

    def get(self, run_id: str) -> dict[str, Any] | None:
        if run_id in self.active:
            return dict(self.active[run_id])
        if not run_id.replace("-", "").isalnum():
            return None
        data = read_json(self.runs_dir / f"{run_id}.json", None)
        return data if isinstance(data, dict) else None


def report(record: dict[str, Any]) -> str:
    """A plain-text report for the command line."""
    lines = [f"Evaluation {record['id']} ({record.get('seconds', 0)} s)"]
    results = record.get("results", {})
    for suite, data in results.items():
        lines.append("")
        lines.append(suite.upper())
        if suite == "retrieval":
            columns = ["hit@1", "hit@3", "hit@5", "hit@10", "mrr", "ndcg@10", "recall@10"]
            lines.append(
                "  " + "method".ljust(15) + "".join(c.rjust(10) for c in [*columns, "p50 ms"])
            )
            for name, m in data.items():
                cells = [_f(m[c]) for c in columns] + [_f(m["latency_ms"]["p50"])]
                lines.append("  " + name.ljust(15) + "".join(c.rjust(10) for c in cells))
        else:
            for key, value in flatten(data).items():
                lines.append(f"  {key:<32}{_f(value):>10}")
    changes = record.get("changes") or []
    if changes:
        lines.append("")
        lines.append(f"CHANGES since {record.get('compare_to')}")
        for c in changes:
            word = "better" if c["better"] else "worse"
            lines.append(
                f"  {c['arrow']} {c['metric']}: {_f(c['before'])} → {_f(c['after'])} ({word})"
            )
    return "\n".join(lines)


def _f(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 100 else f"{value:.0f}"
    return str(value)


def check_thresholds(
    record: dict[str, Any], minimums: dict[str, float], maximums: dict[str, float]
) -> list[str]:
    flat = flatten(record.get("results", {}))
    failures = []
    for key, limit in minimums.items():
        value = flat.get(key)
        if value is None or value < limit:
            failures.append(f"{key} is {_f(value)}, below the minimum {limit}")
    for key, limit in maximums.items():
        value = flat.get(key)
        if value is None or value > limit:
            failures.append(f"{key} is {_f(value)}, above the maximum {limit}")
    return failures
