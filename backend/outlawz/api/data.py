"""Data & backups: chat exports, "Download everything", and backups (create, verify, restore)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from outlawz.api.deps import State
from outlawz.backups import (
    create_backup,
    list_backups,
    resolve_backup,
    restore_backup,
    verify_backup,
)
from outlawz.errors import AppError, NotFound
from outlawz.exports import chat_json, chat_markdown, chat_pdf, safe_name
from outlawz.state import AppState

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/chats/{chat_id}/export", response_model=None)
async def export_chat(
    chat_id: str, state: State, format: Literal["md", "json", "pdf"] = Query("md")
) -> Response:
    chat = state.chats.get(chat_id)
    name = f"{safe_name(chat.title)}.{format}"
    if format == "md":
        body: bytes = chat_markdown(chat).encode()
        media = "text/markdown; charset=utf-8"
    elif format == "json":
        body = chat_json(chat).encode()
        media = "application/json"
    else:
        body = await asyncio.to_thread(chat_pdf, chat)
        media = "application/pdf"
    return Response(
        body, media_type=media, headers={"content-disposition": f'attachment; filename="{name}"'}
    )


# ---------------------------------------------------------------- download everything


@router.post("/exports", status_code=202)
def start_export(state: State) -> dict[str, Any]:
    return state.exports.start()


@router.get("/exports/{export_id}")
def export_status(export_id: str, state: State) -> dict[str, Any]:
    meta = state.exports.get(export_id)
    if meta is None:
        raise NotFound("That export")
    return meta


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, state: State) -> FileResponse:
    meta = state.exports.get(export_id)
    if meta is None or meta.get("status") != "ready":
        raise AppError("not_ready", "That export isn't ready (or has expired).", 404)
    path = state.exports.zip_path(export_id)
    if not path.is_file():
        raise AppError("not_ready", "That export has expired. Start a new one.", 404)
    return FileResponse(path, media_type="application/zip", filename=path.name)


# ---------------------------------------------------------------- backups


class NewBackup(BaseModel):
    include_documents: bool = False
    include_vectors: bool = True


class Restore(BaseModel):
    replace: bool = False


@router.get("/backups")
def backups(state: State) -> dict[str, Any]:
    s = state.settings
    return {
        "backups": list_backups(state.data.backups_dir),
        "folder": str(state.data.backups_dir),
        "schedule": {
            "every": s.backup_every,
            "keep": s.backup_keep,
            "documents": s.backup_documents,
        },
        "last_maintenance": state.last_maintenance,
    }


@router.post("/backups", status_code=201)
async def new_backup(body: NewBackup, state: State) -> dict[str, Any]:
    state.docs.flush()
    path = await asyncio.to_thread(
        create_backup,
        state.settings.library,
        state.data.backups_dir,
        include_documents=body.include_documents,
        include_vectors=body.include_vectors,
    )
    state.events.emit("backup_created", backup=path.name, documents=body.include_documents)
    return {"name": path.name, "size": path.stat().st_size}


@router.post("/backups/{name}/verify")
async def verify(name: str, state: State) -> dict[str, Any]:
    path = resolve_backup(state.data.backups_dir, name)
    report = await asyncio.to_thread(verify_backup, path)
    state.events.emit("backup_verified", backup=name, ok=report.ok)
    return {
        "ok": report.ok,
        "message": report.message,
        "files": report.files,
        "bytes": report.bytes,
        "manifest": report.manifest,
    }


@router.post("/backups/{name}/restore")
async def restore(name: str, body: Restore, request: Request, state: State) -> dict[str, Any]:
    """Close the library, restore it from the backup, and open it again."""
    path = resolve_backup(state.data.backups_dir, name)
    report = await asyncio.to_thread(verify_backup, path)
    if not report.ok:
        raise AppError("backup_invalid", report.message, 422)
    settings = state.settings
    background, watch = getattr(request.app.state, "background", (True, True))
    await state.close()
    error: AppError | None = None
    try:
        await asyncio.to_thread(
            restore_backup,
            path,
            settings.library,
            replace=body.replace,
            safety_dir=state.data.backups_dir,
        )
    except AppError as exc:
        error = exc
    fresh = AppState(settings)
    fresh.open()
    if background:
        fresh.start_background(watch=watch)
    request.app.state.outlawz = fresh
    if error is not None:
        raise error
    fresh.events.emit("backup_restored", backup=name, replaced=body.replace)
    return {
        "restored": name,
        "files": report.files,
        "documents": len(fresh.docs.all()),
        "message": "The library was restored. Documents without search data are being read again.",
    }
