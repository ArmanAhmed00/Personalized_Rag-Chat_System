"""PDF: text with page numbers, headings, tables, and OCR for scanned pages.

* Headings are lines noticeably larger than the body text (or short bold lines on their own).
  If the PDF has a table of contents, its entries name the chapters and sections instead.
* Running headers and footers (the same line near the top or bottom of most pages) and page
  numbers are removed, so they don't pollute passages.
* Tables found by PyMuPDF become Markdown-style tables; their text isn't repeated as paragraphs.
* A page with almost no text but with images is read with OCR when Tesseract is installed.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
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

_PAGE_NUMBER = re.compile(
    r"^\s*[-–—]?\s*(page\s+)?([0-9]{1,4}|[ivxlcdm]{1,7})(\s*(of|/)\s*[0-9]{1,4})?\s*[-–—]?\s*$",
    re.IGNORECASE,
)
_EDGE = 0.09  # top/bottom share of the page where running headers and footers live
_BOLD = 16


@dataclass
class _Line:
    text: str
    size: float
    bold: bool
    y0: float
    y1: float
    block: int


def ocr_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pymupdf

        pymupdf.get_tessdata()
    except Exception:
        return False
    return True


class PdfExtractor:
    type = "pdf"
    label = "PDF"
    extensions = (".pdf",)
    media_type = "application/pdf"
    inline = True
    version = 1

    def sniff(self, path: Path) -> bool:
        with open(path, "rb") as fh:
            head = fh.read(1024)
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65_536))
            tail = fh.read()
        if b"%PDF-" not in head:
            return False
        # A PDF that is also a ZIP (polyglot) is refused.
        return not (head.startswith(b"PK\x03\x04") or b"PK\x05\x06" in tail)

    def validate(self, path: Path) -> None:
        import pymupdf

        try:
            with pymupdf.open(path) as pdf:
                if pdf.needs_pass:
                    raise AppError(
                        "password_protected",
                        "This PDF is password-protected. Remove the password and add it again.",
                    )
                if pdf.page_count < 1:
                    raise AppError("damaged_file", "This PDF has no pages. It wasn't added.")
        except AppError:
            raise
        except Exception:
            raise AppError("damaged_file", "This PDF can't be opened. It may be damaged.") from None

    def extract(
        self,
        path: Path,
        progress: ProgressFn,
        options: ExtractOptions,
        cancelled: Callable[[], bool],
    ) -> Extracted:
        import pymupdf

        try:
            pdf = pymupdf.open(path)
        except Exception:
            raise DocumentError("damaged", "This PDF can't be opened. It may be damaged.") from None
        with pdf:
            if pdf.needs_pass:
                raise DocumentError(
                    "password_protected", "This PDF is password-protected, so it can't be read."
                )
            total = pdf.page_count
            use_ocr = options.ocr and ocr_available()
            pages: list[list[_Line]] = []
            tables: list[list[tuple[float, str]]] = []
            ocr_pages = 0
            for number in range(total):
                if cancelled():
                    raise Cancelled()
                progress(number, total, f"Reading page {number + 1} of {total}")
                page = pdf[number]
                lines, page_tables, used_ocr = _read_page(page, use_ocr)
                ocr_pages += used_ocr
                pages.append(lines)
                tables.append(page_tables)
            progress(total, total, f"Read {total} pages")
            toc = [
                (lvl, title.strip(), pg)
                for lvl, title, pg in pdf.get_toc(simple=True)
                if title and title.strip()
            ]
            heights = [pdf[i].rect.height for i in range(total)]
            title = (pdf.metadata or {}).get("title") or ""

        _drop_running_lines(pages, heights)
        blocks = _to_blocks(pages, tables, toc)
        if not any(b.text.strip() for b in blocks):
            hint = (
                (
                    " It may be a scan: install Tesseract to read scanned pages, then choose "
                    "Read again."
                )
                if not use_ocr
                else ""
            )
            raise DocumentError("no_text", f"No text was found in this PDF.{hint}")
        return Extracted(
            blocks=blocks,
            pages=total,
            meta={"title": title, "toc": bool(toc), "ocr_pages": ocr_pages},
        )


def _read_page(page: Any, use_ocr: bool) -> tuple[list[_Line], list[tuple[float, str]], int]:
    data = page.get_text("dict")
    used_ocr = 0
    if use_ocr and _char_count(data) < 20 and page.get_images():
        try:
            textpage = page.get_textpage_ocr(language="eng", dpi=200, full=True)
            data = page.get_text("dict", textpage=textpage)
            used_ocr = 1
        except Exception:  # noqa: S110 - OCR is best effort; the page just stays empty
            pass

    table_boxes: list[tuple[float, float, float, float]] = []
    page_tables: list[tuple[float, str]] = []
    try:
        finder = page.find_tables()
        for table in finder.tables:
            rows = table.extract()
            text = _table_text(rows)
            if text:
                table_boxes.append(tuple(table.bbox))
                page_tables.append((float(table.bbox[1]), text))
    except Exception:  # noqa: S110 - table detection is best effort
        pass

    lines: list[_Line] = []
    for b_index, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            x0, y0, x1, y1 = line["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in table_boxes):
                continue
            text = "".join(s["text"] for s in spans)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            size = max(float(s.get("size", 0)) for s in spans)
            bold = all(
                int(s.get("flags", 0)) & _BOLD or "bold" in s.get("font", "").lower() for s in spans
            )
            lines.append(_Line(text, size, bool(bold), float(y0), float(y1), b_index))
    return lines, page_tables, used_ocr


def _char_count(data: dict[str, Any]) -> int:
    return sum(
        len(s.get("text", "").strip())
        for b in data.get("blocks", [])
        if b.get("type") == 0
        for ln in b.get("lines", [])
        for s in ln.get("spans", [])
    )


def _table_text(rows: list[list[Any]]) -> str:
    clean = [[re.sub(r"\s+", " ", str(c or "")).strip() for c in row] for row in rows]
    clean = [row for row in clean if any(row)]
    if len(clean) < 2 or max(len(r) for r in clean) < 2:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    keep = [i for i in range(width) if any(row[i] for row in clean)]  # drop all-empty columns
    clean = [[row[i] for i in keep] for row in clean]
    width = len(keep)
    if width < 2:
        return ""
    out = ["| " + " | ".join(clean[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(out)


def _drop_running_lines(pages: list[list[_Line]], heights: list[float]) -> None:
    """Remove page numbers and lines repeated near the top or bottom of most pages."""

    def edge(line: _Line, height: float) -> bool:
        return line.y1 <= height * _EDGE or line.y0 >= height * (1 - _EDGE)

    def key(text: str) -> str:
        return re.sub(r"\d+", "#", text.lower()).strip()

    seen: dict[str, set[int]] = defaultdict(set)
    for number, lines in enumerate(pages):
        for line in lines:
            if edge(line, heights[number]):
                seen[key(line.text)].add(number)
    count = len(pages)
    repeated = {
        k for k, where in seen.items() if count >= 3 and len(where) >= max(3, int(count * 0.5))
    }
    for number, lines in enumerate(pages):
        height = heights[number]
        pages[number] = [
            ln
            for ln in lines
            if not (edge(ln, height) and (_PAGE_NUMBER.match(ln.text) or key(ln.text) in repeated))
        ]


def _body_size(pages: list[list[_Line]]) -> float:
    weights: Counter[float] = Counter()
    for lines in pages:
        for line in lines:
            weights[round(line.size, 1)] += len(line.text)
    if not weights:
        return 10.0
    return weights.most_common(1)[0][0]


def _is_heading(line: _Line, body: float, block_lines: int) -> bool:
    text = line.text
    if len(text) > 150 or not re.search(r"[A-Za-z]", text) or _PAGE_NUMBER.match(text):
        return False
    if line.size >= body * 1.18:
        return True
    return (
        line.bold
        and line.size >= body * 0.98
        and len(text) <= 80
        and block_lines <= 2
        and not text.endswith((".", ",", ";", ":"))
    )


def _join_lines(lines: list[str]) -> str:
    out = ""
    for text in lines:
        if out.endswith("-") and text[:1].islower():
            out = out[:-1] + text  # re-join a word hyphenated across lines
        elif out:
            out += " " + text
        else:
            out = text
    return out


def _toc_lookup(toc: list[tuple[int, str, int]]) -> Callable[[int], tuple[str | None, str | None]]:
    chapters = sorted((pg, title) for lvl, title, pg in toc if lvl == 1 and pg > 0)
    sections = sorted((pg, title, lvl) for lvl, title, pg in toc if lvl >= 2 and pg > 0)

    def lookup(page: int) -> tuple[str | None, str | None]:
        chapter = None
        for pg, title in chapters:
            if pg <= page:
                chapter = title
        section = None
        chapter_start = max((pg for pg, t in chapters if t == chapter), default=0)
        for pg, title, _ in sections:
            if chapter_start <= pg <= page:
                section = title
        return chapter, section

    return lookup


class _BlockBuilder:
    """Collects paragraphs, headings and tables in reading order, tracking the current section."""

    def __init__(self, uses_toc: bool) -> None:
        self.uses_toc = uses_toc
        self.blocks: list[Block] = []
        self.section: str | None = None
        self.chapter: str | None = None
        self.page = 0
        self.paragraph: list[str] = []
        self.heading: list[str] = []

    def add(self, text: str, kind: str = "text") -> None:
        self.blocks.append(
            Block(text=text, kind=kind, page=self.page, section=self.section, chapter=self.chapter)
        )

    def flush_paragraph(self) -> None:
        if self.paragraph:
            self.add(_join_lines(self.paragraph))
            self.paragraph.clear()

    def flush_heading(self) -> None:
        if self.heading:
            text = _join_lines(self.heading)
            if not self.uses_toc or text != self.chapter:
                self.section = text
            self.add(text, "heading")
            self.heading.clear()

    def flush(self) -> None:
        self.flush_paragraph()
        self.flush_heading()


def _to_blocks(
    pages: list[list[_Line]], tables: list[list[tuple[float, str]]], toc: list[tuple[int, str, int]]
) -> list[Block]:
    body = _body_size(pages)
    lookup = _toc_lookup(toc) if toc else None
    out = _BlockBuilder(uses_toc=lookup is not None)
    for number, lines in enumerate(pages, start=1):
        out.page = number
        if lookup is not None:
            out.chapter, toc_section = lookup(number)
            if toc_section is not None:
                out.section = toc_section
        pending_tables = sorted(tables[number - 1])
        per_block = Counter(ln.block for ln in lines)
        current_block = -1
        for line in lines:
            while pending_tables and pending_tables[0][0] <= line.y0:
                out.flush()
                out.add(pending_tables.pop(0)[1], "table")
            if line.block != current_block:
                out.flush()
                current_block = line.block
            if _is_heading(line, body, per_block[line.block]):
                out.flush_paragraph()
                out.heading.append(line.text)
            else:
                out.flush_heading()
                out.paragraph.append(line.text)
        out.flush()
        for _, table_text in pending_tables:
            out.add(table_text, "table")
    return out.blocks


EXTRACTOR = PdfExtractor()
