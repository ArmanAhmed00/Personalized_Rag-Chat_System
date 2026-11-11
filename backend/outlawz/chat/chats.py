"""Chats: one JSON file per chat in ``.outlawz/chats/``.

Each assistant message keeps the sources it was given (with the quoted passage text), the
citations it used, flags from the citation check, token usage and timings. Deleting a document
removes it from every chat's document selection and wipes its quoted text from saved citations.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from outlawz.errors import NotFound
from outlawz.store.files import read_json, write_json

MessageStatus = Literal["complete", "interrupted", "error", "streaming"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class SavedSource(BaseModel):
    n: int
    doc_id: str
    document: str
    i: int
    label: str = ""
    text: str | None = ""
    page: int | None = None
    page_end: int | None = None
    section: str | None = None
    chapter: str | None = None
    rows: list[int] | None = None
    kind: str = "text"
    deleted: bool = False


class ChatMessage(BaseModel):
    id: str = Field(default_factory=new_id)
    role: Literal["user", "assistant"]
    content: str = ""
    created_at: float = Field(default_factory=time.time)
    status: MessageStatus = "complete"
    sources: list[SavedSource] = Field(default_factory=list)
    cited: list[int] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    query: str | None = None  # the standalone query used for retrieval
    usage: dict[str, int] | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    feedback: Literal["up", "down"] | None = None
    suggestion: dict[str, Any] | None = None  # a "remember that…" preference to confirm
    computed: dict[str, Any] | None = None  # a data-analysis result (table + chart)
    error: dict[str, str] | None = None


class Chat(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str = "New chat"
    titled: bool = False  # True once renamed by the user (then it's never auto-changed)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    documents: list[str] | None = None  # None = all documents
    messages: list[ChatMessage] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": len(self.messages),
            "documents": self.documents,
        }


class ChatStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def lock(self, chat_id: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(chat_id)
            if lock is None:
                lock = self._locks[chat_id] = threading.RLock()
            return lock

    def _path(self, chat_id: str) -> Path:
        if not chat_id.isalnum() or len(chat_id) > 40:
            raise NotFound("That chat")
        return self.folder / f"{chat_id}.json"

    def save(self, chat: Chat) -> Chat:
        with self.lock(chat.id):
            chat.updated_at = time.time()
            write_json(self._path(chat.id), chat.model_dump(mode="json"), indent=None)
        return chat

    def get(self, chat_id: str) -> Chat:
        try:
            raw = read_json(self._path(chat_id), None)
        except ValueError:
            raise NotFound("That chat") from None
        if raw is None:
            raise NotFound("That chat")
        try:
            return Chat.model_validate(raw)
        except ValidationError:
            raise NotFound("That chat") from None

    def all(self) -> list[Chat]:
        chats = []
        for path in self.folder.glob("*.json"):
            try:
                chats.append(Chat.model_validate(read_json(path, None)))
            except ValueError:  # includes ValidationError and broken JSON
                continue
        return sorted(chats, key=lambda c: c.updated_at, reverse=True)

    def create(self, title: str | None = None, documents: list[str] | None = None) -> Chat:
        chat = Chat(title=title or "New chat", titled=bool(title), documents=documents)
        return self.save(chat)

    def delete(self, chat_id: str) -> None:
        path = self._path(chat_id)
        with self.lock(chat_id):
            if not path.exists():
                raise NotFound("That chat")
            path.unlink()

    def update(self, chat_id: str, **changes: Any) -> Chat:
        with self.lock(chat_id):
            chat = self.get(chat_id)
            for key, value in changes.items():
                setattr(chat, key, value)
            return self.save(chat)

    def append(self, chat_id: str, message: ChatMessage) -> Chat:
        with self.lock(chat_id):
            chat = self.get(chat_id)
            chat.messages.append(message)
            if (
                message.role == "user"
                and not chat.titled
                and sum(1 for m in chat.messages if m.role == "user") == 1
            ):
                chat.title = _title_from(message.content)
            return self.save(chat)

    def replace_message(self, chat_id: str, message: ChatMessage) -> Chat:
        with self.lock(chat_id):
            chat = self.get(chat_id)
            for index, existing in enumerate(chat.messages):
                if existing.id == message.id:
                    chat.messages[index] = message
                    break
            else:
                chat.messages.append(message)
            return self.save(chat)

    def set_feedback(self, chat_id: str, message_id: str, value: str | None) -> ChatMessage:
        with self.lock(chat_id):
            chat = self.get(chat_id)
            for message in chat.messages:
                if message.id == message_id and message.role == "assistant":
                    message.feedback = value  # type: ignore[assignment]
                    self.save(chat)
                    return message
        raise NotFound("That answer")

    def forget_document(self, doc_id: str) -> int:
        """Remove a deleted document from every selection and wipe its quoted text."""
        changed = 0
        for path in self.folder.glob("*.json"):
            chat_id = path.stem
            with self.lock(chat_id):
                try:
                    chat = self.get(chat_id)
                except NotFound:
                    continue
                touched = False
                if chat.documents is not None and doc_id in chat.documents:
                    chat.documents = [d for d in chat.documents if d != doc_id]
                    touched = True
                for message in chat.messages:
                    for source in message.sources:
                        if source.doc_id == doc_id and not source.deleted:
                            source.text = None
                            source.deleted = True
                            touched = True
                if touched:
                    self.save(chat)
                    changed += 1
        return changed


def _title_from(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= 60 else text[:57].rstrip() + "…"
