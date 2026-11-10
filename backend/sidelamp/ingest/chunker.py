"""Paragraph-aware chunking.

Rules (sizes are configurable, defaults in brackets):

* Paragraphs are packed into passages of about ``target`` tokens [350].
* A passage never crosses a section or chapter boundary.
* A paragraph longer than the target is split on sentence boundaries; a sentence longer than the
  target is split on words.
* Consecutive passages in the same section overlap by about ``overlap`` tokens [50], taken as
  whole sentences from the end of the previous passage.
* Fragments under ``minimum`` tokens [40] are merged into a neighbour in the same section.
* Tables, CSV schema/profile blocks and CSV row blocks are kept as their own passages.

"Tokens" here are words and punctuation marks: close enough to model tokens for sizing, and
stable across embedding models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sidelamp.ingest.extractors.base import Block

_TOKEN = re.compile(r"\w+|[^\w\s]")
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"”’)\]]*\s+(?=[\"“‘(\[]?[A-Z0-9À-Ý])")
_ABBREVIATIONS = re.compile(
    r"\b(e\.g|i\.e|etc|vs|mr|mrs|ms|dr|prof|fig|no|vol|pp|approx|inc|ltd|st|jr|sr)\.$",
    re.IGNORECASE,
)
_STANDALONE = frozenset({"table", "schema", "profile", "rows"})


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


@dataclass
class ChunkSettings:
    target: int = 350
    overlap: int = 50
    minimum: int = 40


@dataclass
class Chunk:
    text: str
    kind: str = "text"
    page: int | None = None
    page_end: int | None = None
    section: str | None = None
    chapter: str | None = None
    rows: tuple[int, int] | None = None
    index_text: str | None = None
    tokens: int = 0
    _pieces: list[str] = field(default_factory=list, repr=False)


def split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text.strip())
    sentences: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if sentences and _ABBREVIATIONS.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return sentences


def _split_words(sentence: str, target: int) -> list[str]:
    words = sentence.split()
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for word in words:
        n = count_tokens(word)
        if current and size + n > target:
            pieces.append(" ".join(current))
            current, size = [], 0
        current.append(word)
        size += n
    if current:
        pieces.append(" ".join(current))
    return pieces


def _units(text: str, target: int) -> list[str]:
    """Break a paragraph into pieces no longer than the target: itself, sentences, or words."""
    if count_tokens(text) <= target:
        return [text]
    units: list[str] = []
    for sentence in split_sentences(text):
        if count_tokens(sentence) <= target:
            units.append(sentence)
        else:
            units.extend(_split_words(sentence, target))
    return units


def _tail(pieces: list[str], overlap: int) -> list[str]:
    """Whole sentences from the end of a passage, about ``overlap`` tokens' worth."""
    if overlap <= 0 or not pieces:
        return []
    sentences: list[str] = []
    for piece in pieces:
        sentences.extend(split_sentences(piece))
    taken: list[str] = []
    size = 0
    for sentence in reversed(sentences):
        n = count_tokens(sentence)
        if taken and size + n > overlap:
            break
        if not taken and n > overlap * 2:
            break  # one huge sentence would make the overlap bigger than the passage
        taken.insert(0, sentence)
        size += n
    return taken


def _split_table(block: Block, target: int) -> list[str]:
    lines = block.text.splitlines()
    if count_tokens(block.text) <= target * 1.5 or len(lines) <= 3:
        return [block.text]
    header, rest = lines[:2], lines[2:]
    pieces: list[str] = []
    current: list[str] = []
    size = count_tokens("\n".join(header))
    for line in rest:
        n = count_tokens(line)
        if current and size + n > target:
            pieces.append("\n".join(header + current))
            current, size = [], count_tokens("\n".join(header))
        current.append(line)
        size += n
    if current:
        pieces.append("\n".join(header + current))
    return pieces


def chunk_blocks(blocks: list[Block], settings: ChunkSettings | None = None) -> list[Chunk]:
    cfg = settings or ChunkSettings()
    chunks: list[Chunk] = []
    group: list[Block] = []
    key: tuple[str | None, str | None] | None = None

    def flush_group() -> None:
        if group:
            chunks.extend(_chunk_section(group, cfg))
            group.clear()

    for block in blocks:
        if not block.text.strip():
            continue
        if block.kind in _STANDALONE:
            flush_group()
            if block.kind == "table":
                for piece in _split_table(block, cfg.target):
                    chunks.append(_make(piece, block, block.page, kind="table"))
            else:
                chunks.append(_make(block.text, block, block.page, kind=block.kind))
            key = None
            continue
        block_key = (block.chapter, block.section)
        if key is not None and block_key != key:
            flush_group()
        key = block_key
        group.append(block)
    flush_group()
    return chunks


def _make(
    text: str, block: Block, page: int | None, *, kind: str = "text", page_end: int | None = None
) -> Chunk:
    return Chunk(
        text=text,
        kind=kind,
        page=page,
        page_end=page_end if page_end != page else None,
        section=block.section,
        chapter=block.chapter,
        rows=block.rows,
        index_text=block.index_text,
        tokens=count_tokens(text),
    )


def _chunk_section(blocks: list[Block], cfg: ChunkSettings) -> list[Chunk]:
    """Pack one section's paragraphs (all share chapter and section) into passages."""
    template = blocks[0]
    units: list[tuple[str, int | None, bool]] = []  # text, page, starts a paragraph
    for block in blocks:
        pieces = _units(block.text, cfg.target)
        for i, piece in enumerate(pieces):
            units.append((piece, block.page, i == 0))

    passages: list[tuple[list[str], list[int | None]]] = []
    current: list[str] = []
    pages: list[int | None] = []
    size = 0
    for text, page, new_paragraph in units:
        n = count_tokens(text)
        if current and size + n > cfg.target:
            passages.append((current, pages))
            carry = _tail(current, cfg.overlap)
            current = list(carry)
            pages = [pages[-1]] * len(carry) if carry else []
            size = sum(count_tokens(t) for t in current)
        if current and new_paragraph:
            current.append("\n\n" + text)
        else:
            current.append(text)
        pages.append(page)
        size += n
    if current:
        passages.append((current, pages))

    out: list[Chunk] = []
    for pieces, page_list in passages:
        text = _join(pieces)
        known = [p for p in page_list if p is not None]
        start = min(known) if known else None
        end = max(known) if known else None
        out.append(_make(text, template, start, page_end=end))
    return _merge_small(out, cfg)


def _join(pieces: list[str]) -> str:
    text = ""
    for piece in pieces:
        if piece.startswith("\n\n"):
            text = text.rstrip() + piece
        elif text:
            text += " " + piece
        else:
            text = piece
    return text.strip()


def _merge_small(chunks: list[Chunk], cfg: ChunkSettings) -> list[Chunk]:
    """Merge passages under the minimum into a neighbour (same section by construction)."""
    if len(chunks) < 2:
        return chunks
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and (chunk.tokens < cfg.minimum or merged[-1].tokens < cfg.minimum)
            and (merged[-1].tokens + chunk.tokens <= int(cfg.target * 1.25))
        ):
            prev = merged[-1]
            prev.text = f"{prev.text}\n\n{chunk.text}"
            prev.tokens = count_tokens(prev.text)
            pages = [p for p in (prev.page, prev.page_end, chunk.page, chunk.page_end) if p]
            if pages:
                prev.page = min(pages)
                end = max(pages)
                prev.page_end = end if end != prev.page else None
            continue
        merged.append(chunk)
    return merged
