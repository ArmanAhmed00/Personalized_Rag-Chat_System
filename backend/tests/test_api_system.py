"""System pages and evaluations through the API, and the ``outlawz eval`` command."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from outlawz.cli import main
from outlawz.config import Settings
from tests.conftest import app_state, wait_ingested
from tests.test_api_search import add_pdf


def test_overview(live_client: TestClient, settings: Settings) -> None:
    add_pdf(live_client, settings, "a.pdf", "Text for the overview page.")
    wait_ingested(live_client)
    body = live_client.get("/api/system/overview").json()
    assert body["documents"]["total"] == 1 and body["documents"]["by_status"] == {"ready": 1}
    assert body["documents"]["passages"] == 1
    assert body["queue"] == {"waiting": 0, "reading": 0}
    assert body["model"]["llm"] == "fake" and body["model"]["cloud"] is False
    assert body["storage"]["app_folder_bytes"] > 0


def test_metrics_page(live_client: TestClient, settings: Settings) -> None:
    doc = add_pdf(live_client, settings, "a.pdf", "Metrics are numbers only.")
    wait_ingested(live_client)
    for _ in range(3):
        live_client.get(f"/api/documents/{doc}")
    live_client.post("/api/search", json={"query": "metrics numbers"})
    body = live_client.get("/api/system/metrics?window=1h").json()
    routes = {r["route"]: r for r in body["routes"]}
    assert routes["/api/documents/{doc_id}"]["requests"] == 3
    assert body["steps"]["search.total"]["count"] == 1
    assert body["steps"]["ingest.document"]["outcomes"] == {"ready": 1}
    assert "ingest.extract" in body["steps"] and "ingest.embed" in body["steps"]
    assert live_client.get("/api/system/metrics?window=2y").status_code == 422


def test_processing_page_and_timeline(live_client: TestClient, settings: Settings) -> None:
    good = add_pdf(live_client, settings, "good.pdf", "This one reads fine.")
    state = app_state(live_client)
    bad_path = settings.library / "broken.pdf"
    bad_path.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    from outlawz.library.documents import Document, Status
    from outlawz.library.uploads import sha256_file

    state.docs.add(
        Document(
            id="broken1",
            path="broken.pdf",
            sha256=sha256_file(bad_path),
            size=1,
            mtime=1,
            type="pdf",
            status=Status.queued,
        )
    )
    assert state.queue is not None
    state.queue.wake()
    wait_ingested(live_client)
    body = live_client.get("/api/system/processing").json()
    assert body["waiting"] == 0 and body["current"] == []
    failures = {f["code"]: f for f in body["failures"]}
    assert "damaged" in failures and failures["damaged"]["documents"][0]["name"] == "broken.pdf"
    assert body["time_per_document"]["count"] == 1
    timeline = live_client.get(f"/api/system/processing/{good}").json()
    run = timeline["runs"][0]
    assert run["status"] == "ok" and [s["name"] for s in run["spans"]] == [
        "extract",
        "chunk",
        "embed",
        "write",
    ]
    failed = live_client.get("/api/system/processing/broken1").json()
    assert failed["runs"][0]["status"] == "failed" and failed["runs"][0]["code"] == "damaged"


def test_traces_page(live_client: TestClient, settings: Settings) -> None:
    add_pdf(live_client, settings, "a.pdf", "Traces show where the time goes.")
    wait_ingested(live_client)
    state = app_state(live_client)
    state.settings.trace_sample = 1.0
    response = live_client.post("/api/search", json={"query": "where the time goes"})
    request_id = response.headers["x-request-id"]
    chat = live_client.post("/api/chats", json={}).json()["id"]
    live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "Where does the time go?"}
    )
    body = live_client.get("/api/system/traces?window=1h").json()
    kinds = {t["kind"] for t in body["traces"]}
    assert {"search", "answer", "ingest"} <= kinds
    assert "answer" in body["where_time_goes"]
    search_trace = live_client.get(f"/api/system/traces/{request_id}").json()
    assert search_trace["kind"] == "search"
    assert [s["name"] for s in search_trace["spans"]][:2] == ["embed question", "meaning search"]
    answer = next(t for t in body["traces"] if t["kind"] == "answer")
    assert next(s["name"] for s in answer["spans"]) == "retrieval"
    assert live_client.get("/api/system/traces/nope").status_code == 404


def test_update_notice_is_off_by_default(client: TestClient) -> None:
    assert client.get("/api/system/update").json() == {"enabled": False}


# ---------------------------------------------------------------- evaluations


def wait_run(c: TestClient, run_id: str, timeout: float = 60) -> dict:  # type: ignore[type-arg]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = c.get(f"/api/evals/{run_id}").json()
        if run["status"] != "running":
            return dict(run)
        time.sleep(0.1)
    raise TimeoutError


def test_evaluation_runs_and_compares(live_client: TestClient) -> None:
    started = live_client.post("/api/evals", json={"suites": ["chunking", "retrieval"]})
    assert started.status_code == 202
    first = wait_run(live_client, started.json()["id"])
    assert first["status"] == "done"
    assert first["results"]["chunking"]["fact_integrity"] == 1.0
    retrieval = first["results"]["retrieval"]
    assert set(retrieval) == {"keyword", "vector", "hybrid", "hybrid+rerank"}
    assert retrieval["keyword"]["hit@5"] == 1.0
    assert first["settings"]["embedder"] == "fake"
    assert first["changes"] == []

    # The benchmark never shows up in the library.
    assert live_client.get("/api/documents").json()["documents"] == []

    live_client.put(
        "/api/settings", json={"changes": {"keyword_weight": 0.0, "vector_weight": 1.0}}
    )
    second = wait_run(
        live_client, live_client.post("/api/evals", json={"suites": ["retrieval"]}).json()["id"]
    )
    assert second["compare_to"] == first["id"]
    assert all({"arrow", "better", "before", "after"} <= set(c) for c in second["changes"])
    listed = live_client.get("/api/evals").json()["runs"]
    assert [r["id"] for r in listed][:2] == [second["id"], first["id"]]
    assert live_client.post("/api/evals", json={"suites": ["vibes"]}).status_code == 422


def test_all_suites_run_with_the_fake_model(live_client: TestClient) -> None:
    run = wait_run(live_client, live_client.post("/api/evals", json={}).json()["id"])
    assert run["status"] == "done"
    answers = run["results"]["answers"]
    assert answers["model_errors"] == 0 and answers["correct_facts"] is not None
    assert run["results"]["data"]["valid_plan"] == 1.0  # the fake always plans a summary


def test_eval_command_reports_and_enforces_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OUTLAWZ_LLM", "fake")
    monkeypatch.setenv("OUTLAWZ_EMBEDDER", "fake")
    monkeypatch.setenv("OUTLAWZ_RERANKER", "lexical")
    monkeypatch.setenv("OUTLAWZ_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "run.json"
    code = main(
        ["eval", "--suite", "retrieval", "--json", str(out), "--min", "retrieval.keyword.hit@5=0.9"]
    )
    printed = capsys.readouterr().out
    assert code == 0 and "RETRIEVAL" in printed and "hybrid+rerank" in printed
    assert json.loads(out.read_text())["results"]["retrieval"]["keyword"]["hit@5"] == 1.0
    failing = main(["eval", "--suite", "chunking", "--max", "chunking.passages=1"])
    assert failing == 1
    assert "above the maximum" in capsys.readouterr().err
