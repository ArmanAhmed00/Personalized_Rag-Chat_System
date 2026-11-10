"""EPUB: chapters, headings and paragraphs, in reading (spine) order.

Chapter names come from the book's table of contents where it has them, otherwise from the first
heading of each chapter file. Copy-protected (DRM) books are reported as such instead of producing
gibberish.
"""

from __future__ import annotations

import re
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sidelamp.errors import AppError, DocumentError
from sidelamp.ingest.extractors.base import (
    Block,
    Cancelled,
    Extracted,
    ExtractOptions,
    ProgressFn,
)
from sidelamp.store.safepath import UnsafePath, check_relative

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TEXT_TAGS = {"p", "li", "blockquote", "pre", "dt", "dd", "figcaption", "caption", "address"}
_SKIP_TAGS = {"script", "style", "nav", "head", "title", "svg", "math", "noscript"}
_FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}


def _zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


class EpubExtractor:
    type = "epub"
    label = "EPUB ebook"
    extensions = (".epub",)
    media_type = "application/epub+zip"
    inline = False
    version = 1

    def sniff(self, path: Path) -> bool:
        with open(path, "rb") as fh:
            head = fh.read(1024)
        if not head.startswith(b"PK\x03\x04") or b"%PDF-" in head:
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if not names or names[0] != "mimetype":
                    return False
                if zf.read("mimetype").strip() != b"application/epub+zip":
                    return False
                if "META-INF/container.xml" not in names:
                    return False
        except (zipfile.BadZipFile, OSError, KeyError):
            return False
        try:
            for name in names:
                check_relative(name)  # zip-slip style entries are refused
        except UnsafePath:
            return False
        return True

    def validate(self, path: Path) -> None:
        try:
            _read_book(path)
        except DocumentError as exc:
            raise AppError(exc.code, exc.message) from None

    def extract(
        self,
        path: Path,
        progress: ProgressFn,
        options: ExtractOptions,
        cancelled: Callable[[], bool],
    ) -> Extracted:
        from bs4 import BeautifulSoup

        book = _read_book(path)
        _refuse_drm(path)
        toc_titles = _toc_titles(book)
        documents = _spine_documents(book)
        total = len(documents)
        blocks: list[Block] = []
        for number, item in enumerate(documents, start=1):
            if cancelled():
                raise Cancelled()
            progress(number - 1, total, f"Reading chapter {number} of {total}")
            soup = BeautifulSoup(item.get_content(), "html.parser")
            body = soup.body or soup
            chapter = toc_titles.get(_href(item.get_name()))
            if not chapter:
                first = body.find(["h1", "h2"])
                chapter = first.get_text(" ", strip=True) if first else None
            if not chapter:
                title = soup.find("title")
                chapter = title.get_text(" ", strip=True) if title else f"Part {number}"
            blocks.extend(_walk(body, chapter))
        progress(total, total, f"Read {total} chapters")
        if not any(b.text.strip() for b in blocks):
            raise DocumentError("no_text", "No text was found in this ebook.")
        book_title = ""
        meta = book.get_metadata("DC", "title")
        if meta:
            book_title = str(meta[0][0])
        return Extracted(blocks=blocks, pages=None, meta={"title": book_title, "chapters": total})


def _read_book(path: Path) -> Any:
    from ebooklib import epub

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return epub.read_epub(str(path), options={"ignore_ncx": True})
    except Exception:
        raise DocumentError("damaged", "This ebook can't be opened. It may be damaged.") from None


def _refuse_drm(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            if "META-INF/encryption.xml" not in zf.namelist():
                return
            xml = zf.read("META-INF/encryption.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError):
        return
    algorithms = set(re.findall(r'Algorithm="([^"]+)"', xml))
    targets = re.findall(r'URI="([^"]+)"', xml)
    content_encrypted = any(t.lower().endswith((".xhtml", ".html", ".htm")) for t in targets)
    if content_encrypted and not algorithms <= _FONT_OBFUSCATION:
        raise DocumentError("drm", "This ebook is copy-protected (DRM), so it can't be read.")


def _href(name: str) -> str:
    return name.split("#", 1)[0].rsplit("/", 1)[-1].lower()


def _toc_titles(book: Any) -> dict[str, str]:
    titles: dict[str, str] = {}

    def visit(entries: Any) -> None:
        for entry in entries:
            if isinstance(entry, tuple | list) and len(entry) == 2:
                section, children = entry
                href = getattr(section, "href", None)
                if href and getattr(section, "title", None):
                    titles.setdefault(_href(href), str(section.title).strip())
                visit(children)
            else:
                href = getattr(entry, "href", None)
                title = getattr(entry, "title", None)
                if href and title:
                    titles.setdefault(_href(href), str(title).strip())

    visit(book.toc or [])
    return titles


def _spine_documents(book: Any) -> list[Any]:
    from ebooklib import ITEM_DOCUMENT

    items = []
    for entry in book.spine:
        idref = entry[0] if isinstance(entry, tuple | list) else entry
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        if "nav" in (getattr(item, "properties", None) or []):
            continue
        items.append(item)
    return items


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _has_block_child(tag: Any) -> bool:
    return any(
        getattr(c, "name", None) in (_TEXT_TAGS | _HEADINGS | {"table", "div", "ul", "ol"})
        for c in tag.children
    )


def _walk(root: Any, chapter: str) -> list[Block]:
    from bs4 import NavigableString, Tag

    blocks: list[Block] = []
    state: dict[str, str | None] = {"section": None}
    loose: list[str] = []

    def flush_loose() -> None:
        text = _clean(" ".join(loose))
        loose.clear()
        if text:
            blocks.append(Block(text=text, section=state["section"], chapter=chapter))

    def visit(node: Any) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                if type(child).__name__ == "NavigableString" and child.strip():
                    loose.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in _SKIP_TAGS:
                continue
            if name in _HEADINGS:
                flush_loose()
                text = _clean(child.get_text(" ", strip=True))
                if text:
                    if text != chapter:
                        state["section"] = text
                    blocks.append(
                        Block(text=text, kind="heading", section=state["section"], chapter=chapter)
                    )
            elif name == "table":
                flush_loose()
                text = _table_text(child)
                if text:
                    blocks.append(
                        Block(text=text, kind="table", section=state["section"], chapter=chapter)
                    )
            elif name in _TEXT_TAGS and not _has_block_child(child):
                flush_loose()
                text = _clean(child.get_text(" ", strip=True))
                if text:
                    if name == "li":
                        text = f"• {text}"
                    blocks.append(Block(text=text, section=state["section"], chapter=chapter))
            elif name in {"br", "hr", "img"}:
                continue
            elif name in {"span", "a", "em", "strong", "i", "b", "sup", "sub", "small", "code"}:
                loose.append(child.get_text(" ", strip=True))
            else:
                flush_loose()
                visit(child)
        # end of a container

    visit(root)
    flush_loose()
    return blocks


def _table_text(table: Any) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [_clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if len(rows) < 2:
        return " ".join(" ".join(r) for r in rows)
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


EXTRACTOR = EpubExtractor()
