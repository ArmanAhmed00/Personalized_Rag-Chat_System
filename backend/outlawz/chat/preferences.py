"""Saved preferences: style guidance the user chose to keep across chats.

* A chat message like "Remember that I like bullet points" or "From now on, answer in French"
  creates a *suggestion*. It's used only after the user clicks Save.
* Saved preferences go into the prompt as style guidance, never as evidence.
* The user can switch them off, and add, edit, delete or clear items.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from outlawz.errors import AppError, NotFound
from outlawz.store.files import read_json, write_json

MAX_ITEMS = 50
MAX_LENGTH = 300

_TRIGGER = re.compile(
    r"^\s*(?:please\s+)?(?:(?P<remember>remember(?:\s+that)?|keep in mind(?:\s+that)?|note that)"
    r"|(?P<from_now>from now on|going forward|in future|in the future)|(?P<always>always|never))"
    r"[\s,:]+(?P<rest>.+)$",
    re.IGNORECASE | re.S,
)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Preference(BaseModel):
    id: str = Field(default_factory=new_id)
    text: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class Suggestion(BaseModel):
    id: str = Field(default_factory=new_id)
    text: str
    chat_id: str | None = None
    created_at: float = Field(default_factory=time.time)


class PreferenceFile(BaseModel):
    enabled: bool = True
    items: list[Preference] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)


def detect_suggestion(message: str) -> str | None:
    """Turn "Remember that I prefer short answers." into "I prefer short answers."."""
    match = _TRIGGER.match(message.strip())
    if not match:
        return None
    rest = match.group("rest").strip()
    if match.group("always"):
        rest = f"{match.group('always').capitalize()} {rest}"
    rest = rest[0].upper() + rest[1:] if rest else rest
    if len(rest.split()) < 2:
        return None
    if not rest.endswith((".", "!", "?")):
        rest += "."
    return rest[:MAX_LENGTH]


def _clean(text: str) -> str:
    value = " ".join(text.split())
    if not value:
        raise AppError("invalid_request", "A preference can't be empty.", 422)
    if len(value) > MAX_LENGTH:
        raise AppError("invalid_request", f"Keep a preference under {MAX_LENGTH} characters.", 422)
    return value


class PreferenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> PreferenceFile:
        try:
            raw = read_json(self.path, None)
        except ValueError:  # hand-edited into invalid JSON: start fresh rather than fail
            return PreferenceFile()
        if not isinstance(raw, dict):
            return PreferenceFile()
        try:
            return PreferenceFile.model_validate(raw)
        except ValueError:
            return PreferenceFile()

    def _save(self, data: PreferenceFile) -> PreferenceFile:
        write_json(self.path, data.model_dump(mode="json"))
        return data

    def active(self) -> list[str]:
        """What goes into the prompt: nothing if switched off."""
        data = self.load()
        return [p.text for p in data.items] if data.enabled else []

    def set_enabled(self, enabled: bool) -> PreferenceFile:
        with self._lock:
            data = self.load()
            data.enabled = enabled
            return self._save(data)

    def add(self, text: str) -> Preference:
        with self._lock:
            data = self.load()
            if len(data.items) >= MAX_ITEMS:
                raise AppError("too_many", f"You can save up to {MAX_ITEMS} preferences.", 409)
            item = Preference(text=_clean(text))
            data.items.append(item)
            self._save(data)
            return item

    def edit(self, item_id: str, text: str) -> Preference:
        with self._lock:
            data = self.load()
            for item in data.items:
                if item.id == item_id:
                    item.text = _clean(text)
                    item.updated_at = time.time()
                    self._save(data)
                    return item
        raise NotFound("That preference")

    def delete(self, item_id: str) -> None:
        with self._lock:
            data = self.load()
            kept = [p for p in data.items if p.id != item_id]
            if len(kept) == len(data.items):
                raise NotFound("That preference")
            data.items = kept
            self._save(data)

    def clear(self) -> int:
        with self._lock:
            data = self.load()
            count = len(data.items)
            data.items = []
            self._save(data)
            return count

    def suggest(self, text: str, chat_id: str | None = None) -> Suggestion:
        with self._lock:
            data = self.load()
            suggestion = Suggestion(text=_clean(text), chat_id=chat_id)
            data.suggestions = [*data.suggestions[-19:], suggestion]
            self._save(data)
            return suggestion

    def accept(self, suggestion_id: str) -> Preference:
        with self._lock:
            data = self.load()
            match = next((s for s in data.suggestions if s.id == suggestion_id), None)
            if match is None:
                raise NotFound("That suggestion")
            data.suggestions = [s for s in data.suggestions if s.id != suggestion_id]
            self._save(data)
        return self.add(match.text)

    def dismiss(self, suggestion_id: str) -> None:
        with self._lock:
            data = self.load()
            kept = [s for s in data.suggestions if s.id != suggestion_id]
            if len(kept) == len(data.suggestions):
                raise NotFound("That suggestion")
            data.suggestions = kept
            self._save(data)

    def public(self) -> dict[str, Any]:
        return self.load().model_dump(mode="json")
