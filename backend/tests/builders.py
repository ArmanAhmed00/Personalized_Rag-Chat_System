"""Builders for realistic test documents."""

from __future__ import annotations

from pathlib import Path

import pymupdf

BODY = (
    "The refund window is 30 days from delivery. Customers must keep the receipt. "
    "Items bought on sale can be exchanged but not refunded. "
)


def structured_pdf(
    path: Path, pages: int = 4, *, toc: bool = True, table_on: int = 2, body: str = BODY
) -> Path:
    """Pages with a running header, a page-number footer, a large heading, body text, an optional
    ruled table and a table of contents."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 40), "ACME Corp — Annual Report 2025", fontsize=9)
        page.insert_text((72, 90), f"Section {n}: Policies", fontsize=18)
        page.insert_textbox(pymupdf.Rect(72, 110, 520, 420), body * 2 + f"Marker {n}.", fontsize=11)
        page.insert_text((300, 820), str(n), fontsize=9)
        if n == table_on:
            xs, ys = [72, 200, 330, 460], [450, 470, 490, 510]
            data = [["Region", "Sales", "Year"], ["North", "120", "2024"], ["South", "80", "2024"]]
            for r in range(3):
                for c in range(3):
                    page.insert_text((xs[c] + 4, ys[r] + 14), data[r][c], fontsize=10)
            for y in ys:
                page.draw_line((72, y), (460, y))
            for x in xs:
                page.draw_line((x, 450), (x, 510))
    if toc:
        doc.set_toc([[1, "Part One", 1], [1, "Part Two", 3]])
    doc.save(path)
    doc.close()
    return path


def simple_pdf(path: Path, texts: list[str]) -> Path:
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 520, 760), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path
