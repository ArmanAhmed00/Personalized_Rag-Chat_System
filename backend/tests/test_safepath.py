from __future__ import annotations

import os
from pathlib import Path

import pytest

from outlawz.store.safepath import UnsafePath, check_relative, resolve_inside, safe_name


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        "../x.pdf",
        "a/../../x",
        "/etc/passwd",
        "C:\\Windows\\x",
        "C:x",
        "\\\\server\\share\\x",
        "//server/share",
        "a\x00b",
        "..\\..\\x",
        "a/b/../../..",
    ],
)
def test_rejects_escaping_paths(bad: str) -> None:
    with pytest.raises(UnsafePath):
        check_relative(bad)


def test_accepts_normal_paths(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    assert resolve_inside(tmp_path, "sub/report.pdf") == (tmp_path / "sub" / "report.pdf").resolve()
    assert resolve_inside(tmp_path, "sub\\report.pdf").name == "report.pdf"
    assert resolve_inside(tmp_path, "./sub/./a.pdf").name == "a.pdf"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin rights on Windows")
def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "library"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePath):
        resolve_inside(root, "link/secret.txt")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../evil.pdf", "evil.pdf"),
        ("C:\\Users\\me\\notes.csv", "notes.csv"),
        (".hidden.pdf", "hidden.pdf"),
        ("CON.pdf", "_CON.pdf"),
        ('we<ird>:"na|me?.pdf', "we_ird___na_me_.pdf"),
        ("   ", "document"),
        ("bad\x00name.epub", "bad_name.epub"),
    ],
)
def test_safe_name(given: str, expected: str) -> None:
    assert safe_name(given) == expected


def test_safe_name_truncates_but_keeps_extension() -> None:
    name = safe_name("x" * 400 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name) <= 180
