"""Processing one document: output layout, idempotence, crash safety, model changes."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from sidelamp.errors import DocumentError
from sidelamp.ingest import pipeline
from sidelamp.ingest.chunker import ChunkSettings
from sidelamp.ingest.embed import FakeEmbedder
from sidelamp.ingest.extractors import ExtractOptions
from sidelamp.ingest.extractors.base import Cancelled
from sidelamp.ingest.pipeline import (
    FileGone,
    PipelineContext,
    needs_processing,
    process,
    read_meta,
    read_passages,
    read_vectors,
)
from sidelamp.library.documents import Document, DocumentIndex, Status
from sidelamp.library.uploads import sha256_file
from sidelamp.paths import LibraryPaths
from tests.builders import structured_pdf


def make_ctx(tmp_path: Path, embedder: object | None = None) -> PipelineContext:
    lib = LibraryPaths(tmp_path / "library")
    lib.ensure()
    return PipelineContext(
        lib=lib,
        docs=DocumentIndex(lib.documents_file),
        embedder=embedder or FakeEmbedder(),  # type: ignore[arg-type]
        chunking=ChunkSettings(target=80, overlap=10, minimum=10),
        options=ExtractOptions(ocr=False),
    )


def add_pdf(ctx: PipelineContext, name: str = "report.pdf") -> Document:
    path = structured_pdf(ctx.lib.root / name)
    stat = path.stat()
    doc = Document(
        id="doc1",
        path=name,
        sha256=sha256_file(path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        type="pdf",
    )
    return ctx.docs.add(doc)


def test_output_layout(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    done = process("doc1", ctx)
    assert done is not None and done.status == Status.ready
    folder = ctx.lib.doc_dir("doc1")
    passages = read_passages(folder)
    vectors = read_vectors(folder)
    assert vectors is not None and vectors.shape == (len(passages), 256)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert done.chunks == len(passages) and done.pages == 4
    first = passages[0]
    assert set(first) >= {"i", "text", "page", "section", "chapter", "tf", "len"}
    assert first["tf"] and first["len"] == sum(first["tf"].values())
    meta = read_meta(folder)
    assert meta is not None and meta["sha256"] == done.sha256 and meta["count"] == len(passages)
    assert list(ctx.lib.tmp_dir.iterdir()) == []


def test_processing_is_idempotent(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    process("doc1", ctx)
    stamp = (ctx.lib.doc_dir("doc1") / "meta.json").stat().st_mtime_ns
    process("doc1", ctx)
    assert (ctx.lib.doc_dir("doc1") / "meta.json").stat().st_mtime_ns == stamp
    assert not needs_processing(ctx.docs.get("doc1"), ctx)  # type: ignore[arg-type]


def test_model_change_only_recomputes_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    process("doc1", ctx)
    before = read_passages(ctx.lib.doc_dir("doc1"))
    ctx.embedder = FakeEmbedder(dim=64)
    assert needs_processing(ctx.docs.get("doc1"), ctx)  # type: ignore[arg-type]

    def no_extract(*args: object, **kwargs: object) -> None:
        raise AssertionError("should not re-read the file")

    from sidelamp.ingest.extractors import get_extractor

    monkeypatch.setattr(get_extractor("pdf"), "extract", no_extract)
    done = process("doc1", ctx)
    assert done is not None and done.embed_model == "fake:hash-64"
    assert read_passages(ctx.lib.doc_dir("doc1")) == before
    vectors = read_vectors(ctx.lib.doc_dir("doc1"))
    assert vectors is not None and vectors.shape[1] == 64


def test_chunk_setting_change_requires_reread(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    process("doc1", ctx)
    ctx.chunking = ChunkSettings(target=200, overlap=10, minimum=10)
    assert needs_processing(ctx.docs.get("doc1"), ctx)  # type: ignore[arg-type]


class ExplodingEmbedder(FakeEmbedder):
    def embed_passages(self, texts, progress=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("killed mid-embedding")


def test_crash_mid_processing_keeps_previous_output(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    process("doc1", ctx)
    before = (ctx.lib.doc_dir("doc1") / "passages.jsonl").read_bytes()
    ctx.embedder = ExplodingEmbedder(dim=32)
    with pytest.raises(RuntimeError):
        process("doc1", ctx, force=True)
    assert (ctx.lib.doc_dir("doc1") / "passages.jsonl").read_bytes() == before
    assert list(ctx.lib.tmp_dir.iterdir()) == []


def test_crash_while_writing_output_leaves_nothing_half_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)

    def die(*args: object, **kwargs: object) -> None:
        raise OSError("power cut")

    monkeypatch.setattr(pipeline, "write_json", die)
    with pytest.raises(OSError):
        process("doc1", ctx)
    assert not ctx.lib.doc_dir("doc1").exists()
    assert list(ctx.lib.tmp_dir.iterdir()) == []


def test_cancel(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        process("doc1", ctx, cancel=cancel)
    assert not ctx.lib.doc_dir("doc1").exists()


def test_missing_file(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)
    (ctx.lib.root / "report.pdf").unlink()
    with pytest.raises(FileGone):
        process("doc1", ctx)


def test_damaged_file_is_a_document_error(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    path = ctx.lib.root / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\nnot really")
    ctx.docs.add(
        Document(id="bad", path="bad.pdf", sha256=sha256_file(path), size=1, mtime=1, type="pdf")
    )
    with pytest.raises(DocumentError):
        process("bad", ctx)


def test_deleted_during_processing_leaves_no_output(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add_pdf(ctx)

    class DeletingEmbedder(FakeEmbedder):
        def embed_passages(self, texts, progress=None):  # type: ignore[no-untyped-def]
            ctx.docs.remove("doc1")
            return super().embed_passages(texts, progress)

    ctx.embedder = DeletingEmbedder()
    assert process("doc1", ctx) is None
    assert not ctx.lib.doc_dir("doc1").exists()


def test_csv_rows_index_values_only(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    path = ctx.lib.root / "s.csv"
    path.write_text("region,sales\nNorth,120\nSouth,80\n")
    ctx.docs.add(
        Document(id="c", path="s.csv", sha256=sha256_file(path), size=1, mtime=1, type="csv")
    )
    process("c", ctx)
    rows = [p for p in read_passages(ctx.lib.doc_dir("c")) if p["kind"] == "rows"]
    assert rows[0]["rows"] == [1, 2]
    assert "region" not in rows[0]["tf"] and "north" in rows[0]["tf"]
