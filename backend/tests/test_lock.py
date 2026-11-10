from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sidelamp.errors import LibraryLocked
from sidelamp.store import lock as lock_module
from sidelamp.store.lock import InstanceLock


def _write_lock(path: Path, **fields: object) -> None:
    info = {
        "pid": 1,
        "host": "elsewhere",
        "token": "t",
        "started_at": time.time(),
        "heartbeat_at": time.time(),
        "version": "0",
    }
    info.update(fields)
    path.write_text(json.dumps(info))


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_acquire_and_release(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with InstanceLock(path) as held:
        assert held.held
        assert path.exists()
    assert not path.exists()


def test_second_copy_is_refused_in_plain_words(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with InstanceLock(path), pytest.raises(LibraryLocked) as info:
        InstanceLock(path).acquire()
    assert "already open in another copy" in info.value.message


def test_stale_lock_from_dead_process_is_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    _write_lock(path, pid=_dead_pid(), host=lock_module._host())
    lock = InstanceLock(path)
    lock.acquire()
    assert json.loads(path.read_text())["token"] == lock.token
    lock.release()


def test_lock_from_other_computer_with_fresh_heartbeat_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    _write_lock(path, host="Arman's MacBook")
    with pytest.raises(LibraryLocked) as info:
        InstanceLock(path).acquire()
    assert "another computer" in info.value.message
    assert "Arman's MacBook" in info.value.message


def test_lock_from_other_computer_with_old_heartbeat_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    _write_lock(path, host="old laptop", heartbeat_at=time.time() - 600)
    InstanceLock(path).acquire()


def test_unreadable_lock_waits_for_grace_period(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    path.write_text("{not json")
    with pytest.raises(LibraryLocked):
        InstanceLock(path).acquire()
    old = time.time() - 60
    import os

    os.utime(path, (old, old))
    InstanceLock(path).acquire()


def test_heartbeat_notices_takeover(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    lock = InstanceLock(path)
    lock.acquire()
    assert lock.heartbeat()
    _write_lock(path, host="other", token="someone-else")
    assert not lock.heartbeat()
    lock.release()  # must not delete the other owner's lock
    assert path.exists()


def test_app_refuses_second_copy(tmp_path: Path) -> None:
    from sidelamp.config import load_settings
    from sidelamp.state import AppState

    settings = load_settings(library=tmp_path / "lib", data_dir=tmp_path / "data")
    first = AppState(settings)
    first.open()
    try:
        with pytest.raises(LibraryLocked):
            AppState(settings).open()
    finally:
        import asyncio

        asyncio.run(first.close())
    AppState(settings).open()
