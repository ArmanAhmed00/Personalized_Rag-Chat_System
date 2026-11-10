"""The file-type extractor interface and its registry.

To support a new file type (say DOCX), add one module to this package that defines a class with
the attributes and methods below and an ``EXTRACTOR = MyExtractor()`` instance. Modules here are
discovered automatically. Add one test with a small sample file. That's all: uploads, the
watcher, the chunker and search pick it up from the registry.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# done, total, message ("Reading page 12 of 80")
ProgressFn = Callable[[int, int, str], None]


class Cancelled(Exception):
    """Processing was cancelled by the user."""


@dataclass
class Block:
    """One piece of a document as the extractor found it, before chunking."""

    text: str
    kind: str = "text"  # text | heading | table | schema | profile | rows
    page: int | None = None  # 1-based
    section: str | None = None
    chapter: str | None = None
    rows: tuple[int, int] | None = None  # CSV data rows, 1-based inclusive
    # Text used for keyword search when it should differ from the shown text (CSV: values only).
    index_text: str | None = None


@dataclass
class Extracted:
    blocks: list[Block]
    pages: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractOptions:
    ocr: bool = True
    csv_rows: int = 5000
    target_tokens: int = 350


@runtime_checkable
class Extractor(Protocol):
    type: str  # short id stored in the document index, e.g. "pdf"
    label: str  # shown to people, e.g. "PDF"
    extensions: tuple[str, ...]  # lower-case, with the dot
    media_type: str  # served when the original file is downloaded
    inline: bool  # open in the browser (True) or download (False)
    version: int  # bump when output changes, so documents are re-read

    def sniff(self, path: Path) -> bool:
        """Do the bytes look like this type? Must refuse polyglots."""
        ...

    def validate(self, path: Path) -> None:
        """Deeper check on upload. Raise ``AppError`` with a plain message if unusable."""
        ...

    def extract(
        self,
        path: Path,
        progress: ProgressFn,
        options: ExtractOptions,
        cancelled: Callable[[], bool],
    ) -> Extracted:
        """Read the file. Raise ``DocumentError`` if the file itself is the problem."""
        ...


_REGISTRY: dict[str, Extractor] = {}
_BY_EXTENSION: dict[str, Extractor] = {}
_LOADED = False


def register(extractor: Extractor) -> None:
    _REGISTRY[extractor.type] = extractor
    for ext in extractor.extensions:
        _BY_EXTENSION[ext.lower()] = extractor


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    package = importlib.import_module("sidelamp.ingest.extractors")
    for info in pkgutil.iter_modules(package.__path__):
        if info.name == "base":
            continue
        module = importlib.import_module(f"sidelamp.ingest.extractors.{info.name}")
        extractor = getattr(module, "EXTRACTOR", None)
        if extractor is not None:
            register(extractor)


def all_extractors() -> list[Extractor]:
    _load()
    return list(_REGISTRY.values())


def get_extractor(doc_type: str) -> Extractor | None:
    _load()
    return _REGISTRY.get(doc_type)


def extractor_for(filename: str) -> Extractor | None:
    _load()
    suffix = Path(filename).suffix.lower()
    return _BY_EXTENSION.get(suffix)


def supported_extensions() -> tuple[str, ...]:
    _load()
    return tuple(sorted(_BY_EXTENSION))
