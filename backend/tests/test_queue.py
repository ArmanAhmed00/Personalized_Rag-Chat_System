"""The status-as-queue: workers, retries with backoff, damaged files, cancel, restart."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidelamp.errors import TransientError
from sidelamp.ingest import queue as queue_module
from sidelamp.ingest.embed import FakeEmbedder
from sidelamp.ingest.queue import IngestQueue, backoff_seconds
from sidelamp.library.documents import Document, Status
from sidelamp.library.uploads import sha256_file
from tests.builders import simple_pdf
from tests.test_pipeline import make_ctx

pytestmark = pytest.mark.anyio


class FlakyEmbedder(FakeEmbedder):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.calls = 0

    def embed_passages(self, texts, progress=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientError("Ollama isn't answering. Is it running?")
        return super().embed_passages(texts, progress)


def add(ctx, name: str, doc_id: str) -> None:  # type: ignore[no-untyped-def]
    path = simple_pdf(ctx.lib.root / name, [f"Content of {name}."])
    ctx.docs.add(
        Document(id=doc_id, path=name, sha256=sha256_file(path), size=1, mtime=1, type="pdf")
    )


async def run_until_idle(q: IngestQueue, timeout: float = 20) -> None:
    task = asyncio.create_task(q.run())
    try:
        while q._wake is None:
            await asyncio.sleep(0.01)
        await q.wait_idle(timeout)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def make_queue(ctx, retries: int = 3) -> IngestQueue:  # type: ignore[no-untyped-def]
    return IngestQueue(ctx.docs, lambda: ctx, workers=lambda: 2, retries=lambda: retries)


async def test_processes_everything(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    for i in range(3):
        add(ctx, f"d{i}.pdf", f"d{i}")
    await run_until_idle(make_queue(ctx))
    assert {d.status for d in ctx.docs.all()} == {Status.ready}


async def test_transient_errors_retry_with_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue_module, "backoff_seconds", lambda attempt: 0.05)
    ctx = make_ctx(tmp_path, FlakyEmbedder(failures=2))
    add(ctx, "a.pdf", "a")
    await run_until_idle(make_queue(ctx))
    doc = ctx.docs.get("a")
    assert doc is not None and doc.status == Status.ready and doc.attempts == 0
    assert ctx.embedder.calls == 3  # type: ignore[attr-defined]


async def test_retries_run_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue_module, "backoff_seconds", lambda attempt: 0.02)
    ctx = make_ctx(tmp_path, FlakyEmbedder(failures=99))
    add(ctx, "a.pdf", "a")
    await run_until_idle(make_queue(ctx, retries=2))
    doc = ctx.docs.get("a")
    assert doc is not None and doc.status == Status.failed
    assert doc.error is not None and "Ollama" in doc.error.message
    assert ctx.embedder.calls == 3  # type: ignore[attr-defined]


async def test_damaged_file_fails_once(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    path = ctx.lib.root / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\nbroken")
    ctx.docs.add(
        Document(id="bad", path="bad.pdf", sha256=sha256_file(path), size=1, mtime=1, type="pdf")
    )
    await run_until_idle(make_queue(ctx))
    doc = ctx.docs.get("bad")
    assert doc is not None and doc.status == Status.failed and doc.attempts == 0
    assert doc.error is not None and doc.error.code == "damaged"


async def test_cancel_queued_document(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add(ctx, "a.pdf", "a")
    q = make_queue(ctx)
    assert q.cancel("a")
    doc = ctx.docs.get("a")
    assert doc is not None and doc.status == Status.failed
    assert doc.error is not None and doc.error.code == "cancelled"
    q.request("a", force=True)
    await run_until_idle(q)
    assert ctx.docs.get("a").status == Status.ready  # type: ignore[union-attr]


async def test_reconcile_requeues_after_model_change(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    add(ctx, "a.pdf", "a")
    await run_until_idle(make_queue(ctx))
    ctx.embedder = FakeEmbedder(dim=32)
    q = make_queue(ctx)
    assert q.reconcile() == 1
    await run_until_idle(q)
    assert ctx.docs.get("a").embed_model == "fake:hash-32"  # type: ignore[union-attr]


def test_backoff_grows_and_caps() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert backoff_seconds(20) == 60
