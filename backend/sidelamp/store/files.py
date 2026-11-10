"""Crash-safe plain-file storage. There is no database: these functions are the whole store.

Rules:

* A JSON write goes to a temp file in the same folder, is flushed and ``fsync``-ed, then
  atomically renamed over the target. A crash leaves either the old file or the new one.
* JSONL logs are append-only. A half-written last line (crash mid-append) is ignored on read,
  and the next append starts on a fresh line so it can't be glued to the broken one.
* A folder of output is built in a temp folder and moved into place only when complete.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_MISSING: Any = object()
_IS_WINDOWS = os.name == "nt"


def fsync_dir(path: Path) -> None:
    """Make a rename durable. Not possible (or needed) on Windows."""
    if _IS_WINDOWS:
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace(src: Path, dst: Path) -> None:
    """``os.replace`` with a short retry: on Windows, antivirus or indexers briefly lock files."""
    attempts = 5 if _IS_WINDOWS else 1
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _temp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(path)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    fsync_dir(path.parent)


def write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    """Atomically write ``data`` as JSON: temp file, fsync, rename."""
    text = json.dumps(data, ensure_ascii=False, indent=indent, default=_json_default)
    write_bytes_atomic(path, (text + "\n").encode("utf-8"))


def read_json(path: Path, default: Any = _MISSING) -> Any:
    """Read a JSON file. Returns ``default`` if it is missing (or raises if no default)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if default is _MISSING:
            raise
        return default


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    if isinstance(value, set | frozenset):
        return sorted(value)
    raise TypeError(f"Can't store {type(value).__name__} as JSON")


def dumps_line(record: Any) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def append_jsonl(path: Path, record: Any, *, sync: bool = False) -> None:
    """Append one record as a line. If the file ends mid-line (an earlier crash), start a new line
    first so the new record is never glued onto the broken one."""
    append_jsonl_many(path, [record], sync=sync)


def append_jsonl_many(path: Path, records: list[Any], *, sync: bool = False) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(dumps_line(r) + "\n" for r in records).encode("utf-8")
    with open(path, "ab+") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() > 0:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.seek(0, os.SEEK_END)
                fh.write(b"\n")
        fh.seek(0, os.SEEK_END)
        fh.write(payload)
        fh.flush()
        if sync:
            os.fsync(fh.fileno())


def iter_jsonl(path: Path) -> Iterator[Any]:
    """Yield each record. Lines that don't parse (a half-written line from a crash) are skipped."""
    try:
        fh = open(path, "rb")  # noqa: SIM115 - closed below; a generator needs the handle open
    except FileNotFoundError:
        return
    with fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue


def read_jsonl(path: Path) -> list[Any]:
    return list(iter_jsonl(path))


def write_jsonl(path: Path, records: list[Any]) -> None:
    """Atomically replace a whole JSONL file."""
    data = "".join(dumps_line(r) + "\n" for r in records).encode("utf-8")
    write_bytes_atomic(path, data)


# ---------------------------------------------------------------- atomic folders

_OLD_MARK = ".old-"
_DEL_MARK = ".del-"


@contextlib.contextmanager
def atomic_dir(final: Path, staging_root: Path) -> Iterator[Path]:
    """Build a folder in ``staging_root`` and move it to ``final`` only if the block succeeds.

    ``staging_root`` must be on the same disk as ``final`` so the move is a rename. If ``final``
    already exists it is renamed aside first, then deleted after the swap. A crash between the
    two renames is repaired by :func:`recover_dirs`.
    """
    staging_root.mkdir(parents=True, exist_ok=True)
    tmp = staging_root / f"{final.name}.{uuid.uuid4().hex[:12]}.partial"
    tmp.mkdir()
    try:
        yield tmp
        _fsync_tree(tmp)
        final.parent.mkdir(parents=True, exist_ok=True)
        old: Path | None = None
        if final.exists():
            old = final.with_name(f"{final.name}{_OLD_MARK}{uuid.uuid4().hex[:8]}")
            _replace(final, old)
        _replace(tmp, final)
        fsync_dir(final.parent)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _fsync_tree(folder: Path) -> None:
    for item in folder.rglob("*"):
        if item.is_file():
            try:
                with open(item, "rb") as fh:
                    os.fsync(fh.fileno())
            except OSError:
                pass
    fsync_dir(folder)


def recover_dirs(parent: Path, staging_root: Path | None = None) -> int:
    """Repair what a crash during :func:`atomic_dir` can leave behind. Returns repairs made.

    * ``name.old-xxxx`` with no ``name``: the swap was interrupted, so put the old one back.
    * ``name.old-xxxx`` next to ``name``: the swap finished, so delete the old one.
    * Anything in ``staging_root`` is an unfinished build and is deleted.
    """
    repairs = 0
    if parent.exists():
        for item in parent.iterdir():
            if _DEL_MARK in item.name and item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
                repairs += 1
                continue
            if _OLD_MARK not in item.name or not item.is_dir():
                continue
            original = parent / item.name.split(_OLD_MARK, 1)[0]
            if original.exists():
                shutil.rmtree(item, ignore_errors=True)
            else:
                _replace(item, original)
            repairs += 1
    if staging_root is not None and staging_root.exists():
        for item in staging_root.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    item.unlink()
            repairs += 1
    return repairs


def remove_tree(path: Path) -> None:
    """Delete a folder by renaming it aside first, so a crash never leaves it half-deleted.
    A leftover ``.del-`` folder is finished off by :func:`recover_dirs`."""
    if not path.exists():
        return
    doomed = path.with_name(f"{path.name}{_DEL_MARK}{uuid.uuid4().hex[:8]}")
    _replace(path, doomed)
    shutil.rmtree(doomed, ignore_errors=True)


# ---------------------------------------------------------------- locks


class KeyedLocks:
    """One ``asyncio.Lock`` per key (usually a file path), so concurrent requests that update the
    same file take turns instead of interleaving their read-modify-write cycles."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, key: str | Path) -> asyncio.Lock:
        name = str(key)
        lock = self._locks.get(name)
        if lock is None:
            lock = self._locks[name] = asyncio.Lock()
        return lock


def file_mode_private(path: Path) -> None:
    """Make a file readable only by its owner (secrets). Best effort; no-op on Windows."""
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
