"""Follow-up rewrite: "and the south one?" → "sales of the south region in 2024".

A separate short prompt sees the last few messages and the follow-up and returns a standalone
search query. If the model fails or returns something unusable, the question is used as is.
"""

from __future__ import annotations

from collections.abc import Sequence

from outlawz.chat.prompts import looks_like_follow_up, rewrite_messages
from outlawz.llm.base import LLM, LLMError, complete

MAX_QUERY_CHARS = 400


def needs_rewrite(history: Sequence[tuple[str, str]], question: str) -> bool:
    return bool(history) and looks_like_follow_up(question)


def clean_query(text: str, fallback: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    query = lines[0] if lines else ""
    for prefix in ("query:", "search query:", "standalone query:"):
        if query.lower().startswith(prefix):
            query = query[len(prefix) :].strip()
    query = query.strip().strip("\"'“”‘’`").strip()
    if not query or len(query) > MAX_QUERY_CHARS:
        return fallback
    return query


async def rewrite_follow_up(llm: LLM, history: Sequence[tuple[str, str]], question: str) -> str:
    """Return a standalone query for ``question`` given ``history`` of ``(role, text)``."""
    try:
        completion = await complete(
            llm, rewrite_messages(history, question), max_tokens=80, temperature=0.0
        )
    except LLMError:
        return question
    return clean_query(completion.text, question)
