"""The evaluation suites. Every check is deterministic: there's no model judging another model.

* **Chunking:** size distribution, sentence boundaries, duplicates, metadata, fact integrity.
* **Retrieval:** keyword vs vector vs hybrid vs hybrid + rerank, with Hit@1/3/5/10, MRR, nDCG@10,
  Recall@10 and latency.
* **Answers:** correct facts, correct and wrong refusals, citation precision and recall, made-up
  numbers, latency.
* **Data questions:** valid plan, right operation, right number.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from outlawz.analysis.numbers import numbers_in, unmatched_numbers
from outlawz.chat.citations import check_citations, is_refusal
from outlawz.chat.prompts import NO_SOURCES_ANSWER, Source, answer_messages, fit_sources
from outlawz.evals.benchmark import QUESTIONS, Question
from outlawz.ingest.chunker import count_tokens
from outlawz.llm.base import LLM, LLMError, complete
from outlawz.search.service import Mode, SearchService

CONFIGS: dict[str, tuple[Mode, bool]] = {
    "keyword": ("words", False),
    "vector": ("meaning", False),
    "hybrid": ("best", False),
    "hybrid+rerank": ("best", True),
}
_SENTENCE_END = re.compile(r"[.!?:;\"”’)\]]\s*$")


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def relevant(passage_text: str, question: Question) -> bool:
    return bool(question.gold_phrase) and norm(question.gold_phrase or "") in norm(passage_text)


def _pct(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None}
    p50, p95 = np.percentile(np.asarray(values), [50, 95])
    return {"p50": round(float(p50), 2), "p95": round(float(p95), 2)}


def _share(n: int, total: int) -> float | None:
    return round(n / total, 4) if total else None


# ---------------------------------------------------------------- chunking


def chunking_suite(
    passages: list[dict[str, Any]], *, target: int, minimum: int, overlap: int
) -> dict[str, Any]:
    texts = [p for p in passages if p.get("kind", "text") in ("text", "table", "heading")]
    sizes = [count_tokens(str(p["text"])) for p in texts]
    size_stats: dict[str, Any] = {}
    if sizes:
        arr = np.asarray(sizes, dtype=float)
        size_stats = {
            "min": int(arr.min()),
            "p10": round(float(np.percentile(arr, 10)), 1),
            "p50": round(float(np.percentile(arr, 50)), 1),
            "p90": round(float(np.percentile(arr, 90)), 1),
            "max": int(arr.max()),
            "mean": round(float(arr.mean()), 1),
        }
    in_range = sum(1 for s in sizes if minimum <= s <= target + overlap)
    ends = sum(1 for p in texts if p.get("kind") == "table" or _SENTENCE_END.search(str(p["text"])))
    seen: dict[str, int] = {}
    for p in texts:
        key = norm(str(p["text"]))
        seen[key] = seen.get(key, 0) + 1
    duplicates = sum(c - 1 for c in seen.values() if c > 1)
    with_page = sum(1 for p in texts if p.get("page"))
    with_section = sum(1 for p in texts if p.get("section") or p.get("chapter"))
    facts = [q for q in QUESTIONS if q.kind == "fact" and q.gold_phrase]
    whole = sum(1 for q in facts if any(relevant(str(p["text"]), q) for p in texts))
    return {
        "passages": len(texts),
        "tokens": size_stats,
        "in_size_range": _share(in_range, len(texts)),
        "sentence_boundaries": _share(ends, len(texts)),
        "duplicates": _share(duplicates, len(texts)),
        "with_page": _share(with_page, len(texts)),
        "with_section": _share(with_section, len(texts)),
        "fact_integrity": _share(whole, len(facts)),
    }


# ---------------------------------------------------------------- retrieval


def _ndcg(ranks: list[int], relevant_count: int, k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(r + 1) for r in ranks if r <= k)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(relevant_count, k)))
    return dcg / ideal if ideal else 0.0


def retrieval_suite(
    search: SearchService,
    doc_ids: list[str],
    total_relevant: Callable[[Question], int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    facts = [q for q in QUESTIONS if q.kind == "fact"]
    out: dict[str, Any] = {}
    details: list[dict[str, Any]] = []
    for name, (mode, rerank) in CONFIGS.items():
        hits = {1: 0, 3: 0, 5: 0, 10: 0}
        rr, ndcg, recall, latency = [], [], [], []
        notes: set[str] = set()
        for q in facts:
            started = time.perf_counter()
            result = search.search(q.question, mode=mode, limit=10, doc_ids=doc_ids, rerank=rerank)
            latency.append((time.perf_counter() - started) * 1000)
            notes.update(result.notes)
            ranks = [
                i
                for i, h in enumerate(result.hits, start=1)
                if relevant(str(h.passage.get("text", "")), q)
            ]
            first = ranks[0] if ranks else None
            for k in hits:
                hits[k] += int(first is not None and first <= k)
            rr.append(1.0 / first if first else 0.0)
            n_rel = max(1, total_relevant(q))
            ndcg.append(_ndcg(ranks, n_rel))
            recall.append(min(1.0, len(ranks) / n_rel))
            details.append(
                {"suite": "retrieval", "config": name, "question": q.id, "first_relevant": first}
            )
        n = len(facts)
        out[name] = {
            **{f"hit@{k}": _share(v, n) for k, v in hits.items()},
            "mrr": round(sum(rr) / n, 4),
            "ndcg@10": round(sum(ndcg) / n, 4),
            "recall@10": round(sum(recall) / n, 4),
            "latency_ms": _pct(latency),
            "notes": sorted(notes),
        }
    return out, details


# ---------------------------------------------------------------- answers


async def answers_suite(
    search: SearchService,
    llm: LLM,
    doc_ids: list[str],
    *,
    top_k: int,
    context_tokens: int,
    max_tokens: int,
    temperature: float,
    document_name: Callable[[str], str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import asyncio

    asked = [q for q in QUESTIONS if q.kind in ("fact", "refusal")]
    details: list[dict[str, Any]] = []
    correct = refused_right = refused_wrong = made_up = 0
    precision, recall, latency = [], [], []
    errors = 0
    for q in asked:
        started = time.perf_counter()
        result = await asyncio.to_thread(
            search.search, q.question, mode="best", limit=top_k, doc_ids=doc_ids
        )
        sources = [
            Source(i, h.doc_id, document_name(h.doc_id), h.passage)
            for i, h in enumerate(result.hits, start=1)
        ]
        sources = fit_sources(sources, context_tokens)
        if sources:
            try:
                reply = await complete(
                    llm,
                    answer_messages(q.question, sources),
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                text = reply.text
            except LLMError as exc:
                errors += 1
                details.append({"suite": "answers", "question": q.id, "error": exc.code})
                continue
        else:
            text = NO_SOURCES_ANSWER
        latency.append((time.perf_counter() - started) * 1000)
        check = check_citations(text, {s.n for s in sources})
        refusal = check.refusal or is_refusal(check.text) or text == NO_SOURCES_ANSWER
        source_text = " ".join(str(s.passage.get("text", "")) for s in sources)
        invented = unmatched_numbers(
            re.sub(r"\[\d+\]", "", check.text),
            [n.value for n in numbers_in(source_text)] + [n.value for n in numbers_in(q.question)],
        )
        made_up += int(bool(invented))
        item: dict[str, Any] = {
            "suite": "answers",
            "question": q.id,
            "refusal": refusal,
            "cited": check.cited,
            "made_up_numbers": len(invented),
        }
        if q.kind == "fact":
            ok = any(norm(g) in norm(check.text) for g in q.gold_answer)
            correct += int(ok and not refusal)
            refused_wrong += int(refusal)
            gold_sources = {s.n for s in sources if relevant(str(s.passage.get("text", "")), q)}
            if check.cited:
                precision.append(len(set(check.cited) & gold_sources) / len(check.cited))
            if gold_sources:
                recall.append(len(set(check.cited) & gold_sources) / len(gold_sources))
            item["correct"] = ok and not refusal
        else:
            refused_right += int(refusal)
        details.append(item)
    facts = sum(1 for q in asked if q.kind == "fact")
    unanswerable = len(asked) - facts
    return (
        {
            "correct_facts": _share(correct, facts),
            "correct_refusals": _share(refused_right, unanswerable),
            "wrong_refusals": _share(refused_wrong, facts),
            "citation_precision": round(sum(precision) / len(precision), 4) if precision else None,
            "citation_recall": round(sum(recall) / len(recall), 4) if recall else None,
            "made_up_numbers": _share(made_up, len(asked) - errors),
            "model_errors": errors,
            "latency_ms": _pct(latency),
        },
        details,
    )


# ---------------------------------------------------------------- data questions


async def data_suite(ask: Callable[[str], Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    questions = [q for q in QUESTIONS if q.kind == "data"]
    valid = right_op = right_number = 0
    details: list[dict[str, Any]] = []
    for q in questions:
        try:
            answer = await ask(q.question)
        except LLMError as exc:
            details.append({"suite": "data", "question": q.id, "error": exc.code})
            continue
        if answer is None:
            details.append({"suite": "data", "question": q.id, "valid_plan": False})
            continue
        valid += 1
        op_ok = answer.plan.op == q.gold_op
        right_op += int(op_ok)
        number_ok = False
        if q.gold_number is not None:
            gold = q.gold_number
            text = f"{gold:.2f}" if not float(gold).is_integer() else f"{int(gold)}"
            number_ok = not unmatched_numbers(text, answer.result.numbers())
        right_number += int(number_ok)
        details.append(
            {
                "suite": "data",
                "question": q.id,
                "valid_plan": True,
                "op": answer.plan.op,
                "right_op": op_ok,
                "right_number": number_ok,
                "attempts": answer.attempts,
            }
        )
    n = len(questions)
    return (
        {
            "valid_plan": _share(valid, n),
            "right_operation": _share(right_op, n),
            "right_number": _share(right_number, n),
        },
        details,
    )
