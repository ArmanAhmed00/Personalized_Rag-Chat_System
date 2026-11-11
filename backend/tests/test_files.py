from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from outlawz.store import files
from outlawz.store.files import (
    KeyedLocks,
    append_jsonl,
    atomic_dir,
    read_json,
    read_jsonl,
    recover_dirs,
    remove_tree,
    write_json,
)


def test_write_json_roundtrip_and_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "a" / "data.json"
    write_json(target, {"x": 1, "path": Path("a/b")})
    assert read_json(target) == {"x": 1, "path": "a/b"}
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]


def test_failed_write_keeps_old_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "data.json"
    write_json(target, {"version": 1})

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)
    with pytest.raises(OSError):
        write_json(target, {"version": 2})
    monkeypatch.undo()
    assert read_json(target) == {"version": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_unserialisable_value_doesnt_touch_file(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    write_json(target, {"ok": True})
    with pytest.raises(TypeError):
        write_json(target, {"bad": object()})
    assert read_json(target) == {"ok": True}


def test_read_json_default(tmp_path: Path) -> None:
    assert read_json(tmp_path / "missing.json", {"d": 1}) == {"d": 1}
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "missing.json")


def test_jsonl_ignores_half_written_last_line(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    append_jsonl(log, {"n": 1})
    append_jsonl(log, {"n": 2})
    with open(log, "ab") as fh:
        fh.write(b'{"n": 3, "trunc')  # crash mid-append
    assert read_jsonl(log) == [{"n": 1}, {"n": 2}]


def test_append_after_broken_line_starts_fresh_line(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    append_jsonl(log, {"n": 1})
    with open(log, "ab") as fh:
        fh.write(b'{"n": 2, "tru')
    append_jsonl(log, {"n": 3})
    assert read_jsonl(log) == [{"n": 1}, {"n": 3}]
    assert log.read_bytes().endswith(b'{"n":3}\n')


def test_read_jsonl_missing_file(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_atomic_dir_success_replaces_existing(tmp_path: Path) -> None:
    final = tmp_path / "docs" / "abc"
    staging = tmp_path / "tmp"
    with atomic_dir(final, staging) as work:
        (work / "one.txt").write_text("first")
    with atomic_dir(final, staging) as work:
        (work / "two.txt").write_text("second")
    assert sorted(p.name for p in final.iterdir()) == ["two.txt"]
    assert list(staging.iterdir()) == []
    assert sorted(p.name for p in final.parent.iterdir()) == ["abc"]


def test_atomic_dir_failure_leaves_old_output(tmp_path: Path) -> None:
    final = tmp_path / "docs" / "abc"
    staging = tmp_path / "tmp"
    with atomic_dir(final, staging) as work:
        (work / "good.txt").write_text("good")
    with pytest.raises(RuntimeError), atomic_dir(final, staging) as work:
        (work / "half.txt").write_text("half")
        raise RuntimeError("killed")
    assert sorted(p.name for p in final.iterdir()) == ["good.txt"]
    assert list(staging.iterdir()) == []


def test_recover_dirs_after_crash_between_renames(tmp_path: Path) -> None:
    parent = tmp_path / "docs"
    staging = tmp_path / "tmp"
    (parent / "abc.old-1234").mkdir(parents=True)
    (parent / "abc.old-1234" / "data.txt").write_text("old")
    (parent / "done").mkdir()
    (parent / "done.old-9999").mkdir()
    (parent / "gone.del-0000").mkdir()
    (staging / "abc.partial").mkdir(parents=True)
    repairs = recover_dirs(parent, staging)
    assert repairs == 4
    assert (parent / "abc" / "data.txt").read_text() == "old"
    assert sorted(p.name for p in parent.iterdir()) == ["abc", "done"]
    assert list(staging.iterdir()) == []


def test_remove_tree(tmp_path: Path) -> None:
    folder = tmp_path / "x"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "f").write_text("1")
    remove_tree(folder)
    assert not folder.exists()
    assert list(tmp_path.iterdir()) == []
    remove_tree(folder)  # missing is fine


def test_concurrent_threads_never_see_a_broken_file(tmp_path: Path) -> None:
    target = tmp_path / "shared.json"
    write_json(target, {"n": 0, "pad": "x" * 10_000})
    errors: list[Exception] = []
    stop = threading.Event()

    def writer(i: int) -> None:
        for n in range(30):
            write_json(target, {"n": n, "writer": i, "pad": "x" * 10_000})

    def reader() -> None:
        while not stop.is_set():
            try:
                json.loads(target.read_text())
            except Exception as exc:  # pragma: no cover - would mean a torn write
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    watcher = threading.Thread(target=reader)
    watcher.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    watcher.join()
    assert errors == []
    assert not [p for p in tmp_path.iterdir() if p.name != "shared.json"]


@pytest.mark.anyio
async def test_keyed_locks_serialise_read_modify_write(tmp_path: Path) -> None:
    target = tmp_path / "counter.json"
    write_json(target, {"count": 0})
    locks = KeyedLocks()

    async def increment() -> None:
        async with locks(target):
            data = read_json(target)
            await asyncio.sleep(0)  # give other tasks a chance to interleave
            data["count"] += 1
            write_json(target, data)

    await asyncio.gather(*(increment() for _ in range(50)))
    assert read_json(target)["count"] == 50
    assert locks(target) is locks(str(target))


def test_write_is_durable_flag(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    append_jsonl(log, {"a": 1}, sync=True)
    assert os.path.getsize(log) > 0
