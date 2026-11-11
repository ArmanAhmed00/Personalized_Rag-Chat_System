from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
import pytest

from outlawz.errors import DocumentError
from outlawz.ingest.extractors import ExtractOptions, all_extractors, extractor_for, get_extractor
from outlawz.ingest.extractors.base import Cancelled, Extracted
from tests.builders import structured_pdf
from tests.conftest import epub_bytes


def run(doc_type: str, path: Path, **options: object) -> Extracted:
    extractor = get_extractor(doc_type)
    assert extractor is not None
    messages: list[str] = []
    result = extractor.extract(
        path, lambda d, t, m: messages.append(m), ExtractOptions(**options), lambda: False
    )  # type: ignore[arg-type]
    result.meta["_messages"] = messages
    return result


def test_registry_discovers_types() -> None:
    assert {e.type for e in all_extractors()} >= {"pdf", "epub", "csv"}
    assert extractor_for("Report.PDF") is get_extractor("pdf")
    assert extractor_for("notes.docx") is None


# ---------------------------------------------------------------- PDF


def test_pdf_pages_headings_and_progress(tmp_path: Path) -> None:
    result = run("pdf", structured_pdf(tmp_path / "r.pdf"))
    assert result.pages == 4
    headings = [b for b in result.blocks if b.kind == "heading"]
    assert [h.text for h in headings] == [f"Section {n}: Policies" for n in range(1, 5)]
    assert [h.page for h in headings] == [1, 2, 3, 4]
    texts = [b for b in result.blocks if b.kind == "text"]
    assert texts[0].section == "Section 1: Policies"
    assert "Reading page 3 of 4" in result.meta["_messages"]


def test_pdf_removes_running_header_and_page_numbers(tmp_path: Path) -> None:
    result = run("pdf", structured_pdf(tmp_path / "r.pdf"))
    joined = "\n".join(b.text for b in result.blocks)
    assert "ACME Corp" not in joined
    assert not any(b.text.strip() in {"1", "2", "3", "4"} for b in result.blocks)


def test_pdf_chapters_come_from_toc(tmp_path: Path) -> None:
    result = run("pdf", structured_pdf(tmp_path / "r.pdf"))
    chapters = {b.page: b.chapter for b in result.blocks}
    assert chapters[1] == chapters[2] == "Part One"
    assert chapters[3] == chapters[4] == "Part Two"


def test_pdf_table_becomes_one_block(tmp_path: Path) -> None:
    result = run("pdf", structured_pdf(tmp_path / "r.pdf"))
    tables = [b for b in result.blocks if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].page == 2
    lines = tables[0].text.splitlines()
    assert lines[0] == "| Region | Sales | Year |"
    assert "| North | 120 | 2024 |" in lines
    others = "\n".join(b.text for b in result.blocks if b.kind != "table")
    assert "North" not in others  # table text isn't repeated as paragraphs


def test_pdf_rejoins_hyphenated_words(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "The committee reached an agree-", fontsize=11)
    page.insert_text((72, 114), "ment on the budget.", fontsize=11)
    path = tmp_path / "h.pdf"
    doc.save(path)
    result = run("pdf", path)
    assert "agreement on the budget" in " ".join(b.text for b in result.blocks)


def test_pdf_without_text_explains_ocr(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(path)
    with pytest.raises(DocumentError) as info:
        run("pdf", path, ocr=False)
    assert info.value.code == "no_text"
    assert "Tesseract" in info.value.message


def test_damaged_pdf(tmp_path: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.7\n garbage garbage")
    with pytest.raises(DocumentError):
        run("pdf", path)


def test_password_protected_pdf(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    with pytest.raises(DocumentError) as info:
        run("pdf", path)
    assert info.value.code == "password_protected"


def test_cancel_stops_extraction(tmp_path: Path) -> None:
    path = structured_pdf(tmp_path / "r.pdf")
    extractor = get_extractor("pdf")
    assert extractor is not None
    with pytest.raises(Cancelled):
        extractor.extract(path, lambda *a: None, ExtractOptions(), lambda: True)


# ---------------------------------------------------------------- EPUB


def test_epub_chapters_sections_and_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "b.epub"
    path.write_bytes(
        epub_bytes(
            [
                (
                    "Chapter One",
                    "<h2>Arrival</h2><p>It was a bright cold day.</p>"
                    "<ul><li>first</li><li>second</li></ul>",
                ),
                (
                    "Chapter Two",
                    "<p>Second chapter text.</p><table><tr><th>a</th><th>b</th></tr>"
                    "<tr><td>1</td><td>2</td></tr></table>",
                ),
            ]
        )
    )
    result = run("epub", path)
    texts = [(b.kind, b.chapter, b.section, b.text) for b in result.blocks]
    assert ("text", "Chapter One", "Arrival", "It was a bright cold day.") in texts
    assert ("text", "Chapter One", "Arrival", "• first") in texts
    assert ("text", "Chapter Two", None, "Second chapter text.") in texts
    tables = [b for b in result.blocks if b.kind == "table"]
    assert tables and tables[0].text.splitlines()[0] == "| a | b |"
    assert result.meta["chapters"] == 2
    assert "Reading chapter 2 of 2" in result.meta["_messages"]


def test_epub_with_drm_is_refused(tmp_path: Path) -> None:
    data = epub_bytes(
        extra_entries={
            "META-INF/encryption.xml": (
                b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                b'<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod '
                b'Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/><CipherData>'
                b'<CipherReference URI="OEBPS/c1.xhtml"/></CipherData></EncryptedData></encryption>'
            )
        }
    )
    path = tmp_path / "drm.epub"
    path.write_bytes(data)
    with pytest.raises(DocumentError) as info:
        run("epub", path)
    assert info.value.code == "drm"


def test_epub_sniff_rejects_plain_zip(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    path = tmp_path / "x.epub"
    path.write_bytes(buf.getvalue())
    extractor = get_extractor("epub")
    assert extractor is not None and not extractor.sniff(path)


# ---------------------------------------------------------------- CSV


def test_csv_schema_profile_and_row_blocks(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    rows = "\n".join(
        f"{'North' if i % 2 else 'South'},{i * 10},2024-01-{(i % 28) + 1:02d}"
        for i in range(1, 121)
    )
    path.write_text("region,sales,date\n" + rows + "\n")
    result = run("csv", path, csv_rows=100, target_tokens=60)
    kinds = [b.kind for b in result.blocks]
    assert kinds[0] == "schema"
    assert "120 rows and 3 columns" in result.blocks[0].text
    assert "Only the first 100 rows" in result.blocks[0].text
    profile = next(b for b in result.blocks if b.kind == "profile")
    assert "sales (number): min 10, max 1,200" in profile.text
    assert "region (text): 2 distinct values" in profile.text
    row_blocks = [b for b in result.blocks if b.kind == "rows"]
    assert row_blocks[0].rows is not None and row_blocks[0].rows[0] == 1
    assert row_blocks[-1].rows is not None and row_blocks[-1].rows[1] == 100  # the cap
    assert "Row 1: region = North; sales = 10" in row_blocks[0].text
    index = row_blocks[0].index_text or ""
    assert "region" not in index and "North" in index  # values only for keyword search
    assert result.meta["kinds"] == {"region": "text", "sales": "number", "date": "date"}


def test_csv_semicolons_latin1_and_european_numbers(tmp_path: Path) -> None:
    path = tmp_path / "eu.csv"
    path.write_bytes("ville;montant\nMontréal;1.200,50\nQuébec;80\n".encode("latin-1"))
    result = run("csv", path)
    assert result.meta["delimiter"] == ";"
    assert "Montréal" in result.blocks[-1].text
    profile = next(b for b in result.blocks if b.kind == "profile")
    assert "max 1,200.5" in profile.text


def test_csv_duplicate_and_blank_headers(tmp_path: Path) -> None:
    path = tmp_path / "h.csv"
    path.write_text("name,,name\na,b,c\n")
    result = run("csv", path)
    assert result.meta["columns"] == ["name", "column_2", "name_2"]
