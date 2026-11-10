"""Paths stay inside the library.

Every path that comes from a request, a file name or an archive goes through
:func:`resolve_inside` before it touches the disk. It rejects ``..``, absolute paths, drive
letters, UNC paths, NUL bytes, and symlinks that point outside the root (zip-slip included).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from sidelamp.errors import AppError

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')


class UnsafePath(AppError):
    def __init__(self, message: str = "That path isn't inside the library.") -> None:
        super().__init__("unsafe_path", message, 400)


def check_relative(rel: str) -> PurePosixPath:
    """Validate a relative path (``/`` or ``\\`` separators) without touching the disk."""
    if not rel or "\x00" in rel:
        raise UnsafePath()
    normalised = rel.replace("\\", "/")
    if PurePosixPath(normalised).is_absolute() or PureWindowsPath(rel).is_absolute():
        raise UnsafePath()
    if PureWindowsPath(rel).drive or normalised.startswith("//"):
        raise UnsafePath()
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise UnsafePath()
    return PurePosixPath(*parts)


def resolve_inside(root: Path, rel: str | PurePosixPath) -> Path:
    """Return the real path of ``root/rel``, refusing anything that ends up outside ``root``
    (including through symlinks)."""
    clean = check_relative(str(rel))
    root_real = root.resolve()
    candidate = (root_real / Path(*clean.parts)).resolve()
    if not is_inside(root_real, candidate):
        raise UnsafePath()
    return candidate


def is_inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def safe_name(filename: str, *, max_length: int = 180) -> str:
    """A file name that is safe on macOS, Windows and Linux. Keeps the user's name where it can."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = _BAD_CHARS.sub("_", base).strip().lstrip(".").strip()
    base = base.rstrip(". ")
    if not base:
        base = "document"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    if len(stem) + len(ext) + 1 > max_length:
        stem = stem[: max(1, max_length - len(ext) - 1)].rstrip()
    return f"{stem}.{ext}" if ext else stem


def to_relative(root: Path, path: Path) -> str:
    """The library-relative POSIX path of a file inside the library."""
    return path.resolve().relative_to(root.resolve()).as_posix()
