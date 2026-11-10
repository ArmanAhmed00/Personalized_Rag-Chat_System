"""Uploads: check what a file really is, then save it in the library under its own name.

The extension must match the content. A ``.pdf`` must start like a PDF and open as one; an
``.epub`` must be a valid EPUB zip; a ``.csv`` must be text. Files that look like two formats at
once (polyglots) are refused.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sidelamp.errors import AppError
from sidelamp.library.documents import DocType, doc_type_for
from sidelamp.store.safepath import check_relative, resolve_inside, safe_name

CHUNK = 1024 * 1024

_BINARY_MAGIC = (
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"\x7fELF",
    b"MZ",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
    b"\xca\xfe\xba\xbe",
    b"\xcf\xfa\xed\xfe",
    b"\x1f\x8b",
    b"7z\xbc\xaf",
    b"Rar!",
    b"\xd0\xcf\x11\xe0",
)


class UploadRefused(AppError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(code, message, status)


@dataclass
class SavedUpload:
    path: Path
    rel: str
    sha256: str
    size: int
    type: DocType


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


def detect_type(path: Path) -> DocType | None:
    """What the bytes say the file is (``None`` if unknown or ambiguous)."""
    with open(path, "rb") as fh:
        head = fh.read(4096)
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 65_536))
        tail = fh.read()
    pdf = b"%PDF-" in head[:1024]
    zip_start = head.startswith(b"PK\x03\x04")
    zip_end = b"PK\x05\x06" in tail
    if pdf and (zip_start or zip_end):
        return None  # PDF + ZIP polyglot
    if pdf:
        return DocType.pdf
    if zip_start:
        return DocType.epub if _is_epub_zip(path) else None
    if _looks_like_text(head):
        return DocType.csv
    return None


def _is_epub_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not names or names[0] != "mimetype":
                return False
            if zf.read("mimetype").strip() != b"application/epub+zip":
                return False
            if "META-INF/container.xml" not in names:
                return False
            for name in names:
                try:
                    check_relative(name)
                except AppError:
                    return False  # zip-slip style entry
            return True
    except (zipfile.BadZipFile, OSError, KeyError):
        return False


def _looks_like_text(head: bytes) -> bool:
    if not head or b"\x00" in head:
        return False
    if any(head.startswith(m) for m in _BINARY_MAGIC):
        return False
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            head.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def validate_content(path: Path, expected: DocType) -> None:
    """Deep check that the file opens as what it claims to be."""
    found = detect_type(path)
    names = {DocType.pdf: "a PDF", DocType.epub: "an EPUB ebook", DocType.csv: "a CSV file"}
    if found != expected:
        raise UploadRefused(
            "content_mismatch",
            f"This file is named like {names[expected]}, but its contents aren't. It wasn't added.",
            415,
        )
    if expected == DocType.pdf:
        _check_pdf(path)
    elif expected == DocType.csv:
        _check_csv(path)


def _check_pdf(path: Path) -> None:
    import pymupdf

    try:
        with pymupdf.open(path) as pdf:
            if pdf.page_count < 1:
                raise UploadRefused("damaged_file", "This PDF has no pages. It wasn't added.")
            if pdf.needs_pass:
                raise UploadRefused(
                    "password_protected",
                    "This PDF is password-protected. Remove the password and add it again.",
                )
    except UploadRefused:
        raise
    except Exception:
        raise UploadRefused(
            "damaged_file", "This PDF can't be opened. It may be damaged."
        ) from None


def _check_csv(path: Path) -> None:
    with open(path, "rb") as fh:
        sample = fh.read(256 * 1024)
    if b"\x00" in sample:
        raise UploadRefused("content_mismatch", "This file isn't a text CSV. It wasn't added.", 415)
    text = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None or not text.strip():
        raise UploadRefused("empty_file", "This CSV file is empty. It wasn't added.")


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
    kind = doc_type_for(name)
    if kind is None:
        raise UploadRefused(
            "unsupported_type",
            "Sidelamp reads PDF, EPUB and CSV files. This file type isn't supported yet.",
            415,
        )
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
        validate_content(tmp, kind)
        target = _claim(target_dir / name)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    rel = target.resolve().relative_to(library_root.resolve()).as_posix()
    return SavedUpload(target, rel, digest.hexdigest(), size, kind)


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
