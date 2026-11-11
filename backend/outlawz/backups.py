"""Backups: create, verify and restore.

A backup is a ``.tar.gz`` of the app folder (optionally with the documents) whose first member is
``manifest.json``: a SHA-256 and size for every file. Verifying reads the whole archive and
checks every file against the manifest, and refuses anything unexpected (paths that leave the
folder, links, extra or missing files). Restore verifies first, so tampering is caught before
anything is written, then builds the restored folder next to the library and swaps it in.

Vectors can be left out for a smaller backup; documents without them are read again after a
restore.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import time
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from outlawz import APP_NAME, __version__
from outlawz.errors import AppError
from outlawz.paths import APP_DIR_NAME, LibraryPaths
from outlawz.store.files import fsync_dir, read_json
from outlawz.store.migrations import read_format
from outlawz.store.safepath import UnsafePath, check_relative, resolve_inside

KIND = "outlawz-backup"
MANIFEST = "manifest.json"
MAX_TOTAL = 64 * 1024**3  # refuse archives that claim more than 64 GB
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.tar\.gz$")
SKIP_APP = {"lock", "tmp"}
RESTORE_STAGING = ".outlawz-restore-"
RESTORE_ASIDE = ".outlawz.restore-old-"


class BackupError(AppError):
    def __init__(self, message: str, code: str = "backup_invalid") -> None:
        super().__init__(code, message, 422)


@dataclass
class VerifyReport:
    ok: bool
    files: int = 0
    bytes: int = 0
    problems: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        if self.ok:
            return f"The backup is complete and unchanged ({self.files:,} files)."
        return self.problems[0] if self.problems else "The backup can't be used."


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _app_files(lib: LibraryPaths, include_vectors: bool) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    if not lib.app.exists():
        return files
    for item in sorted(lib.app.rglob("*")):
        rel = item.relative_to(lib.app)
        if not item.is_file() or item.is_symlink():
            continue
        if rel.parts[0] in SKIP_APP or item.name.endswith((".tmp", ".partial")):
            continue
        if rel.parts[0] == "evals" and len(rel.parts) > 1 and rel.parts[1].startswith("bench-"):
            continue  # the benchmark is rebuilt on demand
        if ".old-" in str(rel) or ".del-" in str(rel):
            continue
        if not include_vectors and item.name == "vectors.npy":
            continue
        files.append((f"app/{rel.as_posix()}", item))
    return files


def _document_files(lib: LibraryPaths) -> list[tuple[str, Path]]:
    raw = read_json(lib.documents_file, {})
    docs = raw.get("documents", []) if isinstance(raw, dict) else []
    files = []
    for doc in docs:
        rel = doc.get("path") if isinstance(doc, dict) else None
        if not isinstance(rel, str):
            continue
        try:
            path = resolve_inside(lib.root, rel)
        except UnsafePath:
            continue
        if path.is_file():
            files.append((f"library/{PurePosixPath(*check_relative(rel).parts).as_posix()}", path))
    return files


def backup_name(prefix: str = "outlawz-backup") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.tar.gz"


def create_backup(
    library: Path,
    dest_dir: Path,
    *,
    include_documents: bool = False,
    include_vectors: bool = True,
    prefix: str = "outlawz-backup",
) -> Path:
    lib = LibraryPaths(library)
    entries = _app_files(lib, include_vectors)
    if include_documents:
        entries += _document_files(lib)
    blobs: list[tuple[str, bytes]] = []
    listing = []
    for arcname, path in entries:
        try:
            data = path.read_bytes()  # read once: the hash covers exactly what's stored
        except OSError:
            continue
        blobs.append((arcname, data))
        listing.append({"path": arcname, "sha256": _sha(data), "size": len(data)})
    manifest = {
        "kind": KIND,
        "version": 1,
        "app": APP_NAME,
        "app_version": __version__,
        "format": read_format(lib.app),
        "created": time.time(),
        "library_name": library.name,
        "includes_documents": include_documents,
        "includes_vectors": include_vectors,
        "files": listing,
    }
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / backup_name(prefix)
    partial = target.with_name(target.name + ".partial")
    try:
        with tarfile.open(partial, "w:gz") as tar:
            _add(tar, MANIFEST, json.dumps(manifest, indent=2).encode())
            for arcname, data in blobs:
                _add(tar, arcname, data)
        with open(partial, "rb") as fh:
            os.fsync(fh.fileno())
        partial.replace(target)
        fsync_dir(dest_dir)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _safe_member_name(name: str) -> bool:
    if name == MANIFEST:
        return True
    try:
        clean = check_relative(name)
    except UnsafePath:
        return False
    return (
        clean.as_posix() == name and clean.parts[0] in ("app", "library") and len(clean.parts) > 1
    )


def verify_backup(path: Path) -> VerifyReport:
    """Check every file against the manifest without writing anything."""
    report = VerifyReport(ok=False)
    try:
        tar = tarfile.open(path, "r:gz")  # noqa: SIM115 - closed by the with block below
    except (OSError, tarfile.TarError, EOFError):
        report.problems.append("This file isn't a readable Outlawz backup.")
        return report
    with tar:
        try:
            first = tar.next()
            if first is None or first.name != MANIFEST or not first.isfile():
                report.problems.append("The backup has no manifest, so it can't be checked.")
                return report
            handle = tar.extractfile(first)
            manifest = json.loads(handle.read(5 * 1024 * 1024) if handle else b"")
        except (tarfile.TarError, ValueError, OSError, EOFError):
            report.problems.append("The backup's manifest can't be read.")
            return report
        if not isinstance(manifest, dict) or manifest.get("kind") != KIND:
            report.problems.append("This isn't a Outlawz backup.")
            return report
        expected: dict[str, dict[str, Any]] = {}
        for item in manifest.get("files", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                expected[item["path"]] = item
        if sum(int(i.get("size", 0)) for i in expected.values()) > MAX_TOTAL:
            report.problems.append("The backup claims to be impossibly large.")
            return report
        seen: set[str] = set()
        try:
            for member in tar:
                if member.offset == first.offset:
                    continue  # the manifest, already read
                name = member.name
                if not member.isfile():
                    report.problems.append(
                        f"The backup contains a link or special file ({name[:80]}), "
                        "which isn't allowed."
                    )
                    return report
                if not _safe_member_name(name) or name == MANIFEST:
                    report.problems.append(
                        f"The backup contains a path that isn't allowed ({name[:80]})."
                    )
                    return report
                if name in seen:
                    report.problems.append(f"The backup contains {name[:80]} twice.")
                    return report
                seen.add(name)
                entry = expected.get(name)
                if entry is None:
                    report.problems.append(
                        f"The backup contains a file the manifest doesn't list ({name[:80]})."
                    )
                    return report
                handle = tar.extractfile(member)
                digest = hashlib.sha256()
                size = 0
                while handle is not None:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                if size != entry.get("size") or digest.hexdigest() != entry.get("sha256"):
                    report.problems.append(f"{name[:80]} was changed after the backup was made.")
                    return report
                report.files += 1
                report.bytes += size
        except (tarfile.TarError, OSError, EOFError, zlib.error):
            report.problems.append("The backup is damaged or incomplete.")
            return report
        missing = sorted(set(expected) - seen)
        if missing:
            report.problems.append(
                f"{len(missing)} files listed in the manifest are missing, "
                f"such as {missing[0][:80]}."
            )
            return report
    report.ok = True
    report.manifest = {k: v for k, v in manifest.items() if k != "files"}
    return report


def library_is_empty(library: Path) -> bool:
    lib = LibraryPaths(library)
    raw = read_json(lib.documents_file, {}) if lib.documents_file.exists() else {}
    if isinstance(raw, dict) and raw.get("documents"):
        return False
    if lib.chats_dir.exists() and any(lib.chats_dir.glob("*.json")):
        return False
    if library.exists():
        for item in library.iterdir():
            if item.name.startswith("."):
                continue
            return False
    return True


def restore_backup(
    path: Path, library: Path, *, replace: bool = False, safety_dir: Path | None = None
) -> VerifyReport:
    """Verify, then restore into ``library``. The library must not be open."""
    report = verify_backup(path)
    if not report.ok:
        raise BackupError(report.message)
    library.mkdir(parents=True, exist_ok=True)
    if not library_is_empty(library):
        if not replace:
            raise AppError(
                "library_not_empty",
                "This library already has documents or chats. Restoring replaces them; choose "
                "Replace to continue (a safety backup of the current state is made first).",
                409,
            )
        if safety_dir is not None:
            create_backup(library, safety_dir, prefix="pre-restore")
    staging = library / f"{RESTORE_STAGING}{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        _extract(path, staging, report)
        _swap_in(staging, library)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def _extract(path: Path, staging: Path, report: VerifyReport) -> None:
    """Write every file, hashing again as it goes (the archive could change after verifying)."""
    expected: dict[str, dict[str, Any]] = {}
    with tarfile.open(path, "r:gz") as tar:
        first = tar.next()
        handle = tar.extractfile(first) if first is not None else None
        manifest = json.loads(handle.read() if handle else b"{}")
        expected = {i["path"]: i for i in manifest.get("files", [])}
        for member in tar:
            if first is not None and member.offset == first.offset:
                continue  # the manifest
            if (
                not member.isfile()
                or not _safe_member_name(member.name)
                or member.name not in expected
            ):
                raise BackupError(
                    "The backup changed while it was being restored. Nothing was replaced."
                )
            target = resolve_inside(staging, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            data = source.read() if source else b""
            if _sha(data) != expected[member.name]["sha256"]:
                raise BackupError(
                    "The backup changed while it was being restored. Nothing was replaced."
                )
            with open(target, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())


def _swap_in(staging: Path, library: Path) -> None:
    app_now = library / APP_DIR_NAME
    app_new = staging / "app"
    aside = library / f"{RESTORE_ASIDE}{uuid.uuid4().hex[:8]}"
    if app_now.exists():
        app_now.replace(aside)
    try:
        if app_new.exists():
            app_new.replace(app_now)
        else:
            app_now.mkdir()
    except BaseException:
        if aside.exists() and not app_now.exists():
            aside.replace(app_now)
        raise
    fsync_dir(library)
    documents = staging / "library"
    if documents.exists():
        for item in sorted(documents.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(documents).as_posix()
            target = resolve_inside(library, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(item, target)
    shutil.rmtree(aside, ignore_errors=True)


def repair_interrupted_restore(library: Path) -> int:
    """Called when a library is opened: finish or undo a restore a crash interrupted."""
    if not library.exists():
        return 0
    repairs = 0
    app = library / APP_DIR_NAME
    for item in library.iterdir():
        if item.name.startswith(RESTORE_ASIDE) and item.is_dir():
            if app.exists():
                shutil.rmtree(item, ignore_errors=True)  # the swap finished
            else:
                item.replace(app)  # the swap didn't: put the old one back
            repairs += 1
        elif item.name.startswith(RESTORE_STAGING) and item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
            repairs += 1
    return repairs


# ---------------------------------------------------------------- the backups folder


def resolve_backup(backups_dir: Path, name: str) -> Path:
    if not _NAME.match(name):
        raise UnsafePath("That isn't a backup name.")
    path = resolve_inside(backups_dir, name)
    if not path.is_file():
        from outlawz.errors import NotFound

        raise NotFound("That backup")
    return path


def list_backups(backups_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not backups_dir.exists():
        return items
    for path in backups_dir.glob("*.tar.gz"):
        stat = path.stat()
        kind = (
            "scheduled"
            if path.name.startswith("outlawz-auto")
            else (
                "before upgrade"
                if path.name.startswith("pre-upgrade")
                else ("before restore" if path.name.startswith("pre-restore") else "manual")
            )
        )
        items.append(
            {"name": path.name, "size": stat.st_size, "created": stat.st_mtime, "kind": kind}
        )
    items.sort(key=lambda i: -float(str(i["created"])))
    return items


def prune_scheduled(backups_dir: Path, keep: int) -> int:
    auto = [b for b in list_backups(backups_dir) if b["kind"] == "scheduled"]
    removed = 0
    for item in auto[keep:]:
        with contextlib.suppress(OSError):
            (backups_dir / item["name"]).unlink()
            removed += 1
    return removed


def last_scheduled(backups_dir: Path) -> float | None:
    auto = [b for b in list_backups(backups_dir) if b["kind"] == "scheduled"]
    return float(str(auto[0]["created"])) if auto else None
