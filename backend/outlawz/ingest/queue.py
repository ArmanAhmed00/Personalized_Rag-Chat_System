"""The processing queue, without a database: a document's status in the index *is* the queue.

* Anything not ``ready`` or ``failed`` is waiting. On startup, documents caught mid-processing go
  back to ``queued``.
* Work runs in a thread pool, so the API stays responsive. ``workers`` documents at a time.
* Temporary errors (file busy, model server not answering) retry with exponential backoff, up to
  ``retries`` times. A damaged file fails once with a clear message.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from outlawz.errors import DocumentError, TransientError
from outlawz.ingest.extractors.base import Cancelled
from outlawz.ingest.pipeline import FileGone, PipelineContext, needs_processing, process
from outlawz.library.documents import DocErrorInfo, DocumentIndex, Progress, Status

log = logging.getLogger("outlawz")

MAX_BACKOFF = 60.0
IDLE_POLL = 2.0


def backoff_seconds(attempt: int) -> float:
    return float(min(MAX_BACKOFF, 2 ** max(1, attempt)))


class IngestQueue:
    def __init__(
        self,
        docs: DocumentIndex,
        make_context: Callable[[], PipelineContext],
        *,
        workers: Callable[[], int],
        retries: Callable[[], int],
        on_step: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> None:
        self.docs = docs
        self.make_context = make_context
        self.workers = workers
        self.retries = retries
        self.on_step = on_step
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: dict[str, threading.Event] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._force: set[str] = set()
        self._retry_at: dict[str, float] = {}
        self.current: dict[str, float] = {}  # doc id -> start time, for the Processing page

    # ---------------------------------------------------------------- control

    def wake(self) -> None:
        if self._wake is None or self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._wake.set()
        else:
            self._loop.call_soon_threadsafe(self._wake.set)

    def request(self, doc_id: str, *, force: bool = False) -> None:
        """Queue a document (again). ``force`` ignores any existing output ("Read again")."""
        if doc_id in self._running:
            self._running[doc_id].set()  # stop the current pass; the new one starts after
        if force:
            self._force.add(doc_id)
        self._retry_at.pop(doc_id, None)
        self.docs.update(
            doc_id,
            status=Status.queued,
            error=None,
            attempts=0,
            progress=Progress(message="Waiting to be read"),
        )
        self.wake()

    def cancel(self, doc_id: str) -> bool:
        event = self._running.get(doc_id)
        if event is not None:
            event.set()
            self._force.discard(doc_id)
            return True
        doc = self.docs.get(doc_id)
        if doc is not None and not doc.status.settled:
            self._mark_cancelled(doc_id)
            return True
        return False

    def _mark_cancelled(self, doc_id: str) -> None:
        self.docs.update(
            doc_id,
            status=Status.failed,
            error=DocErrorInfo(
                code="cancelled", message="Cancelled. Choose Read again to read it."
            ),
            progress=Progress(step="failed", message="Cancelled"),
        )

    def reconcile(self) -> int:
        """Re-queue ready documents whose output no longer matches the settings or model."""
        ctx = self.make_context()
        count = 0
        for doc in self.docs.all():
            if doc.status == Status.ready and needs_processing(doc, ctx):
                self.docs.update(
                    doc.id,
                    lazy=True,
                    status=Status.queued,
                    progress=Progress(message="Settings changed: reading again"),
                )
                count += 1
        if count:
            self.docs.save()
            self.wake()
        return count

    @property
    def length(self) -> int:
        return len(self.docs.pending())

    def is_running(self, doc_id: str) -> bool:
        return doc_id in self._running

    # ---------------------------------------------------------------- loop

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        await asyncio.to_thread(self.reconcile)
        try:
            while True:
                self._start_jobs()
                self._wake.clear()
                await self._sleep(self._next_timeout())
        finally:
            for event in self._running.values():
                event.set()
            for task in list(self._tasks):
                task.cancel()

    async def _sleep(self, timeout: float) -> None:
        """Wait until woken or ``timeout`` passes.

        Not ``asyncio.wait_for``: on Python 3.11 it can swallow a cancellation that arrives just as
        the event is set, and then stopping the app (or a test) would wait forever.
        """
        assert self._wake is not None
        waiter = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({waiter}, timeout=timeout)
        finally:
            waiter.cancel()

    def _next_timeout(self) -> float:
        if not self._retry_at:
            return IDLE_POLL
        soonest = min(self._retry_at.values()) - time.time()
        return max(0.05, min(IDLE_POLL, soonest))

    def _start_jobs(self) -> None:
        now = time.time()
        limit = max(1, self.workers())
        for doc in self.docs.pending():
            if len(self._running) >= limit:
                break
            if doc.id in self._running:
                continue
            if self._retry_at.get(doc.id, 0) > now:
                continue
            self._retry_at.pop(doc.id, None)
            cancel = threading.Event()
            self._running[doc.id] = cancel
            task = asyncio.create_task(self._job(doc.id, cancel), name=f"ingest:{doc.id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def wait_idle(self, timeout: float = 30.0) -> None:
        """Wait until nothing is queued or running (used by tests and the eval runner)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._running and not self.docs.pending():
                return
            self.wake()
            await asyncio.sleep(0.02)
        raise TimeoutError("Ingestion didn't finish in time")

    async def _job(self, doc_id: str, cancel: threading.Event) -> None:
        force = doc_id in self._force
        self._force.discard(doc_id)
        started = time.perf_counter()
        self.current[doc_id] = time.time()
        outcome = "ready"
        steps: dict[str, float] = {}
        try:
            ctx = self.make_context()
            await asyncio.to_thread(process, doc_id, ctx, force=force, cancel=cancel, steps=steps)
        except Cancelled:
            outcome = "cancelled"
            doc = self.docs.get(doc_id)
            # A "Read again" while running also sets the event; then the doc is queued, not failed.
            if doc is not None and doc.status != Status.queued:
                self._mark_cancelled(doc_id)
        except FileGone:
            outcome = "gone"
            self._fail(doc_id, "missing", "The file is no longer in the library folder.")
        except DocumentError as exc:
            outcome = "failed"
            self._fail(doc_id, exc.code, exc.message)
        except (TransientError, OSError) as exc:
            outcome = "retry"
            self._retry(doc_id, exc)
        except Exception:
            outcome = "failed"
            log.exception("Unexpected error while reading document %s", doc_id)
            self._fail(
                doc_id,
                "unreadable",
                "This file couldn't be read. It may be damaged or in an unusual format.",
            )
        finally:
            self._running.pop(doc_id, None)
            self.current.pop(doc_id, None)
            if self.on_step is not None:
                with contextlib.suppress(Exception):
                    self.on_step(
                        "ingest.document",
                        (time.perf_counter() - started) * 1000,
                        {"doc_id": doc_id, "outcome": outcome, "steps": steps},
                    )
            if self._wake is not None:
                self._wake.set()

    def _fail(self, doc_id: str, code: str, message: str) -> None:
        self.docs.update(
            doc_id,
            status=Status.failed,
            error=DocErrorInfo(code=code, message=message),
            progress=Progress(step="failed", message=message),
        )

    def _retry(self, doc_id: str, exc: Exception) -> None:
        doc = self.docs.get(doc_id)
        if doc is None:
            return
        attempts = doc.attempts + 1
        if attempts > self.retries():
            message = (
                str(exc)
                if isinstance(exc, TransientError) and str(exc)
                else (
                    "This file couldn't be read right now (it may be open in another program). "
                    "Choose Read again to try once more."
                )
            )
            self.docs.update(doc_id, attempts=attempts)
            self._fail(doc_id, "temporary", message)
            return
        delay = backoff_seconds(attempts)
        self._retry_at[doc_id] = time.time() + delay
        reason = str(exc) if isinstance(exc, TransientError) and str(exc) else "The file was busy."
        self.docs.update(
            doc_id,
            status=Status.queued,
            attempts=attempts,
            progress=Progress(message=f"{reason} Trying again in {int(delay)} seconds."),
        )
