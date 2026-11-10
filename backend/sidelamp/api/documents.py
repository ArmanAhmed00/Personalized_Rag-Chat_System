"""Documents: list, details, upload, delete (to Trash) and the original file."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from sidelamp.api.deps import State
from sidelamp.errors import AppError
from sidelamp.library.documents import DocType, Document, Status, new_doc_id
from sidelamp.library.uploads import save_stream

router = APIRouter(prefix="/api", tags=["documents"])

_MEDIA_TYPES = {
    DocType.pdf: "application/pdf",
    DocType.epub: "application/epub+zip",
    DocType.csv: "text/csv; charset=utf-8",
}


def document_out(doc: Document) -> dict[str, Any]:
    data = doc.model_dump(mode="json")
    data["name"] = doc.name
    data["folder"] = doc.folder
    return data


@router.get("/documents")
def list_documents(state: State) -> dict[str, Any]:
    docs = sorted(state.docs.all(), key=lambda d: d.path.lower())
    return {"documents": [document_out(d) for d in docs], "version": state.docs.version}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, state: State) -> dict[str, Any]:
    return document_out(state.require_document(doc_id))


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, state: State) -> dict[str, Any]:
    doc = await asyncio.to_thread(state.delete_document, doc_id)
    return {"deleted": doc.id, "message": f"“{doc.name}” was moved to Trash."}


@router.post("/documents", status_code=201)
async def upload_document(request: Request, state: State) -> dict[str, Any]:
    max_bytes = state.settings.max_upload_mb * 1024 * 1024
    form = await request.form(max_files=1, max_fields=5)
    file = form.get("file")
    if not isinstance(file, UploadFile) or not file.filename:
        raise AppError("invalid_request", "Choose a file to upload.", 422)
    folder = form.get("folder")
    folder_value = folder if isinstance(folder, str) else ""
    try:
        saved = await asyncio.to_thread(
            save_stream,
            file.file,
            file.filename,
            state.lib.root,
            state.lib.tmp_dir,
            max_bytes=max_bytes,
            folder=folder_value,
        )
    finally:
        await file.close()
    stat = os.stat(saved.path)
    doc = Document(
        id=new_doc_id(),
        path=saved.rel,
        sha256=saved.sha256,
        size=saved.size,
        mtime=stat.st_mtime,
        type=saved.type,
        status=Status.queued,
    )
    doc.progress.message = "Waiting to be read"
    state.docs.add(doc)
    state.events.emit("document_added", doc=doc)
    return document_out(doc)


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: str, state: State) -> FileResponse:
    doc = state.require_document(doc_id)
    path = state.document_file(doc)
    if not path.is_file():
        raise AppError("not_found", "The file is no longer in the library folder.", 404)
    inline = doc.type == DocType.pdf
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[doc.type],
        filename=doc.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "content-security-policy": "default-src 'none'; frame-ancestors 'none'; "
            "img-src 'self' data:; style-src 'unsafe-inline'"
        },
    )
