"""The library watcher: drop in, rename, move, delete, temp files, collisions, restarts."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from outlawz.library.documents import DocumentIndex, Status
from outlawz.library.watcher import LibraryWatcher, ScanReport
from tests.builders import simple_pdf
from tests.conftest import csv_bytes, epub_bytes

LATER = 10_000.0  # seconds; lets scans treat files as settled


class Harness:
    def __init__(self, root: Path, tmp: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.docs = DocumentIndex(tmp / "documents.json")
        self.forgotten: list[str] = []
        self.watcher = LibraryWatcher(
            root, self.docs, depth=lambda: 8, interval=lambda: 1, forget=self._forget
        )

    def _forget(self, doc_id: str) -> None:
        self.forgotten.append(doc_id)
        self.docs.remove(doc_id)

    def scan(self) -> ScanReport:
        return self.watcher.scan_once(now=time.time() + LATER)

    def paths(self) -> dict[str, str]:
        return {d.path: d.id for d in self.docs.all()}


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path / "library", tmp_path)


def test_existing_files_are_found_on_first_scan(h: Harness) -> None:
    simple_pdf(h.root / "a.pdf", ["Alpha"])
    (h.root / "b.csv").write_bytes(csv_bytes())
    (h.root / "c.epub").write_bytes(epub_bytes())
    report = h.scan()
    assert len(report.added) == 3
    assert {d.type for d in h.docs.all()} == {"pdf", "csv", "epub"}
    assert all(d.status == Status.queued for d in h.docs.all())


def test_dropped_in_file_waits_until_it_stops_changing(h: Harness) -> None:
    h.scan()  # first scan of an empty library
    simple_pdf(h.root / "new.pdf", ["New"])
    assert h.scan().waiting == 1  # seen once: might still be copying
    assert h.paths() == {}
    report = h.scan()
    assert len(report.added) == 1
    assert "new.pdf" in h.paths()


def test_file_being_written_is_not_read(tmp_path: Path, h: Harness) -> None:
    h.scan()
    path = h.root / "growing.csv"
    path.write_text("a,b\n1,2\n")
    report = h.watcher.scan_once(now=time.time())  # mtime is "just now"
    assert report.waiting == 1 and not report.added


def test_rename_keeps_the_id(h: Harness) -> None:
    simple_pdf(h.root / "old name.pdf", ["Same content"])
    h.scan()
    doc_id = h.paths()["old name.pdf"]
    os.rename(h.root / "old name.pdf", h.root / "new name.pdf")
    report = h.scan()
    assert report.moved == [doc_id]
    assert h.paths() == {"new name.pdf": doc_id}
    assert not h.forgotten


def test_move_into_subfolder_keeps_the_id(h: Harness) -> None:
    simple_pdf(h.root / "report.pdf", ["Report"])
    h.scan()
    doc_id = h.paths()["report.pdf"]
    (h.root / "archive" / "2025").mkdir(parents=True)
    shutil.move(h.root / "report.pdf", h.root / "archive" / "2025" / "report.pdf")
    h.scan()
    assert h.paths() == {"archive/2025/report.pdf": doc_id}


def test_delete_leaves_search_after_two_scans(h: Harness) -> None:
    simple_pdf(h.root / "gone.pdf", ["Gone"])
    h.scan()
    doc_id = h.paths()["gone.pdf"]
    (h.root / "gone.pdf").unlink()
    assert h.scan().removed == []  # missing once: could be mid-move
    assert h.scan().removed == [doc_id]
    assert h.forgotten == [doc_id]
    assert h.paths() == {}


def test_changed_content_is_read_again(h: Harness) -> None:
    (h.root / "data.csv").write_text("a,b\n1,2\n")
    h.scan()
    doc_id = h.paths()["data.csv"]
    h.docs.update(doc_id, status=Status.ready)
    (h.root / "data.csv").write_text("a,b\n1,2\n3,4\n")
    h.scan()
    report = h.scan()
    assert report.changed == [doc_id]
    assert h.docs.get(doc_id).status == Status.queued  # type: ignore[union-attr]


def test_touch_without_content_change_is_ignored(h: Harness) -> None:
    (h.root / "data.csv").write_text("a,b\n1,2\n")
    h.scan()
    doc_id = h.paths()["data.csv"]
    h.docs.update(doc_id, status=Status.ready)
    later = time.time() - 100
    os.utime(h.root / "data.csv", (later, later))
    h.scan()
    h.scan()
    assert h.docs.get(doc_id).status == Status.ready  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "name",
    [
        "report.pdf.part",
        "report.pdf.crdownload",
        "~$budget.csv",
        ".hidden.pdf",
        "notes.txt",
        "image.png",
        "Thumbs.db",
        ".report.pdf.icloud",
        "data.csv.tmp",
    ],
)
def test_temp_hidden_and_unsupported_files_are_ignored(h: Harness, name: str) -> None:
    (h.root / name).write_bytes(b"%PDF-1.4 stuff" if "pdf" in name else b"a,b\n1,2\n")
    h.scan()
    h.scan()
    assert h.paths() == {}


def test_app_folder_hidden_and_system_folders_are_skipped(h: Harness) -> None:
    for folder in (".outlawz/docs", ".git", "__MACOSX", "node_modules", "$RECYCLE.BIN"):
        (h.root / folder).mkdir(parents=True)
        (h.root / folder / "x.csv").write_text("a,b\n1,2\n")
    h.scan()
    assert h.paths() == {}


def test_depth_limit(h: Harness) -> None:
    deep = h.root.joinpath(*[f"d{i}" for i in range(10)])
    deep.mkdir(parents=True)
    (deep / "deep.csv").write_text("a\n1\n")
    shallow = h.root / "d0" / "d1"
    (shallow / "ok.csv").write_text("a\n1\n")
    h.scan()
    assert list(h.paths()) == ["d0/d1/ok.csv"]


def test_same_name_in_two_folders_are_two_documents(h: Harness) -> None:
    for folder in ("2024", "2025"):
        (h.root / folder).mkdir()
        (h.root / folder / "report.csv").write_text(f"year\n{folder}\n")
    h.scan()
    assert sorted(h.paths()) == ["2024/report.csv", "2025/report.csv"]


def test_copy_of_existing_file_is_a_new_document(h: Harness) -> None:
    (h.root / "a.csv").write_text("x\n1\n")
    h.scan()
    shutil.copy2(h.root / "a.csv", h.root / "a copy.csv")
    h.scan()
    h.scan()
    paths = h.paths()
    assert set(paths) == {"a.csv", "a copy.csv"}
    assert paths["a.csv"] != paths["a copy.csv"]


def test_missing_library_folder_pauses_instead_of_deleting(h: Harness) -> None:
    (h.root / "a.csv").write_text("x\n1\n")
    h.scan()
    hidden = h.root.with_name("unplugged")
    h.root.rename(hidden)
    assert h.scan().paused
    assert h.scan().paused
    assert list(h.paths()) == ["a.csv"]
    hidden.rename(h.root)
    assert not h.scan().removed


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin rights on Windows")
def test_symlink_leaving_the_library_is_ignored(tmp_path: Path, h: Harness) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.csv").write_text("x\n1\n")
    (h.root / "link.csv").symlink_to(outside / "secret.csv")
    (h.root / "linkdir").symlink_to(outside, target_is_directory=True)
    h.scan()
    assert h.paths() == {}


def test_restart_mid_ingestion_requeues(tmp_path: Path) -> None:
    docs = DocumentIndex(tmp_path / "documents.json")
    from outlawz.library.documents import Document

    docs.add(
        Document(
            id="d1", path="a.pdf", sha256="x", size=1, mtime=1, type="pdf", status=Status.embedding
        )
    )
    docs.add(
        Document(
            id="d2", path="b.pdf", sha256="y", size=1, mtime=1, type="pdf", status=Status.ready
        )
    )
    reopened = DocumentIndex(tmp_path / "documents.json")
    assert reopened.requeue_unsettled() == 1
    assert reopened.get("d1").status == Status.queued  # type: ignore[union-attr]
    assert reopened.get("d2").status == Status.ready  # type: ignore[union-attr]
