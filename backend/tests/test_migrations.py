from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from outlawz.errors import AppError
from outlawz.store.files import read_json, write_json
from outlawz.store.migrations import CURRENT_FORMAT, ensure_format, read_format, write_format


def test_new_folder_is_stamped(tmp_path: Path) -> None:
    app = tmp_path / ".outlawz"
    app.mkdir()
    report = ensure_format(app, tmp_path / "backups")
    assert not report.migrated
    assert read_format(app) == CURRENT_FORMAT
    assert not (tmp_path / "backups").exists()


def test_current_format_is_left_alone(tmp_path: Path) -> None:
    app = tmp_path / ".outlawz"
    app.mkdir()
    write_format(app, CURRENT_FORMAT)
    assert not ensure_format(app, tmp_path / "backups").migrated


def test_old_format_backs_up_then_migrates_in_order(tmp_path: Path) -> None:
    app = tmp_path / ".outlawz"
    app.mkdir()
    write_format(app, 1)
    write_json(app / "documents.json", {"documents": [{"name": "a"}]})
    calls: list[int] = []

    def v1_to_v2(folder: Path) -> None:
        calls.append(1)
        data = read_json(folder / "documents.json")
        for doc in data["documents"]:
            doc["title"] = doc.pop("name")
        write_json(folder / "documents.json", data)

    def v2_to_v3(folder: Path) -> None:
        calls.append(2)
        write_json(folder / "new.json", {})

    report = ensure_format(
        app, tmp_path / "backups", current=3, migrations={1: v1_to_v2, 2: v2_to_v3}
    )
    assert report.steps == [1, 2]
    assert calls == [1, 2]
    assert read_format(app) == 3
    assert read_json(app / "documents.json") == {"documents": [{"title": "a"}]}
    assert report.backup is not None and report.backup.exists()
    with tarfile.open(report.backup) as tar:
        member = tar.extractfile(".outlawz/documents.json")
        assert member is not None
        assert b'"name": "a"' in member.read()  # the backup holds the old format


def test_crash_mid_upgrade_resumes(tmp_path: Path) -> None:
    app = tmp_path / ".outlawz"
    app.mkdir()
    write_format(app, 1)
    attempts = {"n": 0}

    def ok(folder: Path) -> None:
        write_json(folder / "step1.json", {})

    def flaky(folder: Path) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("power cut")
        write_json(folder / "step2.json", {})

    steps = {1: ok, 2: flaky}
    with pytest.raises(RuntimeError):
        ensure_format(app, tmp_path / "backups", current=3, migrations=steps)
    assert read_format(app) == 2  # step 1 was recorded
    report = ensure_format(app, tmp_path / "backups", current=3, migrations=steps)
    assert report.steps == [2]
    assert read_format(app) == 3


def test_newer_format_is_refused(tmp_path: Path) -> None:
    app = tmp_path / ".outlawz"
    app.mkdir()
    write_format(app, CURRENT_FORMAT + 5)
    with pytest.raises(AppError) as info:
        ensure_format(app, tmp_path / "backups")
    assert info.value.code == "newer_format"
    assert "Update Outlawz" in info.value.message


def test_missing_migration_is_refused_without_changes(tmp_path: Path) -> None:
    app = tmp_path / ".outlawz"
    app.mkdir()
    write_format(app, 1)
    with pytest.raises(AppError) as info:
        ensure_format(app, tmp_path / "backups", current=3, migrations={1: lambda p: None})
    assert info.value.code == "no_migration"
    assert read_format(app) == 1
