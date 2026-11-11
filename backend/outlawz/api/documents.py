"""Documents and the library: list, details, upload, delete (to Trash), read again, cancel, the
original file, single passages (for the citation panel), the folder tree and "Rebuild everything".
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from outlawz.api.deps import State
from outlawz.errors import AppError, NotFound
from outlawz.ingest.extractors import get_extractor, supported_extensions
from outlawz.ingest.pipeline import read_passages
from outlawz.library.documents import Document, Progress, Status, doc_type_for, new_doc_id
from outlawz.library.uploads import save_stream, sha256_file
from outlawz.store.files import remove_tree, write_bytes_atomic

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLES_FOLDER = "Samples"

router = APIRouter(prefix="/api", tags=["documents"])


def document_out(doc: Document, *, running: bool = False) -> dict[str, Any]:
    data = doc.model_dump(mode="json")
    data["name"] = doc.name
    data["folder"] = doc.folder
    extractor = get_extractor(doc.type)
    data["label"] = extractor.label if extractor else doc.type
    data["running"] = running
    return data


def _running(state: Any, doc_id: str) -> bool:
    return bool(state.queue is not None and state.queue.is_running(doc_id))


@router.get("/documents")
def list_documents(state: State) -> dict[str, Any]:
    docs = sorted(state.docs.all(), key=lambda d: d.path.lower())
    return {
        "documents": [document_out(d, running=_running(state, d.id)) for d in docs],
        "version": state.docs.version,
        "supported": list(supported_extensions()),
    }


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, state: State) -> dict[str, Any]:
    return document_out(state.require_document(doc_id), running=_running(state, doc_id))


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
        progress=Progress(message="Waiting to be read"),
    )
    state.docs.add(doc)
    state.events.emit("document_added", doc=doc, source="upload")
    if state.queue is not None:
        state.queue.wake()
    return document_out(doc)


@router.post("/documents/{doc_id}/reread")
def reread_document(doc_id: str, state: State) -> dict[str, Any]:
    state.require_document(doc_id)
    if state.queue is None:
        raise AppError("unavailable", "Reading isn't available right now.", 503)
    state.queue.request(doc_id, force=True)
    out = document_out(state.require_document(doc_id))
    state.events.emit("document_reread", doc_id=doc_id)
    return out


@router.post("/documents/{doc_id}/cancel")
def cancel_document(doc_id: str, state: State) -> dict[str, Any]:
    state.require_document(doc_id)
    cancelled = bool(state.queue and state.queue.cancel(doc_id))
    return {"cancelled": cancelled, "document": document_out(state.require_document(doc_id))}


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: str, state: State) -> FileResponse:
    doc = state.require_document(doc_id)
    path = state.document_file(doc)
    if not path.is_file():
        raise AppError("not_found", "The file is no longer in the library folder.", 404)
    extractor = get_extractor(doc.type)
    inline = bool(extractor and extractor.inline)
    return FileResponse(
        path,
        media_type=extractor.media_type if extractor else "application/octet-stream",
        filename=doc.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "content-security-policy": "default-src 'none'; frame-ancestors 'none'; "
            "img-src 'self' data:; style-src 'unsafe-inline'"
        },
    )


def passage_out(doc: Document, passage: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": doc.id,
        "document": doc.name,
        "type": doc.type,
        "i": passage.get("i"),
        "text": passage.get("text", ""),
        "kind": passage.get("kind", "text"),
        "page": passage.get("page"),
        "page_end": passage.get("page_end"),
        "section": passage.get("section"),
        "chapter": passage.get("chapter"),
        "rows": passage.get("rows"),
    }


@router.get("/documents/{doc_id}/passages/{index}")
def get_passage(doc_id: str, index: int, state: State) -> dict[str, Any]:
    doc = state.require_document(doc_id)
    passages = read_passages(state.lib.doc_dir(doc_id))
    if index < 0 or index >= len(passages):
        raise NotFound("That passage")
    return passage_out(doc, passages[index])


@router.get("/library/tree")
def library_tree(state: State) -> dict[str, Any]:
    root: dict[str, Any] = {"name": state.lib.root.name, "path": "", "folders": [], "documents": []}
    folders: dict[str, dict[str, Any]] = {"": root}

    def folder_for(path: str) -> dict[str, Any]:
        if path in folders:
            return folders[path]
        parent_path = PurePosixPath(path).parent.as_posix()
        parent = folder_for("" if parent_path == "." else parent_path)
        node = {"name": PurePosixPath(path).name, "path": path, "folders": [], "documents": []}
        parent["folders"].append(node)
        folders[path] = node
        return node

    for doc in sorted(state.docs.all(), key=lambda d: d.path.lower()):
        folder_for(doc.folder)["documents"].append(
            document_out(doc, running=_running(state, doc.id))
        )
    for node in folders.values():
        node["folders"].sort(key=lambda f: f["name"].lower())
    return {"root": str(state.lib.root), "tree": root}


@router.post("/library/rebuild")
async def rebuild_everything(state: State) -> dict[str, Any]:
    """Delete all processed data and read every document again from the original files."""
    docs = state.docs.all()
    for doc in docs:
        if state.queue is not None and state.queue.is_running(doc.id):
            state.queue.cancel(doc.id)
            # Let the pass stop before deleting its folder, so nothing is written into it after.
            await state.queue.wait_stopped(doc.id)
        await asyncio.to_thread(remove_tree, state.lib.doc_dir(doc.id))
    state.events.emit("library_rebuilt", count=len(docs))
    if state.queue is not None:
        for doc in docs:
            state.queue.request(doc.id, force=True)
    return {
        "queued": len(docs),
        "message": f"Reading {len(docs)} documents again. Search fills back in as each one "
        f"finishes.",
    }


def _add_samples(state: Any) -> list[Document]:
    added: list[Document] = []
    for source in sorted(SAMPLES_DIR.iterdir()):
        doc_type = doc_type_for(source.name)
        if doc_type is None or not source.is_file():
            continue
        rel = f"{SAMPLES_FOLDER}/{source.name}"
        if state.docs.by_path(rel) is not None:
            continue
        target = state.lib.root / SAMPLES_FOLDER / source.name
        if not target.exists():
            write_bytes_atomic(target, source.read_bytes())
        stat = target.stat()
        doc = Document(
            id=new_doc_id(),
            path=rel,
            sha256=sha256_file(target),
            size=stat.st_size,
            mtime=stat.st_mtime,
            type=doc_type,
            status=Status.queued,
            progress=Progress(message="Waiting to be read"),
        )
        state.docs.add(doc)
        state.events.emit("document_added", doc=doc, source="samples")
        added.append(doc)
    return added


@router.post("/library/samples", status_code=201)
async def add_samples(state: State) -> dict[str, Any]:
    """Copy the sample documents into a "Samples" folder in the library."""
    added = await asyncio.to_thread(_add_samples, state)
    if added:
        state.events.emit("samples_added", count=len(added))
    if state.queue is not None:
        state.queue.wake()
    message = (
        f"Added {len(added)} sample documents to the “{SAMPLES_FOLDER}” folder."
        if added
        else "The sample documents are already in your library."
    )
    return {"added": [document_out(d) for d in added], "message": message}
