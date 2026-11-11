"""Exports.

* A chat as Markdown, JSON or PDF. Citations become "[1] report.pdf, Page 2", and computed answers
  include their table.
* "Download everything": a ZIP of the original files, every chat (Markdown and JSON), the saved
  preferences and the settings (never API keys), built in the background into the OS data folder.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from outlawz import APP_NAME, __version__
from outlawz.chat.chats import Chat, SavedSource
from outlawz.config import SECRET_FIELDS
from outlawz.errors import AppError
from outlawz.store.files import read_json, write_json
from outlawz.tabular import fmt_number

if TYPE_CHECKING:
    from outlawz.state import AppState

MAX_TABLE_ROWS = 50


def export_label(source: SavedSource) -> str:
    parts = [source.document]
    if source.page and source.page_end and source.page_end != source.page:
        parts.append(f"Pages {source.page}–{source.page_end}")
    elif source.page:
        parts.append(f"Page {source.page}")
    if source.rows:
        first, last = source.rows[0], source.rows[-1]
        parts.append(f"Rows {first}–{last}" if first != last else f"Row {first}")
    where = " › ".join(t for t in (source.chapter, source.section) if t)
    if where and source.kind not in ("rows", "schema", "profile"):
        parts.append(where)
    return ", ".join(parts)


def _cell(value: Any) -> str:
    if isinstance(value, float | int) and not isinstance(value, bool):
        return fmt_number(value)
    return "" if value is None else str(value).replace("|", "\\|")


def _table_rows(computed: dict[str, Any]) -> tuple[list[str], list[list[str]], int]:
    result = computed.get("result") or {}
    headers = [str(c.get("name", "")) for c in result.get("columns", [])]
    rows = [[_cell(v) for v in row] for row in result.get("rows", [])]
    return headers, rows[:MAX_TABLE_ROWS], len(rows)


def _when(timestamp: float) -> str:
    return time.strftime("%d %b %Y, %H:%M", time.localtime(timestamp))


def chat_markdown(chat: Chat) -> str:
    lines = [f"# {chat.title}", "", f"_Exported from {APP_NAME} on {_when(time.time())}._", ""]
    for message in chat.messages:
        if message.role == "user":
            lines += [f"**You** · {_when(message.created_at)}", "", message.content.strip(), ""]
            continue
        lines += [f"**{APP_NAME}** · {_when(message.created_at)}", ""]
        if message.status == "interrupted":
            lines += ["_This answer was interrupted._", ""]
        if message.status == "error":
            lines += [
                f"_The answer failed: {(message.error or {}).get('message', 'unknown error')}_",
                "",
            ]
        if message.content:
            lines += [message.content.strip(), ""]
        if message.computed:
            headers, rows, total = _table_rows(message.computed)
            title = (message.computed.get("result") or {}).get("title", "Computed result")
            lines += [
                f"**{title}** (computed from {message.computed.get('document', 'the table')})",
                "",
            ]
            if headers:
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("|" + "---|" * len(headers))
                lines += ["| " + " | ".join(r) + " |" for r in rows]
                if total > len(rows):
                    lines.append(f"\n_{total - len(rows)} more rows not shown._")
                lines.append("")
        cited = [s for s in message.sources if s.n in message.cited] or (
            message.sources if message.cited else []
        )
        if cited:
            lines.append("Sources:")
            for source in cited:
                note = " (document deleted)" if source.deleted else ""
                lines.append(f"- [{source.n}] {export_label(source)}{note}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def chat_json(chat: Chat) -> str:
    data = chat.model_dump(mode="json")
    data["exported_by"] = f"{APP_NAME} {__version__}"
    data["exported_at"] = time.time()
    return json.dumps(data, ensure_ascii=False, indent=2)


_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.4; color: #14213d; }
h1 { font-size: 17pt; margin-bottom: 4pt; }
p.meta { color: #5c6b84; font-size: 8.5pt; }
p.who { font-weight: bold; margin-top: 12pt; margin-bottom: 2pt; }
p.note { color: #5c6b84; font-style: italic; }
p.sources { font-size: 9pt; color: #3d4a63; }
table { border-collapse: collapse; margin: 4pt 0; font-size: 9pt; }
td, th { border: 1px solid #c9d1df; padding: 2pt 4pt; }
th { background-color: #eef1f6; }
"""


def _p(text: str, cls: str = "") -> str:
    body = html.escape(text).replace("\n", "<br/>")
    return f'<p class="{cls}">{body}</p>' if cls else f"<p>{body}</p>"


def chat_html(chat: Chat) -> str:
    parts = [
        f"<h1>{html.escape(chat.title)}</h1>",
        _p(f"Exported from {APP_NAME} on {_when(time.time())}", "meta"),
    ]
    for message in chat.messages:
        who = "You" if message.role == "user" else APP_NAME
        parts.append(_p(f"{who} · {_when(message.created_at)}", "who"))
        if message.status == "interrupted":
            parts.append(_p("This answer was interrupted.", "note"))
        if message.content:
            for paragraph in re.split(r"\n{2,}", message.content.strip()):
                parts.append(_p(paragraph))
        if message.computed:
            headers, rows, total = _table_rows(message.computed)
            if headers:
                head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
                body = "".join(
                    "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>"
                    for r in rows
                )
                parts.append(f"<table><tr>{head}</tr>{body}</table>")
                if total > len(rows):
                    parts.append(_p(f"{total - len(rows)} more rows not shown.", "note"))
        cited = [s for s in message.sources if s.n in message.cited]
        if cited:
            parts.append(
                _p("Sources: " + "; ".join(f"[{s.n}] {export_label(s)}" for s in cited), "sources")
            )
    return "<body>" + "".join(parts) + "</body>"


def chat_pdf(chat: Chat) -> bytes:
    import io

    import pymupdf

    from outlawz.ingest.extractors.pdf import MUPDF_LOCK

    with MUPDF_LOCK:
        buffer = io.BytesIO()
        story = pymupdf.Story(html=chat_html(chat), user_css=_CSS)
        writer = pymupdf.DocumentWriter(buffer)
        page = pymupdf.paper_rect("a4")
        where = pymupdf.Rect(page.x0 + 54, page.y0 + 54, page.x1 - 54, page.y1 - 54)
        more = True
        while more:
            device = writer.begin_page(page)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
        writer.close()
        return buffer.getvalue()


def safe_name(text: str, fallback: str = "chat") -> str:
    name = re.sub(r"[^\w\- ]+", "", text, flags=re.UNICODE).strip().replace(" ", "-")
    return (name[:60] or fallback).strip("-") or fallback


# ---------------------------------------------------------------- download everything


class ExportStore:
    """Background "Download everything" exports in the OS data folder."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self._lock = threading.Lock()

    @property
    def folder(self) -> Path:
        return self.state.data.exports_dir

    def _meta_path(self, export_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", export_id):
            from outlawz.errors import NotFound

            raise NotFound("That export")
        return self.folder / f"{export_id}.json"

    def get(self, export_id: str) -> dict[str, Any] | None:
        data = read_json(self._meta_path(export_id), None)
        return data if isinstance(data, dict) else None

    def zip_path(self, export_id: str) -> Path:
        self._meta_path(export_id)
        return self.folder / f"outlawz-export-{export_id}.zip"

    def start(self) -> dict[str, Any]:
        export_id = uuid.uuid4().hex[:12]
        meta = {"id": export_id, "status": "building", "created": time.time()}
        write_json(self._meta_path(export_id), meta)
        thread = threading.Thread(
            target=self._build_safely, args=(export_id,), daemon=True, name=f"export-{export_id}"
        )
        thread.start()
        return meta

    def _build_safely(self, export_id: str) -> None:
        meta_path = self._meta_path(export_id)
        meta = read_json(meta_path, {})
        try:
            counts = self.build(export_id)
            meta.update(
                status="ready",
                finished=time.time(),
                size=self.zip_path(export_id).stat().st_size,
                **counts,
            )
            self.state.events.emit(
                "export_created",
                export_id=export_id,
                documents=counts["documents"],
                chats=counts["chats"],
            )
        except Exception as exc:  # reported in the export's status
            meta.update(status="failed", error=type(exc).__name__, finished=time.time())
        write_json(meta_path, meta)

    def build(self, export_id: str) -> dict[str, int]:
        state = self.state
        target = self.zip_path(export_id)
        partial = target.with_name(target.name + ".partial")
        documents = chats = 0
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for doc in state.docs.all():
                try:
                    path = state.document_file(doc)
                except AppError:  # the file left the library: nothing to export
                    path = None
                if path is not None and path.is_file():
                    zf.write(path, f"documents/{doc.path}")
                    documents += 1
            for chat in state.chats.all():
                base = f"chats/{safe_name(chat.title)}-{chat.id}"
                zf.writestr(f"{base}.md", chat_markdown(chat))
                zf.writestr(f"{base}.json", chat_json(chat))
                chats += 1
            zf.writestr(
                "preferences.json",
                json.dumps(state.preferences.public(), indent=2, ensure_ascii=False),
            )
            library_settings = read_json(state.lib.settings_file, {})
            if isinstance(library_settings, dict):
                library_settings = {
                    k: v for k, v in library_settings.items() if k not in SECRET_FIELDS
                }
            zf.writestr("settings.json", json.dumps(library_settings, indent=2, ensure_ascii=False))
            zf.writestr(
                "README.txt",
                f"Exported from {APP_NAME} {__version__} on {_when(time.time())}.\n\n"
                f"documents/  your original files ({documents})\n"
                f"chats/      every chat as Markdown and JSON ({chats})\n"
                "preferences.json, settings.json  your saved preferences and settings "
                "(no API keys)\n",
            )
        partial.replace(target)
        return {"documents": documents, "chats": chats}

    def expire(self, days: int) -> int:
        """Delete exports older than ``days``. Returns how many were removed."""
        cutoff = time.time() - days * 86_400
        removed = 0
        if not self.folder.exists():
            return 0
        for meta_path in self.folder.glob("*.json"):
            meta = read_json(meta_path, {})
            created = meta.get("created") if isinstance(meta, dict) else None
            if (
                isinstance(created, int | float)
                and created < cutoff
                and meta.get("status") != "building"
            ):
                (self.folder / f"outlawz-export-{meta_path.stem}.zip").unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                removed += 1
        for leftover in self.folder.glob("*.partial"):
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink(missing_ok=True)
        return removed
