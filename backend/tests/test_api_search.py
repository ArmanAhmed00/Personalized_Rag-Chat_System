"""Search through the API, including the "Rebuild everything" guarantee."""

from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from sidelamp.config import Settings
from tests.builders import simple_pdf
from tests.conftest import app_state, make_client, wait_ingested


def add_pdf(c: TestClient, settings: Settings, name: str, text: str) -> str:
    path = simple_pdf(settings.library_paths.tmp_dir.parent / f"_{name}", [text])
    doc = c.post("/api/documents", files={"file": (name, path.read_bytes())}).json()
    path.unlink()
    return str(doc["id"])


def test_search_finds_and_explains(live_client: TestClient, settings: Settings) -> None:
    refunds = add_pdf(
        live_client, settings, "refunds.pdf", "Refunds are issued within 30 days of delivery."
    )
    add_pdf(live_client, settings, "shipping.pdf", "Shipping takes five business days.")
    wait_ingested(live_client)
    body = live_client.post("/api/search", json={"query": "refund within 30 days"}).json()
    top = body["hits"][0]
    assert top["doc_id"] == refunds and top["document"] == "refunds.pdf"
    assert top["page"] == 1
    assert "30" in top["matched"]
    assert top["explanation"].startswith("Found by")
    assert body["reranked"] is True  # lexical reranker in tests
    assert body["searched_passages"] == 2


def test_search_respects_document_selection(live_client: TestClient, settings: Settings) -> None:
    add_pdf(live_client, settings, "a.pdf", "Alpha project budget is 400 euros.")
    b = add_pdf(live_client, settings, "b.pdf", "Beta project budget is 900 euros.")
    wait_ingested(live_client)
    body = live_client.post(
        "/api/search", json={"query": "project budget", "documents": [b]}
    ).json()
    assert {h["doc_id"] for h in body["hits"]} == {b}


def test_deleted_document_leaves_search(live_client: TestClient, settings: Settings) -> None:
    doc = add_pdf(live_client, settings, "gone.pdf", "Unique zebra paragraph.")
    wait_ingested(live_client)
    assert live_client.post("/api/search", json={"query": "zebra"}).json()["hits"]
    live_client.delete(f"/api/documents/{doc}")
    assert live_client.post("/api/search", json={"query": "zebra"}).json()["hits"] == []


def test_validation(live_client: TestClient) -> None:
    assert live_client.post("/api/search", json={"query": ""}).status_code == 422
    assert live_client.post("/api/search", json={"query": "x", "mode": "fuzzy"}).status_code == 422


def test_index_is_rebuilt_from_files_on_restart(settings: Settings) -> None:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "kept.pdf", "The lighthouse keeper logs every ship.")
        wait_ingested(c)
    with make_client(settings) as c:  # no workers: the index must come from the stored files
        assert app_state(c).index.passage_count == 1
        hits = c.post("/api/search", json={"query": "lighthouse keeper"}).json()["hits"]
        assert hits and hits[0]["document"] == "kept.pdf"


def test_deleting_processed_data_and_rebuilding_restores_search(settings: Settings) -> None:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "a.pdf", "Quarterly revenue grew by 12 percent.")
        wait_ingested(c)
    shutil.rmtree(settings.library / ".sidelamp" / "docs")  # processed data gone
    (settings.library / ".sidelamp" / "docs").mkdir()
    with make_client(settings, background=True) as c:
        state = app_state(c)
        assert state.index.passage_count == 0
        c.post("/api/library/rebuild")
        wait_ingested(c)
        hits = c.post("/api/search", json={"query": "quarterly revenue"}).json()["hits"]
        assert hits and hits[0]["document"] == "a.pdf"
