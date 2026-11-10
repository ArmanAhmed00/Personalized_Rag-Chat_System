"""The library watcher: polls the library folder with ``os.scandir`` every few seconds.

Polling behaves the same on every OS, on network drives and in Docker volumes, where file-change
notifications are unreliable.

* New files are queued; changed files (size or modification time, then content hash) are read
  again.
* Renamed or moved files are matched by content hash and keep their id (and their chats).
* Files deleted outside the app leave search. A file must be missing on two scans in a row, so a
  move that takes a moment isn't mistaken for a delete. If the library folder itself disappears
  (an unplugged drive), the watcher pauses instead.
* Skipped: the app folder, hidden files and folders, partial downloads and temp files, system
  folders, unsupported types, symlinks that leave the library, and anything deeper than
  ``watch_depth`` levels.
* A file still being written waits: it's only read once its size and modification time are the
  same on two scans in a row (files already there when the app starts are read at once).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sidelamp.ingest.extractors import extractor_for
from sidelamp.library.documents import Document, DocumentIndex, Progress, Status, new_doc_id
from sidelamp.library.uploads import sha256_file
from sidelamp.paths import APP_DIR_NAME
from sidelamp.store.safepath import is_inside

log = logging.getLogger("sidelamp")

SETTLE_SECONDS = 2.0
SKIP_DIRS = frozenset(
    {
        APP_DIR_NAME,
        "__MACOSX",
        "$RECYCLE.BIN",
        "System Volume Information",
        "node_modules",
        "@eaDir",
        "#recycle",
        "#snapshot",
        "lost+found",
    }
)
TEMP_SUFFIXES = (
    ".part",
    ".partial",
    ".crdownload",
    ".download",
    ".tmp",
    ".temp",
    ".swp",
    ".icloud",
    ".opdownload",
    ".!ut",
    ".aria2",
)
SKIP_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})


@dataclass
class ScanReport:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    waiting: int = 0
    paused: bool = False

    @property
    def any(self) -> bool:
        return bool(self.added or self.changed or self.moved or self.removed)


def skip_name(name: str, *, is_dir: bool) -> bool:
    lowered = name.lower()
    if name.startswith((".", "~$")) or lowered in SKIP_FILES:
        return True
    if is_dir:
        return name in SKIP_DIRS or name.startswith(".Trash")
    return lowered.endswith(TEMP_SUFFIXES) or extractor_for(name) is None


def scan_tree(root: Path, max_depth: int) -> dict[str, tuple[int, float]]:
    """``{relative path: (size, mtime)}`` for every candidate file under ``root``."""
    found: dict[str, tuple[int, float]] = {}
    root_real = root.resolve()
    stack: list[tuple[Path, str, int]] = [(root, "", 0)]
    while stack:
        folder, prefix, depth = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                is_link = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if skip_name(entry.name, is_dir=is_dir or (is_link and entry.is_dir())):
                continue
            rel = f"{prefix}{entry.name}"
            if is_dir:
                if depth + 1 < max_depth:
                    stack.append((Path(entry.path), f"{rel}/", depth + 1))
                continue
            if is_link:
                # Follow links to files only, and only if they stay inside the library.
                target = Path(entry.path)
                if target.is_dir() or not is_inside(root_real, target):
                    continue
            try:
                stat = entry.stat(follow_symlinks=True)
            except OSError:
                continue
            if not entry.is_file(follow_symlinks=True):
                continue
            found[rel] = (stat.st_size, stat.st_mtime)
    return found


class LibraryWatcher:
    def __init__(
        self,
        root: Path,
        docs: DocumentIndex,
        *,
        depth: Callable[[], int],
        interval: Callable[[], float],
        forget: Callable[[str], object],
        on_change: Callable[[ScanReport], None] | None = None,
        emit: Callable[..., None] | None = None,
    ) -> None:
        self.root = root
        self.docs = docs
        self.depth = depth
        self.interval = interval
        self.forget = forget
        self.on_change = on_change
        self.emit = emit or (lambda *a, **k: None)
        self._missing_since: dict[str, int] = {}
        self._previous: dict[str, tuple[int, float]] = {}
        self.scans = 0
        self.last_scan: float | None = None

    def scan_once(self, *, now: float | None = None) -> ScanReport:
        report = ScanReport()
        now = time.time() if now is None else now
        if not self.root.is_dir():
            report.paused = True
            return report
        self.scans += 1
        found = scan_tree(self.root, self.depth())
        docs = self.docs.all()
        by_path = {d.path: d for d in docs}
        first_scan = self.scans == 1
        previous = self._previous

        def settled(rel: str) -> bool:
            size, mtime = found[rel]
            if now - mtime < SETTLE_SECONDS:
                return False
            return first_scan or previous.get(rel) == (size, mtime)

        # Changed files: size or mtime differ, then confirm by content hash.
        for rel, (size, mtime) in found.items():
            doc = by_path.get(rel)
            if doc is None or (doc.size == size and abs(doc.mtime - mtime) < 1e-6):
                continue
            if not settled(rel):
                report.waiting += 1
                continue
            sha = self._hash(rel)
            if sha is None:
                continue
            if sha == doc.sha256:
                self.docs.update(doc.id, lazy=True, size=size, mtime=mtime)
                continue
            self.docs.update(
                doc.id,
                sha256=sha,
                size=size,
                mtime=mtime,
                status=Status.queued,
                error=None,
                attempts=0,
                progress=Progress(message="The file changed: reading again"),
            )
            report.changed.append(doc.id)
            self.emit("document_changed", doc_id=doc.id)

        missing = {d.path: d for d in docs if d.path not in found}
        for path in list(self._missing_since):
            if path not in missing:
                del self._missing_since[path]

        # New paths: a move/rename if the content matches a missing document, else a new one.
        # A file with the same size as a missing document is hashed at once: if the content
        # matches a known, complete file it's a move, and there's nothing to wait for.
        missing_sizes = {d.size for d in missing.values()}
        maybe_moved: set[int] = set()
        for rel in sorted(set(found) - set(by_path)):
            size, mtime = found[rel]
            ready = settled(rel)
            if not ready and size not in missing_sizes:
                report.waiting += 1
                continue
            sha = self._hash(rel)
            if sha is None:
                continue
            match = next((d for d in missing.values() if d.sha256 == sha), None)
            if match is None and not ready:
                maybe_moved.add(size)
                report.waiting += 1
                continue
            if match is not None:
                self.docs.update(match.id, path=rel, size=size, mtime=mtime)
                missing.pop(match.path, None)
                self._missing_since.pop(match.path, None)
                report.moved.append(match.id)
                self.emit("document_moved", doc_id=match.id)
                continue
            extractor = extractor_for(rel)
            if extractor is None:
                continue
            doc = Document(
                id=new_doc_id(),
                path=rel,
                sha256=sha,
                size=size,
                mtime=mtime,
                type=extractor.type,
                progress=Progress(message="Waiting to be read"),
            )
            self.docs.add(doc)
            report.added.append(doc.id)
            self.emit("document_added", doc=doc)

        # Deleted outside the app: only after being missing on two scans in a row.
        for path, doc in missing.items():
            first_seen = self._missing_since.setdefault(path, self.scans)
            if doc.size in maybe_moved:
                continue  # a file of the same size is still arriving; decide next scan
            if self.scans - first_seen >= 1:
                self.forget(doc.id)
                self._missing_since.pop(path, None)
                report.removed.append(doc.id)

        self._previous = found
        self.docs.flush()
        self.last_scan = now
        if report.any and self.on_change is not None:
            self.on_change(report)
        return report

    def _hash(self, rel: str) -> str | None:
        try:
            return sha256_file(self.root / rel)
        except OSError:
            return None  # locked or vanished; try again next scan

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.scan_once)
            except Exception:
                log.exception("Library scan failed")
            await asyncio.sleep(self.interval())


def document_for(docs: list[Document], rel: str) -> Document | None:
    return next((d for d in docs if d.path == rel), None)
