"""Chats: create, list, read, rename or change documents, delete, ask, and answer feedback.

``POST /api/chats/{id}/ask`` streams Server-Sent Events (``start``, ``sources``, ``delta``,
``done``, ``error``). With ``?stream=false`` it returns the finished answer as JSON instead.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sidelamp.api.deps import State
from sidelamp.chat.answer import AnswerRun
from sidelamp.errors import AppError

router = APIRouter(prefix="/api", tags=["chats"])

MAX_QUESTION = 4000


class NewChat(BaseModel):
    title: str | None = Field(None, max_length=120)
    documents: list[str] | None = Field(None, max_length=500)


class ChatChanges(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    documents: list[str] | None = Field(None, max_length=500)
    all_documents: bool = Field(False, description="Search every document again")


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)


class Feedback(BaseModel):
    value: Literal["up", "down"] | None


def _check_documents(state: Any, ids: list[str] | None) -> list[str] | None:
    if ids is None:
        return None
    unknown = [d for d in ids if state.docs.get(d) is None]
    if unknown:
        raise AppError(
            "unknown_document",
            "Some selected documents don't exist (or were deleted). Choose them again.",
            422,
        )
    return list(dict.fromkeys(ids))


@router.get("/chats")
def list_chats(state: State) -> dict[str, Any]:
    return {"chats": [c.summary() for c in state.chats.all()]}


@router.post("/chats", status_code=201)
def create_chat(body: NewChat, state: State) -> dict[str, Any]:
    chat = state.chats.create(body.title, _check_documents(state, body.documents))
    return chat.model_dump(mode="json")


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, state: State) -> dict[str, Any]:
    return state.chats.get(chat_id).model_dump(mode="json")


@router.patch("/chats/{chat_id}")
def change_chat(chat_id: str, body: ChatChanges, state: State) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if body.title is not None:
        changes["title"] = " ".join(body.title.split())
        changes["titled"] = True
    if body.all_documents:
        changes["documents"] = None
    elif body.documents is not None:
        changes["documents"] = _check_documents(state, body.documents)
    chat = state.chats.update(chat_id, **changes)
    return chat.model_dump(mode="json")


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, state: State) -> dict[str, Any]:
    if chat_id in state.answering:
        raise AppError("busy", "This chat is still answering. Stop it first.", 409)
    state.chats.delete(chat_id)
    state.events.emit("chat_deleted", chat_id=chat_id)
    return {"deleted": chat_id}


@router.put("/chats/{chat_id}/messages/{message_id}/feedback")
def set_feedback(chat_id: str, message_id: str, body: Feedback, state: State) -> dict[str, Any]:
    message = state.chats.set_feedback(chat_id, message_id, body.value)
    state.events.emit("feedback", chat_id=chat_id, value=body.value)
    return message.model_dump(mode="json")


def _sse(name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n".encode()


@router.post("/chats/{chat_id}/ask", response_model=None)
async def ask(
    chat_id: str,
    body: Ask,
    state: State,
    stream: bool = Query(True, description="Stream Server-Sent Events (default) or return JSON"),
) -> StreamingResponse | dict[str, Any]:
    state.chats.get(chat_id)  # 404 before anything starts
    if chat_id in state.answering:
        raise AppError("busy", "This chat is still answering the last question.", 409)
    run = AnswerRun(state, chat_id, body.question.strip())

    if not stream:
        state.answering.add(chat_id)
        try:
            done: dict[str, Any] | None = None
            async for event in run.events():
                if event.name == "error":
                    raise AppError(
                        str(event.data.get("code", "model_error")),
                        str(event.data.get("message", "The model couldn't answer.")),
                        502,
                        message_id=event.data.get("message_id"),
                    )
                if event.name == "done":
                    done = event.data["message"]
            return {"message": done}
        finally:
            state.answering.discard(chat_id)

    async def body_iter() -> AsyncIterator[bytes]:
        state.answering.add(chat_id)
        try:
            async for event in run.events():
                yield _sse(event.name, event.data)
        finally:
            state.answering.discard(chat_id)

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )
