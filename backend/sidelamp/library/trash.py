"""Sidelamp's Trash, in the OS data folder. Deleting a document in the app moves its file here;
the maintenance loop empties items older than ``trash_days``."""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from sidelamp.store.files import read_json, write_json


def move_to_trash(src: Path, trash_dir: Path, *, original: str) -> Path:
    """Move ``src`` into its own folder in the Trash, with a note of where it came from."""
    slot = trash_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    slot.mkdir(parents=True)
    dest = slot / src.name
    shutil.move(str(src), str(dest))
    write_json(slot / "trash.json", {"original": original, "deleted_at": time.time()})
    return dest


def list_trash(trash_dir: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if not trash_dir.exists():
        return items
    for slot in sorted(trash_dir.iterdir()):
        meta = read_json(slot / "trash.json", None)
        if isinstance(meta, dict):
            items.append({"slot": slot.name, **meta})
    return items


def purge_trash(trash_dir: Path, older_than_days: float, *, now: float | None = None) -> int:
    """Permanently delete Trash items older than the given age. Returns how many."""
    if not trash_dir.exists():
        return 0
    cutoff = (now or time.time()) - older_than_days * 86_400
    removed = 0
    for slot in trash_dir.iterdir():
        if not slot.is_dir():
            continue
        meta = read_json(slot / "trash.json", {})
        deleted_at = meta.get("deleted_at") if isinstance(meta, dict) else None
        stamp = float(deleted_at) if isinstance(deleted_at, int | float) else slot.stat().st_mtime
        if stamp < cutoff:
            shutil.rmtree(slot, ignore_errors=True)
            removed += 1
    return removed
