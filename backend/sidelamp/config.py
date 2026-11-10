"""Settings: one typed class with a default for everything.

Where a value comes from, highest priority first:

1. an environment variable (``SIDELAMP_PORT=9000``),
2. the library's ``.sidelamp/settings.json`` (written by the Settings page),
3. the default below.

Which library to open is decided before the library's settings can be read: ``SIDELAMP_LIBRARY``,
else the choice saved by first-run setup in the OS data folder, else the default. API keys are kept
in the OS data folder (``secrets.json``), never in the library, so syncing the library never
uploads them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from sidelamp.paths import DataPaths, LibraryPaths, default_data_dir, default_library
from sidelamp.store.files import file_mode_private, read_json, write_json

ENV_PREFIX = "SIDELAMP_"

LLMProvider = Literal["ollama", "openai", "anthropic", "fake"]
EmbedProvider = Literal["fastembed", "ollama", "fake"]
RerankMode = Literal["model", "lexical", "off"]


class Settings(BaseSettings):
    """Every setting, with its default. See ``.env.example`` for one line on each."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, extra="ignore", validate_assignment=True, env_file=None
    )

    # Folders and server
    library: Path = Field(default_factory=default_library)
    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = Field(8642, ge=1, le=65535)
    open_browser: bool = True

    # Access
    access_token: str = ""
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    dev_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    docker: bool = Field(default_factory=lambda: Path("/.dockerenv").exists())

    # Library and reading
    max_upload_mb: int = Field(50, ge=1, le=2048)
    watch_interval: float = Field(3.0, ge=0.5, le=600)
    watch_depth: int = Field(8, ge=1, le=32)
    workers: int = Field(1, ge=1, le=16)
    retries: int = Field(3, ge=0, le=10)
    csv_rows: int = Field(5000, ge=10, le=1_000_000)
    chunk_tokens: int = Field(350, ge=50, le=2000)
    chunk_overlap: int = Field(50, ge=0, le=500)
    chunk_min: int = Field(40, ge=1, le=500)
    ocr: Literal["auto", "off"] = "auto"

    # Models
    embedder: EmbedProvider = "fastembed"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    reranker: RerankMode = "model"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    llm: LLMProvider = "ollama"
    llm_model: str = "llama3.1:8b"
    ollama_url: str = "http://127.0.0.1:11434"
    openai_url: str = "http://127.0.0.1:1234/v1"
    openai_key: str = ""
    anthropic_key: str = ""
    llm_timeout: float = Field(120.0, ge=5, le=1800)
    temperature: float = Field(0.1, ge=0, le=2)
    max_answer_tokens: int = Field(800, ge=64, le=8192)

    # Search
    vector_k: int = Field(40, ge=1, le=500)
    keyword_k: int = Field(40, ge=1, le=500)
    rerank_k: int = Field(30, ge=1, le=200)
    top_k: int = Field(8, ge=1, le=50)
    rrf_k: int = Field(60, ge=1, le=1000)
    vector_weight: float = Field(1.0, ge=0, le=10)
    keyword_weight: float = Field(1.0, ge=0, le=10)

    # Chat
    history_messages: int = Field(6, ge=0, le=40)
    context_tokens: int = Field(3200, ge=200, le=100_000)
    preferences: bool = True

    # Logs, backups, clean-up
    log_days: int = Field(30, ge=1, le=3650)
    trace_sample: float = Field(0.1, ge=0, le=1)
    slow_trace_ms: int = Field(2000, ge=1)
    backup_every: Literal["daily", "off"] = "daily"
    backup_keep: int = Field(7, ge=1, le=365)
    backup_documents: bool = False
    trash_days: int = Field(30, ge=1, le=3650)
    export_days: int = Field(7, ge=1, le=3650)
    update_check: bool = False
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    _locked: frozenset[str] = PrivateAttr(default_factory=frozenset)

    @field_validator("library", "data_dir", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        if isinstance(value, str | Path):
            return Path(os.path.expandvars(str(value))).expanduser()
        return value

    @field_validator("allowed_hosts", "dev_origins", mode="before")
    @classmethod
    def _split_list(cls, value: Any) -> Any:
        # Accept "a,b" from an env var as well as a JSON list.
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @field_validator("ollama_url", "openai_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    # ------------------------------------------------------------- helpers

    @property
    def locked(self) -> frozenset[str]:
        """Settings fixed by an environment variable (the UI shows them as locked)."""
        return self._locked

    @property
    def library_paths(self) -> LibraryPaths:
        return LibraryPaths(self.library)

    @property
    def data_paths(self) -> DataPaths:
        return DataPaths(self.data_dir)

    @property
    def is_cloud_llm(self) -> bool:
        """True when questions and passages leave this computer."""
        if self.llm == "anthropic":
            return True
        if self.llm == "openai":
            return not _is_local_url(self.openai_url)
        if self.llm == "ollama":
            return not _is_local_url(self.ollama_url)
        return False

    def public(self) -> dict[str, Any]:
        """Settings safe to send to the browser: secrets are replaced by whether they're set."""
        data = self.model_dump(mode="json")
        for key in SECRET_FIELDS:
            data[key] = bool(data[key])
        return data


SECRET_FIELDS = frozenset({"openai_key", "anthropic_key", "access_token"})
# Settings that only make sense per computer, or must be known before a library is opened.
MACHINE_FIELDS = frozenset({"library", "data_dir", "host", "port", "open_browser", "docker"})
EDITABLE_FIELDS = frozenset(Settings.model_fields) - MACHINE_FIELDS - {"access_token"}


def _is_local_url(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}:
        return True
    return host.startswith("127.") or host.endswith(".local")


def _machine_config(data_dir: Path) -> dict[str, Any]:
    raw = read_json(DataPaths(data_dir).config_file, {})
    return raw if isinstance(raw, dict) else {}


def load_settings(**overrides: Any) -> Settings:
    """Build the settings for this run: overrides > env vars > library settings file > defaults."""
    env_and_overrides = Settings(**overrides)
    locked = frozenset(env_and_overrides.model_fields_set) - set(overrides)
    set_explicitly = frozenset(env_and_overrides.model_fields_set)

    data_dir = env_and_overrides.data_dir
    values: dict[str, Any] = {}
    if "library" not in set_explicitly:
        chosen = _machine_config(data_dir).get("library")
        if isinstance(chosen, str) and chosen:
            values["library"] = chosen
    library = Path(values.get("library", env_and_overrides.library)).expanduser()

    saved = read_json(LibraryPaths(library).settings_file, {})
    if isinstance(saved, dict):
        values.update({k: v for k, v in saved.items() if k in EDITABLE_FIELDS - SECRET_FIELDS})
    secrets = read_json(DataPaths(data_dir).secrets_file, {})
    if isinstance(secrets, dict):
        values.update({k: v for k, v in secrets.items() if k in SECRET_FIELDS - {"access_token"}})
    values = {k: v for k, v in values.items() if k not in set_explicitly}

    try:
        settings = Settings(**values, **overrides)
    except ValueError:
        # A hand-edited settings file with a bad value must not stop the app from starting.
        settings = Settings(**overrides)
    settings._locked = locked
    return settings


def save_settings(current: Settings, changes: dict[str, Any]) -> Settings:
    """Validate and save changes made in the UI. Returns the new settings.

    Library-wide settings go to the library's ``settings.json``; keys go to ``secrets.json`` in
    the OS data folder. Settings fixed by an env var can't be changed here.
    """
    from sidelamp.errors import AppError

    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise AppError(
            "invalid_setting",
            f"These settings can't be changed here: {', '.join(sorted(unknown))}.",
        )
    blocked = set(changes) & current.locked
    if blocked:
        raise AppError(
            "setting_locked",
            f"These settings are fixed by an environment variable: {', '.join(sorted(blocked))}.",
        )
    merged = current.model_dump()
    merged.update(changes)
    try:
        updated = Settings(**merged)
    except ValueError as exc:
        raise AppError("invalid_setting", _first_error(exc)) from None
    updated._locked = current.locked

    lib = updated.library_paths
    saved = read_json(lib.settings_file, {})
    saved = saved if isinstance(saved, dict) else {}
    secrets_path = updated.data_paths.secrets_file
    secrets = read_json(secrets_path, {})
    secrets = secrets if isinstance(secrets, dict) else {}
    for key in changes:
        target = secrets if key in SECRET_FIELDS else saved
        target[key] = getattr(updated, key)
    if any(k not in SECRET_FIELDS for k in changes):
        write_json(lib.settings_file, _jsonable(saved))
    if any(k in SECRET_FIELDS for k in changes):
        write_json(secrets_path, secrets)
        file_mode_private(secrets_path)
    return updated


def save_library_choice(data_dir: Path, library: Path) -> None:
    """Remember which library this computer opens (first-run setup)."""
    paths = DataPaths(data_dir)
    config = _machine_config(data_dir)
    config["library"] = str(library.expanduser())
    write_json(paths.config_file, config)


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in data.items()}


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        items = errors()
        if items:
            loc = ".".join(str(p) for p in items[0].get("loc", ()))
            return f"{loc}: {items[0].get('msg', 'invalid value')}"
    return "That value isn't valid."
