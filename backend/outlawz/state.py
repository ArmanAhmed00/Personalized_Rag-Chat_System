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

from outlawz.analysis.service import DataAnalyst
from outlawz.backups import repair_interrupted_restore
from outlawz.chat.chats import ChatMessage, ChatStore
from outlawz.chat.preferences import PreferenceStore
from outlawz.config import Settings
from outlawz.errors import NotFound
from outlawz.evals.runner import EvalRunner
from outlawz.exports import ExportStore
from outlawz.ingest.chunker import ChunkSettings
from outlawz.ingest.embed import Embedder, embedder_id, make_embedder
from outlawz.ingest.extractors import ExtractOptions
from outlawz.ingest.pipeline import PipelineContext, read_passages, read_vectors
from outlawz.ingest.queue import IngestQueue
from outlawz.library.documents import Document, DocumentIndex, Progress, Status
from outlawz.library.trash import move_to_trash
from outlawz.library.watcher import LibraryWatcher
from outlawz.llm.base import LLM, llm_key, make_llm
from outlawz.obs.hub import Observability
from outlawz.paths import DataPaths, LibraryPaths
from outlawz.search.index import SearchIndex
from outlawz.search.rerank import Reranker, make_reranker
from outlawz.search.service import SearchOptions, SearchService
from outlawz.store.files import KeyedLocks, recover_dirs, remove_tree
from outlawz.store.lock import HEARTBEAT_SECONDS, InstanceLock
from outlawz.store.migrations import MigrationReport, ensure_format
from outlawz.store.safepath import UnsafePath, resolve_inside

log = logging.getLogger("outlawz")

Handler = Callable[..., None]


class Events:
    """A tiny in-process event hub, so modules react to each other without importing each other.

    Events: ``document_added``, ``document_changed``, ``document_moved``, ``document_removed``,
    ``document_ready`` and ``library_rebuilt``.
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
        self.embedder: Embedder | None = None
        self.queue: IngestQueue | None = None
        self.watcher: LibraryWatcher | None = None
        self.index = SearchIndex()
        self.search = SearchService(
            self.index, lambda: self.embedder, self.reranker, self.search_options
        )
        self._reranker: tuple[tuple[str, str], Reranker | None] | None = None
        self._chats: ChatStore | None = None
        self._preferences: PreferenceStore | None = None
        self._llm: tuple[tuple[Any, ...], LLM] | None = None
        self.llm_override: LLM | None = None  # tests and demos: use this model instead
        self.answering: set[str] = set()  # chats with an answer in progress
        # Answers numeric questions about a selected CSV with computed results.
        self.analyst = DataAnalyst(self)
        self.evals = EvalRunner(self)
        self.exports = ExportStore(self)
        self.last_maintenance: dict[str, Any] | None = None
        self.data_hook: Callable[..., Any] | None = self.analyst.chat_hook
        # Observability hooks: called with numbers only.
        self.on_search: Callable[[Any], None] | None = None
        self.on_answer: Callable[[ChatMessage], None] | None = None
        self.on_analysis: Callable[[Any], None] | None = None
        self.on_analysis_step: Callable[[str, float], None] | None = None
        self.on_request: Callable[[str, str, int, float], None] | None = None
        self.obs: Observability | None = None
        self.opened = False
        self._tasks: list[asyncio.Task[None]] = []
        self._closers: list[Callable[[], Any]] = []

    # ---------------------------------------------------------------- lifecycle

    def open(self) -> None:
        if self.opened:
            return
        self.data.ensure()
        self.lib.root.mkdir(parents=True, exist_ok=True)
        repair_interrupted_restore(self.lib.root)
        self.lib.app.mkdir(parents=True, exist_ok=True)
        self.lock.acquire()
        try:
            self.migration = ensure_format(self.lib.app, self.data.backups_dir)
            self.lib.ensure()
            recover_dirs(self.lib.docs_dir, self.lib.tmp_dir)
            self.documents = DocumentIndex(self.lib.documents_file)
            self.documents.requeue_unsettled()
            self._chats = ChatStore(self.lib.chats_dir)
            self._preferences = PreferenceStore(self.lib.preferences_file)
            self._attach_ingestion(self.documents)
            self._attach_search()
            self._attach_chat()
            self.obs = Observability(self)
            self.obs.attach()
        except BaseException:
            self.lock.release()
            raise
        self.opened = True

    def _attach_ingestion(self, docs: DocumentIndex) -> None:
        self.embedder = make_embedder(self.settings)
        self.queue = IngestQueue(
            docs,
            self.pipeline_context,
            workers=lambda: self.settings.workers,
            retries=lambda: self.settings.retries,
        )
        queue = self.queue
        self.watcher = LibraryWatcher(
            self.lib.root,
            docs,
            depth=lambda: self.settings.watch_depth,
            interval=lambda: self.settings.watch_interval,
            forget=self.forget_document,
            on_change=lambda report: queue.wake(),
            emit=self.events.emit,
        )

    def _attach_search(self) -> None:
        self.events.on("document_ready", self._index_document)
        self.events.on("document_removed", self._unindex_document)
        self.events.on("library_rebuilt", lambda **_: self.reset_index())
        self.load_index()

    def _attach_chat(self) -> None:
        chats = self.chats

        def forget(doc: Document, **_: Any) -> None:
            chats.forget_document(doc.id)
            self.analyst.frames.forget(doc.id)

        self.events.on("document_removed", forget)
        # The table cache is keyed by content hash too, so this only frees memory early.
        self.events.on(
            "document_changed",
            lambda doc_id="", **_: self.analyst.frames.forget(doc_id) if doc_id else None,
        )

    def llm(self) -> LLM:
        """The model provider for the current settings (rebuilt when they change)."""
        if self.llm_override is not None:
            return self.llm_override
        key = llm_key(self.settings)
        if self._llm is None or self._llm[0] != key:
            self._llm = (key, make_llm(self.settings))
        return self._llm[1]

    def apply_settings(self, new: Settings) -> list[str]:
        """Switch to new settings while running. Returns plain-language notes about what follows
        (for example, documents being read again for a new search model)."""
        old = self.settings
        self.settings = new
        notes: list[str] = []
        if embedder_id(old) != embedder_id(new):
            self.embedder = make_embedder(new)
            self.load_index()
            notes.append(
                "The search model changed, so every document is being prepared again. Search "
                "fills back in as each one finishes."
            )
        if self.queue is not None and self.queue.reconcile() and not notes:
            notes.append("Reading settings changed, so affected documents are being read again.")
        return notes

    def reset_index(self) -> None:
        self.index.clear()
        if self.embedder is not None:
            self.index.model_id = self.embedder.model_id

    def load_index(self) -> int:
        """Rebuild the in-memory index from the stored passages and vectors."""
        self.reset_index()
        loaded = 0
        model = self.embedder.model_id if self.embedder else None
        for doc in self.docs.all():
            if doc.status != Status.ready or doc.embed_model != model:
                continue
            folder = self.lib.doc_dir(doc.id)
            passages = read_passages(folder)
            vectors = read_vectors(folder)
            if vectors is None or len(vectors) != len(passages):
                self.docs.update(
                    doc.id,
                    status=Status.queued,
                    lazy=True,
                    progress=Progress(message="Search data was missing: reading again"),
                )
                continue
            if self.index.add(doc.id, passages, vectors, model):
                loaded += 1
        self.docs.flush()
        return loaded

    def _index_document(
        self, doc: Document, passages: list[dict[str, Any]], vectors: Any, **_: Any
    ) -> None:
        self.index.add(doc.id, passages, vectors, doc.embed_model)

    def _unindex_document(self, doc: Document, **_: Any) -> None:
        self.index.remove(doc.id)

    def search_options(self) -> SearchOptions:
        s = self.settings
        return SearchOptions(
            vector_k=s.vector_k,
            keyword_k=s.keyword_k,
            rerank_k=s.rerank_k,
            rrf_k=s.rrf_k,
            vector_weight=s.vector_weight,
            keyword_weight=s.keyword_weight,
        )

    def reranker(self) -> Reranker | None:
        key = (self.settings.reranker, self.settings.rerank_model)
        if self._reranker is None or self._reranker[0] != key:
            self._reranker = (key, make_reranker(key[0], key[1], self.data.models_dir))
        return self._reranker[1]

    def pipeline_context(self) -> PipelineContext:
        s = self.settings
        if self.embedder is None:
            raise RuntimeError("The library isn't open")
        return PipelineContext(
            lib=self.lib,
            docs=self.docs,
            embedder=self.embedder,
            chunking=ChunkSettings(
                target=s.chunk_tokens, overlap=s.chunk_overlap, minimum=s.chunk_min
            ),
            options=ExtractOptions(
                ocr=s.ocr == "auto", csv_rows=s.csv_rows, target_tokens=s.chunk_tokens
            ),
            on_ready=self._on_ready,
        )

    def _on_ready(self, doc: Document, passages: list[dict[str, Any]], vectors: Any) -> None:
        self.events.emit("document_ready", doc=doc, passages=passages, vectors=vectors)

    def start_background(
        self, *, watch: bool = True, ingest: bool = True, maintain: bool = True
    ) -> None:
        self.spawn(self._heartbeat_loop(), "heartbeat")
        self.spawn(self._flush_loop(), "flush")
        if self.obs is not None:
            self.spawn(self.obs.run(), "observability")
        if maintain:
            from outlawz import maintenance

            self.spawn(maintenance.loop(self), "maintenance")
        if ingest and self.queue is not None:
            self.spawn(self.queue.run(), "queue")
        if watch and self.watcher is not None:
            self.spawn(self.watcher.run(), "watcher")

    def spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        self._tasks.append(asyncio.create_task(coro, name=f"outlawz:{name}"))

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
        if self.obs is not None:
            self.obs.flush()
        self.lock.release()
        self.opened = False

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not await asyncio.to_thread(self.lock.heartbeat):
                log.warning("Another copy of Outlawz took over this library's lock.")

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

    @property
    def chats(self) -> ChatStore:
        if self._chats is None:
            raise RuntimeError("The library isn't open")
        return self._chats

    @property
    def preferences(self) -> PreferenceStore:
        if self._preferences is None:
            raise RuntimeError("The library isn't open")
        return self._preferences

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

    def forget_document(self, doc_id: str, reason: str = "file_gone") -> Document | None:
        """Remove a document from the index and delete its processed data (not the file)."""
        if self.queue is not None and self.queue.is_running(doc_id):
            self.queue.cancel(doc_id)
        doc = self.docs.remove(doc_id)
        remove_tree(self.lib.doc_dir(doc_id))
        if doc is not None:
            self.events.emit("document_removed", doc=doc, reason=reason)
        return doc

    def delete_document(self, doc_id: str) -> Document:
        """Delete in the app: the file goes to Trash, its processed data is removed."""
        doc = self.require_document(doc_id)
        path = self.document_file(doc)
        if path.exists():
            move_to_trash(path, self.data.trash_dir, original=doc.path)
        self.forget_document(doc_id, reason="deleted")
        return doc
