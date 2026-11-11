"""Exports, "Download everything", backups (create, verify, restore), maintenance and doctor."""

from __future__ import annotations

import io
import json
import os
import tarfile
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from outlawz import maintenance
from outlawz.backups import (
    create_backup,
    list_backups,
    repair_interrupted_restore,
    restore_backup,
    verify_backup,
)
from outlawz.cli import main
from outlawz.config import Settings
from outlawz.doctor import FAIL, OK, WARN, check_llm, check_lock, print_report, run_checks
from outlawz.errors import AppError
from outlawz.llm.fake import FakeLLM
from tests.conftest import app_state, make_client, wait_ingested
from tests.test_api_analysis import scripted, upload_csv
from tests.test_api_search import add_pdf


def wait_for(fn, timeout: float = 20.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError


@pytest.fixture
def filled(live_client: TestClient, settings: Settings) -> dict[str, str]:
    doc = add_pdf(
        live_client, settings, "report.pdf", "The refund window is 30 days from delivery."
    )
    csv_doc = upload_csv(live_client)
    wait_ingested(live_client)
    chat = live_client.post("/api/chats", json={}).json()["id"]
    live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "How long is the refund window?"}
    )
    data_chat = live_client.post("/api/chats", json={"documents": [csv_doc]}).json()["id"]
    app_state(live_client).llm_override = scripted(
        ['{"op": "aggregate", "agg": "sum", "column": "revenue", "by": ["region"]}']
    )
    live_client.post(
        f"/api/chats/{data_chat}/ask?stream=false", json={"question": "Total revenue by region?"}
    )
    app_state(live_client).llm_override = None
    live_client.post("/api/preferences/items", json={"text": "Keep answers short."})
    live_client.put(
        "/api/settings", json={"changes": {"top_k": 6, "openai_key": "sk-never-exported"}}
    )
    return {"doc": doc, "csv": csv_doc, "chat": chat, "data_chat": data_chat}


# ---------------------------------------------------------------- exports


def test_chat_exports(live_client: TestClient, filled: dict[str, str]) -> None:
    md = live_client.get(f"/api/chats/{filled['chat']}/export?format=md")
    assert md.headers["content-disposition"].endswith('.md"')
    assert "[1] report.pdf, Page 1" in md.text
    assert "30 days" in md.text
    data_md = live_client.get(f"/api/chats/{filled['data_chat']}/export?format=md").text
    assert "| region | sum of revenue |" in data_md and "| East | 100 |" in data_md
    as_json = live_client.get(f"/api/chats/{filled['chat']}/export?format=json").json()
    assert as_json["messages"][1]["sources"][0]["document"] == "report.pdf"
    pdf = live_client.get(f"/api/chats/{filled['data_chat']}/export?format=pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    import pymupdf

    with pymupdf.open(stream=pdf.content, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "Total revenue by region?" in text and "East" in text
    assert live_client.get(f"/api/chats/{filled['chat']}/export?format=docx").status_code == 422


def test_download_everything(
    live_client: TestClient, filled: dict[str, str], settings: Settings
) -> None:
    started = live_client.post("/api/exports")
    assert started.status_code == 202
    export_id = started.json()["id"]
    meta = wait_for(
        lambda: (
            (m := live_client.get(f"/api/exports/{export_id}").json())["status"] != "building" and m
        )
    )
    assert meta["status"] == "ready" and meta["documents"] == 2 and meta["chats"] == 2
    download = live_client.get(f"/api/exports/{export_id}/download")
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        names = set(zf.namelist())
        assert {
            "documents/report.pdf",
            "documents/sales.csv",
            "preferences.json",
            "settings.json",
            "README.txt",
        } <= names
        assert sum(1 for n in names if n.startswith("chats/") and n.endswith(".md")) == 2
        assert sum(1 for n in names if n.startswith("chats/") and n.endswith(".json")) == 2
        assert json.loads(zf.read("settings.json"))["top_k"] == 6
        everything = b"".join(zf.read(n) for n in names)
    assert b"sk-never-exported" not in everything
    # The client collapses "../" to "/etc/download": a web-app address, not an export.
    escaped = live_client.get("/api/exports/../../etc/download")
    assert "zip" not in escaped.headers.get("content-type", "")
    assert live_client.get("/api/exports/zzzzzzzzzzzz").status_code == 404
    events = [e["action"] for e in live_client.get("/api/system/activity").json()["events"]]
    assert "export_created" in events


# ---------------------------------------------------------------- backups


def test_backup_verify_restore_round_trip(settings: Settings, tmp_path: Path) -> None:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "keep.pdf", "The lighthouse keeper logs every passing ship.")
        wait_ingested(c)
        chat = c.post("/api/chats", json={}).json()["id"]
        c.post(
            f"/api/chats/{chat}/ask?stream=false", json={"question": "What does the keeper log?"}
        )
        made = c.post("/api/backups", json={"include_documents": True}).json()
        listed = c.get("/api/backups").json()["backups"]
        assert listed[0]["name"] == made["name"] and listed[0]["kind"] == "manual"
        checked = c.post(f"/api/backups/{made['name']}/verify").json()
        assert checked["ok"] and checked["manifest"]["includes_documents"] is True
    backup = settings.data_dir / "backups" / made["name"]

    fresh = tmp_path / "restored"
    report = restore_backup(backup, fresh)
    assert report.ok
    assert (fresh / "keep.pdf").exists()
    assert (fresh / ".outlawz" / "chats" / f"{chat}.json").exists()
    restored = settings.model_copy(update={"library": fresh})
    with make_client(restored) as c:
        hits = c.post("/api/search", json={"query": "lighthouse keeper"}).json()["hits"]
        assert hits and hits[0]["document"] == "keep.pdf"
        assert c.get(f"/api/chats/{chat}").status_code == 200


def test_backup_without_vectors_is_rebuilt_after_restore(
    settings: Settings, tmp_path: Path
) -> None:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "a.pdf", "Vectors are left out of this smaller backup.")
        wait_ingested(c)
    backup = create_backup(
        settings.library, tmp_path / "b", include_documents=True, include_vectors=False
    )
    with tarfile.open(backup) as tar:
        assert not any(n.endswith("vectors.npy") for n in tar.getnames())
    target = tmp_path / "lib2"
    restore_backup(backup, target)
    with make_client(settings.model_copy(update={"library": target}), background=True) as c:
        wait_ingested(c)
        hits = c.post("/api/search", json={"query": "smaller backup"}).json()["hits"]
        assert hits and hits[0]["document"] == "a.pdf"


def _rewrite(backup: Path, edit) -> Path:  # type: ignore[no-untyped-def]
    """Copy a backup, letting ``edit(name, data)`` change, drop (None) or keep each member."""
    out = backup.with_name("tampered.tar.gz")
    with tarfile.open(backup) as src, tarfile.open(out, "w:gz") as dst:
        for member in src:
            handle = src.extractfile(member)
            data = handle.read() if handle else b""
            changed = edit(member.name, data)
            if changed is None:
                continue
            info = tarfile.TarInfo(member.name)
            info.size = len(changed)
            dst.addfile(info, io.BytesIO(changed))
    return out


@pytest.fixture
def backup(settings: Settings, tmp_path: Path) -> Path:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "a.pdf", "Original content.")
        wait_ingested(c)
    return create_backup(settings.library, tmp_path / "b", include_documents=True)


def test_tampering_is_detected_before_anything_is_written(backup: Path, tmp_path: Path) -> None:
    changed = _rewrite(
        backup, lambda n, d: d.replace(b"Original", b"Doctored") if n.endswith(".jsonl") else d
    )
    report = verify_backup(changed)
    assert not report.ok and "was changed after the backup was made" in report.message
    target = tmp_path / "target"
    with pytest.raises(AppError):
        restore_backup(changed, target)
    assert not (target / ".outlawz").exists() and not (target / "a.pdf").exists()
    dropped = _rewrite(backup, lambda n, d: None if n.endswith("documents.json") else d)
    assert "missing" in verify_backup(dropped).message
    extra = _with_member(backup, tarfile.TarInfo("app/extra.txt"), b"surprise")
    assert "doesn't list" in verify_backup(extra).message
    assert verify_backup(Path(os.devnull)).ok is False


def _with_member(backup: Path, info: tarfile.TarInfo, data: bytes = b"x") -> Path:
    out = backup.with_name("evil.tar.gz")
    with tarfile.open(backup) as src, tarfile.open(out, "w:gz") as dst:
        for member in src:
            handle = src.extractfile(member)
            dst.addfile(member, io.BytesIO(handle.read() if handle else b""))
        info.size = len(data) if info.isfile() else 0
        dst.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return out


@pytest.mark.parametrize(
    "name",
    [
        "app/../../escape.txt",
        "/etc/escape.txt",
        "library/../../escape.txt",
        "other/file.txt",
        "app/a\\..\\..\\x",
    ],
)
def test_path_traversal_in_restore_is_refused(backup: Path, tmp_path: Path, name: str) -> None:
    evil = _with_member(backup, tarfile.TarInfo(name))
    report = verify_backup(evil)
    assert not report.ok
    with pytest.raises(AppError):
        restore_backup(evil, tmp_path / "target")
    assert not (tmp_path / "escape.txt").exists()


def test_links_in_restore_are_refused(backup: Path, tmp_path: Path) -> None:
    link = tarfile.TarInfo("app/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    report = verify_backup(_with_member(backup, link))
    assert not report.ok and "link or special file" in report.message


def test_restore_refuses_a_library_that_is_not_empty(
    backup: Path, settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(AppError) as err:
        restore_backup(backup, settings.library)
    assert err.value.code == "library_not_empty"
    safety = tmp_path / "safety"
    restore_backup(backup, settings.library, replace=True, safety_dir=safety)
    assert [b["kind"] for b in list_backups(safety)] == ["before restore"]


def test_an_interrupted_restore_is_repaired(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    aside = lib / ".outlawz.restore-old-1234abcd"
    aside.mkdir(parents=True)
    (aside / "documents.json").write_text('{"documents": []}')
    (lib / ".outlawz-restore-deadbeef").mkdir()
    assert repair_interrupted_restore(lib) == 2
    assert (lib / ".outlawz" / "documents.json").exists()
    assert not (lib / ".outlawz-restore-deadbeef").exists()


def test_restore_through_the_api_swaps_the_library(settings: Settings) -> None:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "before.pdf", "This document existed before the backup.")
        wait_ingested(c)
        name = c.post("/api/backups", json={"include_documents": True}).json()["name"]
        doc = c.get("/api/documents").json()["documents"][0]["id"]
        c.delete(f"/api/documents/{doc}")
        assert c.get("/api/documents").json()["documents"] == []
        refused = c.post(f"/api/backups/{name}/restore", json={"replace": False})
        assert refused.status_code in (200, 409)
        restored = c.post(f"/api/backups/{name}/restore", json={"replace": True}).json()
        assert restored["documents"] == 1
        wait_ingested(c)
        hits = c.post("/api/search", json={"query": "existed before the backup"}).json()["hits"]
        assert hits and hits[0]["document"] == "before.pdf"
        assert c.post("/api/backups/..%2F..%2Fx.tar.gz/restore", json={}).status_code in (400, 404)
        actions = [e["action"] for e in c.get("/api/system/activity").json()["events"]]
        assert "backup_restored" in actions


def test_backup_and_restore_commands(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with make_client(settings, background=True) as c:
        add_pdf(c, settings, "cli.pdf", "Backed up from the command line.")
        wait_ingested(c)
    out = tmp_path / "out"
    assert (
        main(["backup", "--library", str(settings.library), "--documents", "--out", str(out)]) == 0
    )
    archive = next(out.glob("*.tar.gz"))
    assert main(["verify", str(archive)]) == 0
    target = tmp_path / "cli-restore"
    assert main(["restore", str(archive), "--library", str(target)]) == 0
    assert (target / "cli.pdf").exists()
    assert main(["restore", str(archive), "--library", str(target)]) == 1  # not empty now
    assert "Replace" in capsys.readouterr().err


# ---------------------------------------------------------------- maintenance


def test_maintenance_cleans_up_and_backs_up(live_client: TestClient, settings: Settings) -> None:
    state = app_state(live_client)
    trash = settings.data_dir / "trash" / "20200101-000000-aaaaaa"
    trash.mkdir(parents=True)
    (trash / "trash.json").write_text(
        json.dumps({"original": "old.pdf", "deleted_at": time.time() - 40 * 86400})
    )
    exports = settings.data_dir / "exports"
    (exports / "aaaaaaaaaaaa.json").write_text(
        json.dumps({"id": "aaaaaaaaaaaa", "status": "ready", "created": time.time() - 30 * 86400})
    )
    (exports / "outlawz-export-aaaaaaaaaaaa.zip").write_bytes(b"PK")
    logs = settings.library / ".outlawz" / "logs"
    (logs / "metrics-2020-01-01.jsonl").write_text('{"t": 1}\n')
    for i in range(9):
        (settings.data_dir / "backups" / f"outlawz-auto-2020010{i}-000000-abcd.tar.gz").write_bytes(
            b"x"
        )
        os.utime(
            settings.data_dir / "backups" / f"outlawz-auto-2020010{i}-000000-abcd.tar.gz",
            (1e9 + i, 1e9 + i),
        )
    report = maintenance.run_once(state)
    assert report.trash == 1 and report.exports == 1 and report.logs >= 1
    assert report.backup is not None and report.backup.startswith("outlawz-auto-")
    assert (
        len([b for b in list_backups(settings.data_dir / "backups") if b["kind"] == "scheduled"])
        == 7
    )
    assert not trash.exists() and not (logs / "metrics-2020-01-01.jsonl").exists()
    again = maintenance.run_once(state)
    assert again.backup is None  # one a day
    state.settings.backup_every = "off"


# ---------------------------------------------------------------- doctor


def test_doctor_checks(settings: Settings) -> None:
    checks = {c.name: c for c in run_checks(settings)}
    assert checks["Python"].status == OK and checks["Packages"].status == OK
    assert checks["Folders"].status == OK
    assert checks["Model"].status == OK  # the fake model
    assert checks["Web app"].status in (OK, WARN)

    import httpx

    def down(*args: object, **kwargs: object) -> object:
        raise httpx.ConnectError("refused")

    ollama = settings.model_copy(update={"llm": "ollama", "llm_model": "llama3.1:8b"})
    result = check_llm(ollama, get=down)
    assert result.status == WARN and "ollama.com" in result.fix

    class Tags:
        def json(self) -> dict[str, object]:
            return {"models": [{"name": "qwen:7b"}]}

    missing = check_llm(ollama, get=lambda *a, **k: Tags())
    assert missing.fix == "Run: ollama pull llama3.1:8b"
    no_key = check_llm(settings.model_copy(update={"llm": "anthropic", "anthropic_key": ""}))
    assert no_key.status == FAIL


def test_doctor_reports_a_held_lock(settings: Settings) -> None:
    with make_client(settings):
        assert check_lock(settings).status == WARN
    assert check_lock(settings).status == OK


def test_doctor_command_exit_codes(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OUTLAWZ_LLM", "fake")
    monkeypatch.setenv("OUTLAWZ_DATA_DIR", str(settings.data_dir))
    assert main(["doctor", "--library", str(settings.library)]) == 0
    printed = capsys.readouterr().out
    assert "✓ Python" in printed and "Everything needed is in place" in printed
    from outlawz.doctor import Check

    assert print_report([Check("X", FAIL, "broken", "fix it")]) == 1
    assert "→ fix it" in capsys.readouterr().out


def test_fake_llm_is_unused_here() -> None:
    assert FakeLLM().provider == "fake"
