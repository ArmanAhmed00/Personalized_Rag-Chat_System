from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sidelamp.config import load_settings, save_library_choice, save_settings
from sidelamp.errors import AppError
from sidelamp.store.files import read_json, write_json


def _base(tmp_path: Path) -> dict[str, Path]:
    return {"library": tmp_path / "lib", "data_dir": tmp_path / "data"}


def test_defaults(tmp_path: Path) -> None:
    s = load_settings(**_base(tmp_path))
    assert s.port == 8642
    assert s.host == "127.0.0.1"
    assert s.chunk_tokens == 350
    assert s.embed_model == "BAAI/bge-small-en-v1.5"
    assert not s.is_cloud_llm


def test_settings_file_overrides_defaults(tmp_path: Path) -> None:
    write_json(tmp_path / "lib" / ".sidelamp" / "settings.json", {"top_k": 5, "port": 1})
    s = load_settings(**_base(tmp_path))
    assert s.top_k == 5
    assert s.port == 8642  # machine settings can't come from the library file


def test_env_overrides_settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(tmp_path / "lib" / ".sidelamp" / "settings.json", {"top_k": 5})
    monkeypatch.setenv("SIDELAMP_TOP_K", "11")
    s = load_settings(**_base(tmp_path))
    assert s.top_k == 11
    assert "top_k" in s.locked


def test_env_list_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDELAMP_ALLOWED_HOSTS", "a.local, b.local")
    monkeypatch.setenv("SIDELAMP_DEV_ORIGINS", '["http://localhost:3000"]')
    s = load_settings(**_base(tmp_path))
    assert s.allowed_hosts == ["a.local", "b.local"]
    assert s.dev_origins == ["http://localhost:3000"]


def test_library_choice_from_setup(tmp_path: Path) -> None:
    data = tmp_path / "data"
    save_library_choice(data, tmp_path / "chosen")
    s = load_settings(data_dir=data)
    assert s.library == tmp_path / "chosen"


def test_bad_settings_file_value_does_not_crash(tmp_path: Path) -> None:
    write_json(tmp_path / "lib" / ".sidelamp" / "settings.json", {"top_k": "lots"})
    assert load_settings(**_base(tmp_path)).top_k == 8


def test_save_settings_splits_secrets(tmp_path: Path) -> None:
    s = load_settings(**_base(tmp_path))
    updated = save_settings(s, {"top_k": 4, "openai_key": "sk-test"})
    assert updated.top_k == 4
    lib_file = read_json(tmp_path / "lib" / ".sidelamp" / "settings.json")
    assert lib_file == {"top_k": 4}
    secrets = tmp_path / "data" / "secrets.json"
    assert read_json(secrets) == {"openai_key": "sk-test"}
    if os.name != "nt":
        assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    again = load_settings(**_base(tmp_path))
    assert again.top_k == 4 and again.openai_key == "sk-test"
    assert again.public()["openai_key"] is True


def test_save_settings_validates(tmp_path: Path) -> None:
    s = load_settings(**_base(tmp_path))
    with pytest.raises(AppError) as info:
        save_settings(s, {"top_k": 0})
    assert info.value.code == "invalid_setting"
    with pytest.raises(AppError):
        save_settings(s, {"port": 1})


def test_locked_setting_cannot_be_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDELAMP_TOP_K", "3")
    s = load_settings(**_base(tmp_path))
    with pytest.raises(AppError) as info:
        save_settings(s, {"top_k": 4})
    assert info.value.code == "setting_locked"


def test_cloud_detection(tmp_path: Path) -> None:
    assert load_settings(**_base(tmp_path), llm="anthropic").is_cloud_llm
    assert load_settings(
        **_base(tmp_path), llm="openai", openai_url="https://api.openai.com/v1"
    ).is_cloud_llm
    assert not load_settings(**_base(tmp_path), llm="openai").is_cloud_llm
