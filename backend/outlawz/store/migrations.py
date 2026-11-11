"""The on-disk format version and the migrations between versions.

The app folder records its format in ``format.json``. When a newer Outlawz opens an older
library, it backs up the app folder first and then runs each migration in order, recording the
new version after every step. Each migration must be safe to run twice, so a crash in the middle
simply resumes where it stopped.

To change the on-disk format: bump ``CURRENT_FORMAT``, add a function to ``MIGRATIONS`` keyed by
the version it upgrades *from*, and add a test that upgrades a folder written in the old format.
"""

from __future__ import annotations

import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from outlawz import __version__
from outlawz.errors import AppError
from outlawz.store.files import read_json, write_json

CURRENT_FORMAT = 1

Migration = Callable[[Path], None]

# version N -> N + 1. Empty until the format first changes.
MIGRATIONS: dict[int, Migration] = {}


@dataclass
class MigrationReport:
    from_version: int
    to_version: int
    backup: Path | None = None
    steps: list[int] = field(default_factory=list)

    @property
    def migrated(self) -> bool:
        return bool(self.steps)


def read_format(app_dir: Path) -> int | None:
    raw = read_json(app_dir / "format.json", None)
    if not isinstance(raw, dict):
        return None
    try:
        return int(raw["format"])
    except (KeyError, TypeError, ValueError):
        return None


def write_format(app_dir: Path, version: int) -> None:
    write_json(app_dir / "format.json", {"format": version, "written_by": __version__})


def backup_app_dir(app_dir: Path, backups_dir: Path, from_version: int, to_version: int) -> Path:
    """A plain .tar.gz of the app folder, taken before any migration touches it."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backups_dir / f"pre-upgrade-v{from_version}-to-v{to_version}-{stamp}.tar.gz"
    partial = target.with_name(target.name + ".partial")
    with tarfile.open(partial, "w:gz") as tar:
        for item in sorted(app_dir.rglob("*")):
            rel = item.relative_to(app_dir)
            if (rel.parts and rel.parts[0] == "tmp") or item.name == "lock":
                continue
            if item.is_file():
                tar.add(item, arcname=str(Path(app_dir.name) / rel))
    partial.replace(target)
    return target


def ensure_format(
    app_dir: Path,
    backups_dir: Path,
    *,
    current: int = CURRENT_FORMAT,
    migrations: dict[int, Migration] | None = None,
) -> MigrationReport:
    """Bring the app folder up to ``current``. A brand-new folder is simply stamped."""
    steps = MIGRATIONS if migrations is None else migrations
    found = read_format(app_dir)
    if found is None:
        if _looks_used(app_dir):
            found = 0  # written before format versioning existed
        else:
            write_format(app_dir, current)
            return MigrationReport(current, current)
    if found > current:
        raise AppError(
            "newer_format",
            f"This library was last opened by a newer version of Outlawz (format {found}; "
            f"this version understands up to {current}). Update Outlawz to open it. "
            f"Nothing was changed.",
            409,
        )
    report = MigrationReport(found, current)
    if found == current:
        return report
    missing = [v for v in range(found, current) if v not in steps]
    if missing:
        raise AppError(
            "no_migration",
            f"Outlawz can't upgrade this library from format {found}. Nothing was changed.",
            409,
        )
    report.backup = backup_app_dir(app_dir, backups_dir, found, current)
    for version in range(found, current):
        steps[version](app_dir)
        write_format(app_dir, version + 1)
        report.steps.append(version)
    return report


def _looks_used(app_dir: Path) -> bool:
    chats = app_dir / "chats"
    return (app_dir / "documents.json").exists() or (chats.is_dir() and any(chats.iterdir()))
