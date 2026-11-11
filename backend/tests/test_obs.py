"""Metrics, traces and the hash-chained activity log, plus the rule that logs hold numbers and ids,
never content."""

from __future__ import annotations

import json
import random
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from outlawz.config import Settings
from outlawz.obs.activity import ActivityLog
from outlawz.obs.daily import DailyLog
from outlawz.obs.metrics import Metrics, percentiles
from outlawz.obs.traces import Tracer, spans_in_order
from tests.conftest import app_state, wait_ingested
from tests.test_api_search import add_pdf

# ---------------------------------------------------------------- metrics


def test_percentiles() -> None:
    values = [float(v) for v in range(1, 101)]
    result = percentiles(values)
    assert result["p50"] == 50.5 and result["p95"] == 95.05 and result["p99"] == 99.01
    assert percentiles([])["p50"] is None


def test_metrics_summary_by_route(tmp_path: Path) -> None:
    metrics = Metrics(tmp_path)
    for ms in (10, 20, 30, 40):
        metrics.request("GET", "/api/documents/{doc_id}", 200, ms)
    metrics.request("GET", "/api/documents/{doc_id}", 500, 99)
    metrics.request("POST", "/api/search", 422, 5)
    metrics.request("GET", "/api/health", 200, 1)  # not recorded: it's polled every minute
    metrics.step("chat.answer", 1200, status="complete")
    metrics.step("chat.tokens", 0, prompt_tokens=300, completion_tokens=40)
    metrics.flush()
    summary = metrics.summary(3600)
    routes = {(r["method"], r["route"]): r for r in summary["routes"]}
    doc = routes[("GET", "/api/documents/{doc_id}")]
    assert doc["requests"] == 5 and doc["errors"] == 1 and doc["p50"] == 30.0
    assert routes[("POST", "/api/search")]["refused"] == 1
    assert ("GET", "/api/health") not in routes
    assert summary["steps"]["chat.answer"]["count"] == 1
    assert summary["steps"]["chat.tokens"]["prompt_tokens"]["total"] == 300
    assert sum(b["requests"] for b in summary["timeline"]) == 6
    files = list(tmp_path.glob("metrics-*.jsonl"))
    assert len(files) == 1


def test_metric_steps_drop_long_text(tmp_path: Path) -> None:
    metrics = Metrics(tmp_path)
    metrics.step("x", 1, note="a sentence that is far too long to be a code and might be content")
    metrics.flush()
    record = json.loads(next(tmp_path.glob("metrics-*.jsonl")).read_text())
    assert "note" not in record


def test_daily_log_prunes_old_days(tmp_path: Path) -> None:
    log = DailyLog(tmp_path, "metrics")
    old = time.time() - 40 * 86400
    log.append([{"t": old, "x": 1}, {"t": time.time(), "x": 2}])
    assert len(log.files()) == 2
    removed = log.prune(30)
    assert len(removed) == 1 and [r["x"] for r in log.read()] == [2]


# ---------------------------------------------------------------- traces


def test_traces_keep_errors_and_slow_ones_and_sample_the_rest(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, sample=lambda: 0.0, slow_ms=lambda: 2000, rng=random.Random(1))
    assert tracer.record("search", 10, []) is False  # normal and fast: sampled out at 0%
    assert tracer.record("answer", 2500, []) is True  # slow
    assert tracer.record("answer", 10, [], status="error", code="timeout") is True
    assert tracer.record("ingest", 10, [], always=True) is True
    tracer.flush()
    kept = tracer.recent(time.time() - 60)
    assert {t["kind"] for t in kept} == {"answer", "ingest"}
    everything = Tracer(tmp_path / "all", sample=lambda: 1.0, slow_ms=lambda: 2000)
    assert everything.record("search", 10, []) is True


def test_where_the_time_goes(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, sample=lambda: 1.0, slow_ms=lambda: 99999)
    spans = spans_in_order([("retrieval", 20), ("model", 80), ("skipped", None)])
    assert spans == [
        {"name": "retrieval", "start_ms": 0.0, "ms": 20.0},
        {"name": "model", "start_ms": 20.0, "ms": 80.0},
    ]
    tracer.record("answer", 100, spans, trace_id="abc")
    tracer.record("answer", 100, spans)
    view = tracer.where_time_goes(time.time() - 60)["answer"]
    assert view["traces"] == 2 and view["steps"][0] == {
        "name": "model",
        "ms": 160.0,
        "share": 0.8,
        "mean_ms": 80.0,
    }
    tracer.flush()
    assert tracer.get("abc", time.time() - 60) is not None


# ---------------------------------------------------------------- activity log


def test_activity_chain_checks_out(tmp_path: Path) -> None:
    log = ActivityLog(tmp_path)
    for i in range(5):
        log.record("document_added", doc_id=f"d{i}")
    result = log.check()
    assert result["ok"] and result["events"] == 5
    assert [e["seq"] for e in log.query()] == [5, 4, 3, 2, 1]
    assert log.query(action="chat_deleted") == []
    # A fresh instance continues the chain from the files.
    ActivityLog(tmp_path).record("chat_deleted", chat_id="c1")
    assert ActivityLog(tmp_path).check()["events"] == 6


def _lines(tmp_path: Path) -> tuple[Path, list[str]]:
    path = next(tmp_path.glob("activity-*.jsonl"))
    return path, path.read_text().splitlines()


def test_a_changed_event_is_found(tmp_path: Path) -> None:
    log = ActivityLog(tmp_path)
    for i in range(4):
        log.record("document_added", doc_id=f"d{i}")
    path, lines = _lines(tmp_path)
    event = json.loads(lines[2])
    event["ids"]["doc_id"] = "someone-else"
    lines[2] = json.dumps(event)
    path.write_text("\n".join(lines) + "\n")
    result = ActivityLog(tmp_path).check()
    assert not result["ok"]
    assert result["problem"]["kind"] == "changed" and result["problem"]["seq"] == 3
    assert "Everything before it (2 events) checks out." in result["message"]


def test_a_missing_event_is_found(tmp_path: Path) -> None:
    log = ActivityLog(tmp_path)
    for i in range(4):
        log.record("document_added", doc_id=f"d{i}")
    path, lines = _lines(tmp_path)
    del lines[1]
    path.write_text("\n".join(lines) + "\n")
    result = ActivityLog(tmp_path).check()
    assert result["problem"]["kind"] == "missing" and result["problem"]["seq"] == 2


def test_a_half_written_last_line_is_not_tampering(tmp_path: Path) -> None:
    log = ActivityLog(tmp_path)
    log.record("document_added", doc_id="d1")
    path, _ = _lines(tmp_path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "t": 1, "act')  # crash mid-append
    assert ActivityLog(tmp_path).check()["ok"]
    ActivityLog(tmp_path).record("document_added", doc_id="d2")  # starts on a fresh line
    assert ActivityLog(tmp_path).check() | {"message": ""} == {
        "ok": True,
        "events": 2,
        "last_seq": 2,
        "problem": None,
        "message": "",
    }


def test_pruning_keeps_the_rest_of_the_chain_valid(tmp_path: Path) -> None:
    log = ActivityLog(tmp_path)
    for i in range(3):
        log.record("document_added", doc_id=f"d{i}")
    old_day = date.today() - timedelta(days=500)
    old_path = log.log.path_for(old_day)
    _, current = log.log.files()[0]
    current.rename(old_path)
    log = ActivityLog(tmp_path)
    log.record("chat_deleted", chat_id="c")
    assert log.prune(365) == 1
    result = ActivityLog(tmp_path).check()
    assert result["ok"] and result["events"] == 1 and result["last_seq"] == 4


def test_csv_export_and_ids_only(tmp_path: Path) -> None:
    log = ActivityLog(tmp_path)
    log.record("settings_changed", keys=["top_k", "temperature"], text={"nested": "dropped"})
    events = log.query()
    assert events[0]["ids"] == {"keys": ["top_k", "temperature"]}
    csv_text = log.to_csv(events)
    assert csv_text.splitlines()[0] == "seq,time_utc,action,ids,hash"
    assert "settings_changed" in csv_text


# ---------------------------------------------------------------- through the app


def test_logs_never_hold_content(live_client: TestClient, settings: Settings) -> None:
    secret = "Zanzibarquokka"
    doc = add_pdf(live_client, settings, "private.pdf", f"The {secret} protocol starts at dawn.")
    wait_ingested(live_client)
    state = app_state(live_client)
    state.settings.trace_sample = 1.0
    chat = live_client.post("/api/chats", json={}).json()["id"]
    live_client.post(
        f"/api/chats/{chat}/ask?stream=false",
        json={"question": f"When does the {secret} protocol start?"},
    )
    live_client.post("/api/search", json={"query": f"{secret} protocol"})
    live_client.post("/api/preferences/items", json={"text": f"Mention {secret} often."})
    live_client.get(f"/api/documents/{doc}")
    live_client.delete(f"/api/chats/{chat}")
    assert state.obs is not None
    state.obs.flush()
    logs = list((settings.library / ".outlawz" / "logs").glob("*.jsonl"))
    assert {p.name.split("-")[0] for p in logs} >= {"metrics", "traces", "activity"}
    for path in logs:
        text = path.read_text(encoding="utf-8")
        assert secret.lower() not in text.lower(), path.name
        assert "dawn" not in text and "protocol" not in text
        assert f"/api/documents/{doc}" not in text  # route templates, never real ids in routes
    metrics = settings.library / ".outlawz" / "logs"
    assert any("/api/documents/{doc_id}" in p.read_text() for p in metrics.glob("metrics-*.jsonl"))


def test_activity_records_the_listed_actions(live_client: TestClient, settings: Settings) -> None:
    doc = add_pdf(live_client, settings, "a.pdf", "Some text here for the test.")
    wait_ingested(live_client)
    live_client.post(f"/api/documents/{doc}/reread")
    wait_ingested(live_client)
    chat = live_client.post("/api/chats", json={}).json()["id"]
    live_client.delete(f"/api/chats/{chat}")
    live_client.put("/api/preferences", json={"enabled": False})
    live_client.put("/api/settings", json={"changes": {"top_k": 6}})
    live_client.delete(f"/api/documents/{doc}")
    events = live_client.get("/api/system/activity").json()["events"]
    actions = [e["action"] for e in reversed(events)]
    assert actions == [
        "document_added",
        "document_reread",
        "chat_deleted",
        "preferences_changed",
        "settings_changed",
        "document_deleted",
    ]
    assert events[-1]["ids"] == {"doc_id": doc, "type": "pdf", "source": "upload"}
    assert live_client.get("/api/system/activity/check").json()["ok"] is True
    filtered = live_client.get("/api/system/activity?action=chat_deleted").json()["events"]
    assert [e["ids"]["chat_id"] for e in filtered] == [chat]
    csv_response = live_client.get("/api/system/activity.csv")
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert len(csv_response.text.strip().splitlines()) == 7
    assert live_client.get("/api/system/activity?action=hack").status_code == 422
