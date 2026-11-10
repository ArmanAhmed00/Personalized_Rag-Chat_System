"""Where Sidelamp keeps things.

* The **library folder** holds the user's documents as ordinary files.
* The hidden **app folder** (``.sidelamp``) inside it holds all state, so the library backs up and
  moves as one folder.
* The **OS data folder** holds things that belong to this computer: the model cache, exports,
  backups, Trash, API keys and which library to open.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sidelamp import APP_NAME, SLUG

APP_DIR_NAME = ".sidelamp"


def default_data_dir() -> Path:
    """The per-user data folder for this OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / SLUG


def default_library() -> Path:
    return Path.home() / "Documents" / f"{APP_NAME} Library"


@dataclass(frozen=True)
class LibraryPaths:
    """The layout of a library and its hidden app folder."""

    root: Path

    @property
    def app(self) -> Path:
        return self.root / APP_DIR_NAME

    @property
    def format_file(self) -> Path:
        return self.app / "format.json"

    @property
    def lock_file(self) -> Path:
        return self.app / "lock"

    @property
    def documents_file(self) -> Path:
        return self.app / "documents.json"

    @property
    def docs_dir(self) -> Path:
        return self.app / "docs"

    @property
    def chats_dir(self) -> Path:
        return self.app / "chats"

    @property
    def preferences_file(self) -> Path:
        return self.app / "preferences.json"

    @property
    def settings_file(self) -> Path:
        return self.app / "settings.json"

    @property
    def logs_dir(self) -> Path:
        return self.app / "logs"

    @property
    def evals_dir(self) -> Path:
        return self.app / "evals"

    @property
    def tmp_dir(self) -> Path:
        """Staging area for atomic writes. Same disk as the library, so renames are atomic."""
        return self.app / "tmp"

    def doc_dir(self, doc_id: str) -> Path:
        return self.docs_dir / doc_id

    def ensure(self) -> None:
        for path in (
            self.root,
            self.app,
            self.docs_dir,
            self.chats_dir,
            self.logs_dir,
            self.evals_dir,
            self.tmp_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DataPaths:
    """The layout of the OS data folder."""

    root: Path

    @property
    def config_file(self) -> Path:
        """Which library this computer opens (set by first-run setup)."""
        return self.root / "config.json"

    @property
    def secrets_file(self) -> Path:
        """API keys. Kept out of the library so syncing the library never uploads them."""
        return self.root / "secrets.json"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def trash_dir(self) -> Path:
        return self.root / "trash"

    def ensure(self) -> None:
        for path in (
            self.root,
            self.models_dir,
            self.exports_dir,
            self.backups_dir,
            self.trash_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
