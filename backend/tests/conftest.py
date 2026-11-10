"""Shared fixtures. Every test runs offline in temp folders with fake models."""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidelamp.app import create_app
from sidelamp.config import Settings, load_settings
from sidelamp.state import AppState

LOCAL = ("127.0.0.1", 50000)
BASE_URL = "http://127.0.0.1:8642"
CSRF = {"X-Sidelamp": "1"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith("SIDELAMP_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        library=tmp_path / "library",
        data_dir=tmp_path / "data",
        embedder="fake",
        llm="fake",
        reranker="lexical",
        docker=False,
        open_browser=False,
    )


def make_client(
    settings: Settings,
    *,
    client: tuple[str, int] = LOCAL,
    base_url: str = BASE_URL,
    csrf: bool = True,
    background: bool = False,
    watch: bool = False,
) -> TestClient:
    app = create_app(settings, background=background, watch=watch)
    headers = dict(CSRF) if csrf else {}
    return TestClient(app, base_url=base_url, client=client, headers=headers)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with make_client(settings) as c:
        yield c


@pytest.fixture
def live_client(settings: Settings) -> Iterator[TestClient]:
    """A client whose ingestion queue runs (the folder watcher doesn't)."""
    with make_client(settings, background=True) as c:
        yield c


def app_state(c: TestClient) -> AppState:
    state: AppState = c.app.state.sidelamp  # type: ignore[attr-defined]
    return state


def wait_ingested(c: TestClient, timeout: float = 30.0) -> None:
    state = app_state(c)
    assert state.queue is not None
    c.portal.call(state.queue.wait_idle, timeout)  # type: ignore[union-attr]


# ---------------------------------------------------------------- sample files


def pdf_bytes(pages: list[str] | None = None) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text in pages or ["Hello from a test PDF."]:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return bytes(data)


def epub_bytes(
    chapters: list[tuple[str, str]] | None = None, *, extra_entries: dict[str, bytes] | None = None
) -> bytes:
    """A minimal valid EPUB 3."""
    chapters = chapters or [("Chapter One", "<p>It was a bright cold day.</p>")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine = []
        nav_items = []
        for i, (title, body) in enumerate(chapters, 1):
            manifest.append(
                f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="c{i}"/>')
            nav_items.append(f'<li><a href="c{i}.xhtml">{title}</a></li>')
            zf.writestr(
                f"OEBPS/c{i}.xhtml",
                f'<?xml version="1.0" encoding="utf-8"?><html '
                f'xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title>'
                f"</head><body><h1>{title}</h1>{body}</body></html>",
            )
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<head><title>Nav</title></head><body><nav epub:type="toc"><ol>'
            + "".join(nav_items)
            + "</ol></nav></body></html>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?><package version="3.0" '
            'xmlns="http://www.idpf.org/2007/opf" unique-identifier="id"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">test-book'
            "</dc:identifier><dc:title>Test Book</dc:title><dc:language>en</dc:language>"
            '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
            f"<manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine>"
            "</package>",
        )
        for name, data in (extra_entries or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def csv_bytes(text: str = "region,sales\nNorth,120\nSouth,80\n") -> bytes:
    return text.encode("utf-8")
