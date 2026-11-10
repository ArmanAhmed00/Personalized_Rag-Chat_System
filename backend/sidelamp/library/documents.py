"""The document index (``documents.json``). It is also the processing queue.

Each record says where a document is, what its content hash is and how far processing has got.
A document whose status is not ``ready`` or ``failed`` is waiting to be processed; on startup all
of those are queued again, so no separate queue store is needed.

The index lives in memory and is written to disk atomically. Progress updates arrive many times a
second while a document is being read, so those are written lazily (``flush``) while status
changes are written at once.
"""

from __future__ import annotations

import threading
import time
import uuid
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from sidelamp.store.files import read_json, write_json


class DocType(StrEnum):
    pdf = "pdf"
    epub = "epub"
    csv = "csv"


EXTENSIONS: dict[str, DocType] = {".pdf": DocType.pdf, ".epub": DocType.epub, ".csv": DocType.csv}


class Status(StrEnum):
    queued = "queued"
    reading = "reading"
    chunking = "chunking"
    embedding = "embedding"
    ready = "ready"
    failed = "failed"

    @property
    def settled(self) -> bool:
        return self in (Status.ready, Status.failed)


class Progress(BaseModel):
    step: str = ""
    done: int = 0
    total: int = 0
    message: str = ""


class DocErrorInfo(BaseModel):
    code: str
    message: str


class Document(BaseModel):
    id: str
    path: str  # relative to the library root, POSIX separators
    sha256: str
    size: int
    mtime: float
    type: DocType
    status: Status = Status.queued
    progress: Progress = Field(default_factory=Progress)
    error: DocErrorInfo | None = None
    embed_model: str | None = None
    chunks: int = 0
    pages: int | None = None
    attempts: int = 0
    added_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    ready_at: float | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def folder(self) -> str:
        parent = PurePosixPath(self.path).parent.as_posix()
        return "" if parent == "." else parent


def new_doc_id() -> str:
    return uuid.uuid4().hex[:12]


def doc_type_for(path: str | Path) -> DocType | None:
    return EXTENSIONS.get(PurePosixPath(str(path)).suffix.lower())


class DocumentIndex:
    """Thread-safe: ingestion workers update it from a thread pool while the API reads it."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self._docs: dict[str, Document] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self.version = 0  # bumps on every change, so pollers can tell if anything moved
        self.load()

    # ---------------------------------------------------------------- persistence

    def load(self) -> None:
        raw = read_json(self.file, {"documents": []})
        docs: dict[str, Document] = {}
        for item in raw.get("documents", []) if isinstance(raw, dict) else []:
            try:
                doc = Document.model_validate(item)
            except ValidationError:
                continue
            docs[doc.id] = doc
        with self._lock:
            self._docs = docs
            self._dirty = False

    def save(self) -> None:
        with self._lock:
            payload = {
                "documents": [d.model_dump(mode="json") for d in self._docs.values()],
            }
            write_json(self.file, payload)
            self._dirty = False

    def flush(self) -> None:
        """Write pending lazy updates, if any."""
        if self._dirty:
            self.save()

    # ---------------------------------------------------------------- reads

    def all(self) -> list[Document]:
        with self._lock:
            return [d.model_copy() for d in self._docs.values()]

    def get(self, doc_id: str) -> Document | None:
        with self._lock:
            doc = self._docs.get(doc_id)
            return doc.model_copy() if doc else None

    def by_path(self, path: str) -> Document | None:
        with self._lock:
            for doc in self._docs.values():
                if doc.path == path:
                    return doc.model_copy()
        return None

    def by_sha(self, sha256: str) -> list[Document]:
        with self._lock:
            return [d.model_copy() for d in self._docs.values() if d.sha256 == sha256]

    def pending(self) -> list[Document]:
        """Documents waiting to be processed, oldest first."""
        with self._lock:
            docs = [d.model_copy() for d in self._docs.values() if not d.status.settled]
        return sorted(docs, key=lambda d: d.updated_at)

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)

    # ---------------------------------------------------------------- writes

    def add(self, doc: Document) -> Document:
        with self._lock:
            self._docs[doc.id] = doc
            self.version += 1
            self.save()
        return doc.model_copy()

    def update(self, doc_id: str, *, lazy: bool = False, **changes: Any) -> Document | None:
        """Change fields of a document. ``lazy`` skips the immediate write (progress ticks)."""
        with self._lock:
            doc = self._docs.get(doc_id)
            if doc is None:
                return None
            data = doc.model_dump()
            data.update(changes)
            data["updated_at"] = time.time()
            updated = Document.model_validate(data)
            self._docs[doc_id] = updated
            self.version += 1
            if lazy:
                self._dirty = True
            else:
                self.save()
            return updated.model_copy()

    def remove(self, doc_id: str) -> Document | None:
        with self._lock:
            doc = self._docs.pop(doc_id, None)
            if doc is not None:
                self.version += 1
                self.save()
            return doc

    def requeue_unsettled(self) -> int:
        """On startup: anything that was mid-processing goes back in the queue."""
        count = 0
        with self._lock:
            for doc_id, doc in list(self._docs.items()):
                if doc.status.settled or doc.status == Status.queued:
                    continue
                data = doc.model_dump()
                data.update(status=Status.queued, progress=Progress(message="Waiting to be read"))
                self._docs[doc_id] = Document.model_validate(data)
                count += 1
            if count:
                self.version += 1
                self.save()
        return count
