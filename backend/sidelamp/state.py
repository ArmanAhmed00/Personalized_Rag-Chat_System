"""The running app's services, held in one object (no module-level globals).

``AppState.open()`` takes the library lock, upgrades the on-disk format if needed, repairs what a
crash may have left behind and loads the document index. Later phases attach the search index,
ingestion queue, watcher and model providers here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

from sidelamp.config import Settings
from sidelamp.errors import NotFound
from sidelamp.library.documents import Document, DocumentIndex
from sidelamp.library.trash import move_to_trash
from sidelamp.paths import DataPaths, LibraryPaths
from sidelamp.store.files import KeyedLocks, recover_dirs, remove_tree
from sidelamp.store.lock import HEARTBEAT_SECONDS, InstanceLock
from sidelamp.store.migrations import MigrationReport, ensure_format
from sidelamp.store.safepath import UnsafePath, resolve_inside

log = logging.getLogger("sidelamp")

Handler = Callable[..., None]


class Events:
    """A tiny in-process event hub, so modules react to each other without importing each other.

    Events: ``document_added``, ``document_changed``, ``document_removed``, ``document_ready``.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, name: str, handler: Handler) -> None:
        self._handlers[name].append(handler)

    def emit(self, name: str, **payload: Any) -> None:
        for handler in list(self._handlers.get(name, ())):
            try:
                handler(**payload)
            except Exception:
                log.exception("Handler for %s failed", name)


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lib = LibraryPaths(settings.library)
        self.data = DataPaths(settings.data_dir)
        self.lock = InstanceLock(self.lib.lock_file)
        self.events = Events()
        self.file_locks = KeyedLocks()
        self.documents: DocumentIndex | None = None
        self.migration: MigrationReport | None = None
        self.opened = False
        self._tasks: list[asyncio.Task[None]] = []
        self._closers: list[Callable[[], Any]] = []

    # ---------------------------------------------------------------- lifecycle

    def open(self) -> None:
        if self.opened:
            return
        self.data.ensure()
        self.lib.root.mkdir(parents=True, exist_ok=True)
        self.lib.app.mkdir(parents=True, exist_ok=True)
        self.lock.acquire()
        try:
            self.migration = ensure_format(self.lib.app, self.data.backups_dir)
            self.lib.ensure()
            recover_dirs(self.lib.docs_dir, self.lib.tmp_dir)
            self.documents = DocumentIndex(self.lib.documents_file)
            self.documents.requeue_unsettled()
        except BaseException:
            self.lock.release()
            raise
        self.opened = True

    def start_background(self) -> None:
        self.spawn(self._heartbeat_loop(), "heartbeat")
        self.spawn(self._flush_loop(), "flush")

    def spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        self._tasks.append(asyncio.create_task(coro, name=f"sidelamp:{name}"))

    def on_close(self, fn: Callable[[], Any]) -> None:
        self._closers.append(fn)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        for fn in reversed(self._closers):
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("Shutdown step failed")
        if self.documents is not None:
            self.documents.flush()
        self.lock.release()
        self.opened = False

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not await asyncio.to_thread(self.lock.heartbeat):
                log.warning("Another copy of Sidelamp took over this library's lock.")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.documents is not None:
                await asyncio.to_thread(self.documents.flush)

    # ---------------------------------------------------------------- helpers

    @property
    def docs(self) -> DocumentIndex:
        if self.documents is None:
            raise RuntimeError("The library isn't open")
        return self.documents

    def require_document(self, doc_id: str) -> Document:
        doc = self.docs.get(doc_id)
        if doc is None:
            raise NotFound("That document")
        return doc

    def document_file(self, doc: Document) -> Any:
        """The real path of a document's file, checked to be inside the library."""
        try:
            return resolve_inside(self.lib.root, doc.path)
        except UnsafePath:
            raise NotFound("That document's file") from None

    def forget_document(self, doc_id: str) -> Document | None:
        """Remove a document from the index and delete its processed data (not the file)."""
        doc = self.docs.remove(doc_id)
        remove_tree(self.lib.doc_dir(doc_id))
        if doc is not None:
            self.events.emit("document_removed", doc=doc)
        return doc

    def delete_document(self, doc_id: str) -> Document:
        """Delete in the app: the file goes to Trash, its processed data is removed."""
        doc = self.require_document(doc_id)
        path = self.document_file(doc)
        if path.exists():
            move_to_trash(path, self.data.trash_dir, original=doc.path)
        self.forget_document(doc_id)
        return doc
