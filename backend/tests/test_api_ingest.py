"""Ingestion through the API, and the restart-mid-ingestion guarantee."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sidelamp.config import Settings
from sidelamp.library.documents import Status
from tests.builders import structured_pdf
from tests.conftest import app_state, csv_bytes, make_client, pdf_bytes, wait_ingested


def test_upload_is_processed_to_ready(live_client: TestClient) -> None:
    doc = live_client.post(
        "/api/documents", files={"file": ("r.pdf", pdf_bytes(["Refunds take 30 days."]))}
    ).json()
    wait_ingested(live_client)
    ready = live_client.get(f"/api/documents/{doc['id']}").json()
    assert ready["status"] == "ready"
    assert ready["chunks"] >= 1
    assert ready["progress"]["message"].startswith("Ready")
    passage = live_client.get(f"/api/documents/{doc['id']}/passages/0").json()
    assert "Refunds take 30 days." in passage["text"]
    assert passage["page"] == 1 and passage["document"] == "r.pdf"
    assert "tf" not in passage
    assert live_client.get(f"/api/documents/{doc['id']}/passages/99").status_code == 404


def test_read_again(live_client: TestClient) -> None:
    doc = live_client.post("/api/documents", files={"file": ("d.csv", csv_bytes())}).json()
    wait_ingested(live_client)
    again = live_client.post(f"/api/documents/{doc['id']}/reread").json()
    assert again["status"] == "queued"
    wait_ingested(live_client)
    assert live_client.get(f"/api/documents/{doc['id']}").json()["status"] == "ready"


def test_rebuild_everything_restores_processed_data(
    live_client: TestClient, settings: Settings
) -> None:
    for name in ("a.csv", "b.csv"):
        live_client.post("/api/documents", files={"file": (name, csv_bytes())})
    wait_ingested(live_client)
    docs_dir = settings.library / ".sidelamp" / "docs"
    for folder in docs_dir.iterdir():  # someone deletes the processed data
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
    result = live_client.post("/api/library/rebuild").json()
    assert result["queued"] == 2
    wait_ingested(live_client)
    statuses = {d["status"] for d in live_client.get("/api/documents").json()["documents"]}
    assert statuses == {"ready"}
    assert len(list(docs_dir.iterdir())) == 2


def test_folder_tree(live_client: TestClient, settings: Settings) -> None:
    (settings.library / "work" / "2025").mkdir(parents=True)
    live_client.post("/api/documents", files={"file": ("top.csv", csv_bytes())})
    live_client.post(
        "/api/documents", files={"file": ("deep.csv", csv_bytes())}, data={"folder": "work/2025"}
    )
    tree = live_client.get("/api/library/tree").json()["tree"]
    assert [d["name"] for d in tree["documents"]] == ["top.csv"]
    work = tree["folders"][0]
    assert work["name"] == "work"
    assert work["folders"][0]["documents"][0]["path"] == "work/2025/deep.csv"


def test_restart_mid_ingestion_finishes_the_document(settings: Settings) -> None:
    with make_client(settings) as c:  # no workers: the upload stays queued
        doc = c.post("/api/documents", files={"file": ("r.pdf", pdf_bytes(["Text."]))}).json()
        state = app_state(c)
        state.docs.update(doc["id"], status=Status.embedding)  # killed while embedding
    with make_client(settings, background=True) as c:
        wait_ingested(c)
        assert c.get(f"/api/documents/{doc['id']}").json()["status"] == "ready"


def test_watcher_picks_up_dropped_file(settings: Settings) -> None:
    settings.watch_interval = 0.5
    with make_client(settings, background=True, watch=True) as c:
        structured_pdf(settings.library / "dropped.pdf")
        state = app_state(c)
        assert state.watcher is not None
        import time

        later = time.time() + 100
        state.watcher.scan_once(now=later)
        state.watcher.scan_once(now=later)
        wait_ingested(c)
        names = {d["name"]: d["status"] for d in c.get("/api/documents").json()["documents"]}
        assert names == {"dropped.pdf": "ready"}
