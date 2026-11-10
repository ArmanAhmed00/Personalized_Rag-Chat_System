"""Process one document: extract → chunk → embed → write output atomically → ready.

Output for each document lives in ``.sidelamp/docs/<id>/``:

* ``passages.jsonl``: one passage per line (text, page, section, chapter, rows, BM25 term
  frequencies and length),
* ``vectors.npy``: one embedding per passage (row *i* = passage *i*),
* ``meta.json``: what produced it (content hash, extractor version, chunk settings, model).

The folder is built in ``.sidelamp/tmp`` and renamed into place only when complete, so killing the
app at any moment leaves either the old output or the new one. Processing is idempotent: if the
output already matches the file and settings, nothing is redone; if only the embedding model
changed, only the vectors are recomputed.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sidelamp.errors import DocumentError
from sidelamp.ingest.chunker import ChunkSettings, chunk_blocks
from sidelamp.ingest.embed import Embedder
from sidelamp.ingest.extractors import ExtractOptions, get_extractor
from sidelamp.ingest.extractors.base import Cancelled
from sidelamp.library.documents import Document, DocumentIndex, Progress, Status
from sidelamp.library.uploads import sha256_file
from sidelamp.paths import LibraryPaths
from sidelamp.search.tokenizer import term_frequencies
from sidelamp.store.files import atomic_dir, iter_jsonl, read_json, write_json, write_jsonl
from sidelamp.store.safepath import UnsafePath, resolve_inside

PASSAGES = "passages.jsonl"
VECTORS = "vectors.npy"
META = "meta.json"
PROGRESS_INTERVAL = 0.25


@dataclass
class PipelineContext:
    lib: LibraryPaths
    docs: DocumentIndex
    embedder: Embedder
    chunking: ChunkSettings
    options: ExtractOptions
    on_ready: Callable[[Document, list[dict[str, Any]], np.ndarray], None] | None = None


class FileGone(Exception):
    """The file disappeared from the library while waiting to be processed."""


def read_meta(doc_dir: Path) -> dict[str, Any] | None:
    meta = read_json(doc_dir / META, None)
    return meta if isinstance(meta, dict) else None


def read_passages(doc_dir: Path) -> list[dict[str, Any]]:
    return [p for p in iter_jsonl(doc_dir / PASSAGES) if isinstance(p, dict)]


def read_vectors(doc_dir: Path) -> np.ndarray | None:
    try:
        return np.load(doc_dir / VECTORS, allow_pickle=False)
    except (OSError, ValueError):
        return None


def output_settings(doc: Document, ctx: PipelineContext) -> dict[str, Any]:
    """What the passages depend on. If any of it changes, the document is read again."""
    extractor = get_extractor(doc.type)
    data: dict[str, Any] = {
        "extractor": doc.type,
        "extractor_version": getattr(extractor, "version", 0),
        "chunking": {
            "target": ctx.chunking.target,
            "overlap": ctx.chunking.overlap,
            "minimum": ctx.chunking.minimum,
        },
    }
    if doc.type == "csv":
        data["csv_rows"] = ctx.options.csv_rows
    return data


def needs_processing(doc: Document, ctx: PipelineContext) -> bool:
    """For a ``ready`` document: does its stored output still match the settings and model?"""
    meta = read_meta(ctx.lib.doc_dir(doc.id))
    if meta is None or meta.get("sha256") != doc.sha256:
        return True
    if meta.get("settings") != output_settings(doc, ctx):
        return True
    return meta.get("embed_model") != ctx.embedder.model_id


def embedding_text(passage: dict[str, Any]) -> str:
    """Passages are embedded with their chapter and section titles for context."""
    context = " › ".join(t for t in (passage.get("chapter"), passage.get("section")) if t)
    return f"{context}\n\n{passage['text']}" if context else str(passage["text"])


class _Ticker:
    """Throttled progress writes to the document index."""

    def __init__(self, docs: DocumentIndex, doc_id: str) -> None:
        self.docs = docs
        self.doc_id = doc_id
        self.last = 0.0

    def __call__(self, step: str, done: int, total: int, message: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last < PROGRESS_INTERVAL:
            return
        self.last = now
        self.docs.update(
            self.doc_id,
            lazy=True,
            progress=Progress(step=step, done=done, total=total, message=message),
        )


def _set_status(ctx: PipelineContext, doc_id: str, status: Status, message: str) -> None:
    ctx.docs.update(doc_id, status=status, progress=Progress(step=status.value, message=message))


def process(
    doc_id: str, ctx: PipelineContext, *, force: bool = False, cancel: threading.Event | None = None
) -> Document | None:
    """Process one document. Raises ``DocumentError`` (don't retry), ``TransientError`` or
    ``OSError`` (retry later), ``Cancelled`` or ``FileGone``."""
    cancel = cancel or threading.Event()
    doc = ctx.docs.get(doc_id)
    if doc is None:
        return None
    extractor = get_extractor(doc.type)
    if extractor is None:
        raise DocumentError("unsupported", "This file type isn't supported.")
    try:
        path = resolve_inside(ctx.lib.root, doc.path)
    except UnsafePath:
        raise FileGone() from None
    if not path.is_file():
        raise FileGone()

    sha = sha256_file(path)
    if sha != doc.sha256:
        stat = path.stat()
        doc = ctx.docs.update(doc_id, sha256=sha, size=stat.st_size, mtime=stat.st_mtime) or doc

    doc_dir = ctx.lib.doc_dir(doc_id)
    settings = output_settings(doc, ctx)
    meta = None if force else read_meta(doc_dir)
    reusable = (
        meta is not None
        and meta.get("sha256") == sha
        and meta.get("settings") == settings
        and (doc_dir / PASSAGES).exists()
    )
    ticker = _Ticker(ctx.docs, doc_id)

    if reusable and meta is not None and meta.get("embed_model") == ctx.embedder.model_id:
        vectors = read_vectors(doc_dir)
        passages = read_passages(doc_dir)
        if vectors is not None and len(vectors) == len(passages):
            return _finish(ctx, doc_id, sha, passages, vectors, meta)

    extra: dict[str, Any] = {}
    pages: int | None = doc.pages
    if reusable and meta is not None:
        passages = read_passages(doc_dir)
        extra = meta.get("extra", {})
        pages = meta.get("pages")
    else:
        _set_status(ctx, doc_id, Status.reading, f"Opening {extractor.label}")
        extracted = extractor.extract(
            path,
            lambda done, total, message: ticker("reading", done, total, message),
            ctx.options,
            cancel.is_set,
        )
        pages = extracted.pages
        extra = extracted.meta
        if cancel.is_set():
            raise Cancelled()
        _set_status(ctx, doc_id, Status.chunking, "Splitting into passages")
        chunks = chunk_blocks(extracted.blocks, ctx.chunking)
        if not chunks:
            raise DocumentError("no_text", "No readable text was found in this file.")
        passages = []
        for i, chunk in enumerate(chunks):
            tf, length = term_frequencies(chunk.index_text or chunk.text)
            passages.append(
                {
                    "i": i,
                    "text": chunk.text,
                    "kind": chunk.kind,
                    "page": chunk.page,
                    "page_end": chunk.page_end,
                    "section": chunk.section,
                    "chapter": chunk.chapter,
                    "rows": list(chunk.rows) if chunk.rows else None,
                    "tf": tf,
                    "len": length,
                }
            )

    if cancel.is_set():
        raise Cancelled()
    total = len(passages)
    _set_status(ctx, doc_id, Status.embedding, f"Making {total} passages searchable")

    def embed_progress(done: int, count: int) -> None:
        if cancel.is_set():
            raise Cancelled()
        ticker("embedding", done, count, f"Making passages searchable: {done} of {count}")

    vectors = ctx.embedder.embed_passages([embedding_text(p) for p in passages], embed_progress)
    if len(vectors) != total:
        raise DocumentError(
            "embedding_mismatch",
            "The search model returned the wrong number of results. Choose Read again.",
        )
    new_meta = {
        "doc_id": doc_id,
        "sha256": sha,
        "type": doc.type,
        "settings": settings,
        "embed_model": ctx.embedder.model_id,
        "dim": int(vectors.shape[1]) if total else 0,
        "count": total,
        "pages": pages,
        "extra": _jsonable(extra),
        "created_at": time.time(),
    }
    with atomic_dir(doc_dir, ctx.lib.tmp_dir) as work:
        write_jsonl(work / PASSAGES, passages)
        with open(work / VECTORS, "wb") as fh:
            np.save(fh, vectors.astype(np.float32), allow_pickle=False)
        write_json(work / META, new_meta)
    return _finish(ctx, doc_id, sha, passages, vectors, new_meta)


def _finish(
    ctx: PipelineContext,
    doc_id: str,
    sha: str,
    passages: list[dict[str, Any]],
    vectors: np.ndarray,
    meta: dict[str, Any],
) -> Document | None:
    current = ctx.docs.get(doc_id)
    if current is None:
        # Deleted while we worked: don't leave orphaned output behind.
        from sidelamp.store.files import remove_tree

        remove_tree(ctx.lib.doc_dir(doc_id))
        return None
    if current.sha256 != sha:
        return current  # changed again while we worked; it's already queued for another pass
    done = ctx.docs.update(
        doc_id,
        status=Status.ready,
        chunks=len(passages),
        pages=meta.get("pages"),
        embed_model=ctx.embedder.model_id,
        error=None,
        attempts=0,
        ready_at=time.time(),
        progress=Progress(
            step="ready",
            done=len(passages),
            total=len(passages),
            message=f"Ready: {len(passages)} passages",
        ),
    )
    if done is not None and ctx.on_ready is not None:
        ctx.on_ready(done, passages, vectors)
    return done


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
