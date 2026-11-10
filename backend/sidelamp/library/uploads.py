"""Uploads: check what a file really is, then save it in the library under its own name.

The extension must match the content: each file type's extractor sniffs the bytes, and a file
that more than one type claims (a polyglot) is refused. Then the extractor's own deeper check runs
(a PDF must open, an EPUB must be a valid book, a CSV must parse).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sidelamp.errors import AppError
from sidelamp.ingest.extractors import Extractor, all_extractors, extractor_for
from sidelamp.store.safepath import resolve_inside, safe_name

CHUNK = 1024 * 1024


class UploadRefused(AppError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(code, message, status)


@dataclass
class SavedUpload:
    path: Path
    rel: str
    sha256: str
    size: int
    type: str


def unique_path(path: Path) -> Path:
    """``report.pdf`` → ``report (2).pdf`` → ``report (3).pdf`` … whichever is free."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _article(label: str) -> str:
    return f"an {label}" if label[:1].lower() in "aeiou" else f"a {label}"


def validate_content(path: Path, expected: Extractor) -> None:
    claims = [e.type for e in all_extractors() if e.sniff(path)]
    if expected.type not in claims or len(claims) > 1:
        raise UploadRefused(
            "content_mismatch",
            f"This file is named like {_article(expected.label)}, but its contents aren't. "
            f"It wasn't added.",
            415,
        )
    try:
        expected.validate(path)
    except AppError as exc:
        raise UploadRefused(
            exc.code, exc.message, 415 if exc.code == "content_mismatch" else 400
        ) from None


def supported_message() -> str:
    labels = sorted({e.label for e in all_extractors()})
    return (
        f"Sidelamp reads {', '.join(labels[:-1])} and {labels[-1]} files. This file type "
        f"isn't supported yet."
        if len(labels) > 1
        else "This file type isn't supported."
    )


def save_stream(
    source: BinaryIO,
    filename: str,
    library_root: Path,
    staging_dir: Path,
    *,
    max_bytes: int,
    folder: str = "",
) -> SavedUpload:
    """Copy an upload into the library: size limit, hash, content check, collision-free name."""
    name = safe_name(filename)
    extractor = extractor_for(name)
    if extractor is None:
        raise UploadRefused("unsupported_type", supported_message(), 415)
    target_dir = library_root
    if folder:
        target_dir = resolve_inside(library_root, folder)
        if not target_dir.is_dir():
            raise UploadRefused("not_found", "That folder isn't in the library.", 404)

    staging_dir.mkdir(parents=True, exist_ok=True)
    tmp = staging_dir / f"upload-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                block = source.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    limit_mb = max_bytes // (1024 * 1024)
                    raise UploadRefused(
                        "too_large",
                        f"This file is larger than the {limit_mb} MB limit. You can raise the "
                        f"limit in Settings, or copy the file into the library folder directly.",
                        413,
                    )
                digest.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if size == 0:
            raise UploadRefused("empty_file", "This file is empty. It wasn't added.")
        validate_content(tmp, extractor)
        target = _claim(target_dir / name)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    rel = target.resolve().relative_to(library_root.resolve()).as_posix()
    return SavedUpload(target, rel, digest.hexdigest(), size, extractor.type)


def _claim(path: Path) -> Path:
    """Reserve a free name atomically (``O_EXCL``), so two uploads can't take the same one."""
    for _ in range(1000):
        candidate = unique_path(path)
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise UploadRefused("conflict", "Couldn't find a free file name.", 409)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()
