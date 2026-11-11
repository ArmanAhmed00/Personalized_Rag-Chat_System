"""``outlawz doctor``: checks the setup and says how to fix each problem.

Checks Python, the packages, the search models, whether Ollama is reachable, folder permissions,
disk space, stale locks, the built web app and optional OCR. Nothing is changed.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from outlawz.config import Settings
from outlawz.llm.base import shown_url

OK, WARN, FAIL = "ok", "warn", "fail"
PACKAGES = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("httpx", "httpx"),
    ("pymupdf", "pymupdf"),
    ("ebooklib", "ebooklib"),
    ("bs4", "beautifulsoup4"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("fastembed", "fastembed"),
)
MIN_FREE_BYTES = 1024**3


@dataclass
class Check:
    name: str
    status: str
    message: str
    fix: str = ""


def check_python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 11):
        return Check(
            "Python",
            FAIL,
            f"Python {text} is too old.",
            "Install Python 3.11 or newer, then reinstall Outlawz.",
        )
    return Check("Python", OK, f"Python {text}.")


def check_packages() -> Check:
    missing = []
    for module, dist in PACKAGES:
        try:
            importlib.import_module(module)
        except Exception:
            missing.append(dist)
    if missing:
        return Check(
            "Packages",
            FAIL,
            f"Missing or broken: {', '.join(missing)}.",
            "Reinstall: pipx reinstall outlawz (or pip install --force-reinstall outlawz).",
        )
    return Check("Packages", OK, f"All {len(PACKAGES)} required packages load.")


def check_models(settings: Settings) -> Check:
    if settings.embedder != "fastembed":
        return Check("Search models", OK, f"Using {settings.embedder} embeddings.")
    folder = settings.data_paths.models_dir
    name = settings.embed_model.split("/")[-1].lower()
    found = folder.exists() and any(name in p.name.lower() for p in folder.rglob("*") if p.is_dir())
    if found:
        return Check("Search models", OK, f"“{settings.embed_model}” is downloaded.")
    return Check(
        "Search models",
        WARN,
        f"“{settings.embed_model}” isn't downloaded yet. It downloads once (about 70 MB) "
        "the first time a document is read.",
        "Connect to the internet for the first run, or copy the model cache into " + str(folder),
    )


def check_llm(settings: Settings, get: Callable[..., object] | None = None) -> Check:
    import httpx

    getter = get or httpx.get
    if settings.llm == "ollama":
        url = f"{shown_url(settings.ollama_url)}/api/tags"
        try:
            response = getter(url, timeout=3.0)
        except httpx.HTTPError:
            return Check(
                "Model",
                WARN,
                f"Ollama isn't answering at {shown_url(settings.ollama_url)}.",
                "Install Ollama from ollama.com and start it, or choose another model server "
                "in Settings.",
            )
        try:
            names = [m.get("name", "") for m in response.json().get("models", [])]  # type: ignore[attr-defined]
        except Exception:
            names = []
        if not any(n == settings.llm_model or n.split(":")[0] == settings.llm_model for n in names):
            return Check(
                "Model",
                WARN,
                f"Ollama is running, but “{settings.llm_model}” isn't installed.",
                f"Run: ollama pull {settings.llm_model}",
            )
        return Check("Model", OK, f"Ollama is running with “{settings.llm_model}”.")
    if settings.llm == "openai":
        try:
            getter(f"{shown_url(settings.openai_url)}/models", timeout=3.0)
        except httpx.HTTPError:
            return Check(
                "Model",
                WARN,
                f"Nothing is answering at {shown_url(settings.openai_url)}.",
                "Start your OpenAI-compatible server (LM Studio, llama.cpp, vLLM) or fix the "
                "address in Settings.",
            )
        return Check("Model", OK, f"A server is answering at {shown_url(settings.openai_url)}.")
    if settings.llm == "anthropic":
        if not settings.anthropic_key:
            return Check(
                "Model",
                FAIL,
                "Anthropic is selected but no API key is set.",
                "Add the key in Settings, or choose a local model.",
            )
        return Check(
            "Model",
            OK,
            "Anthropic is selected and a key is set (questions and passages are sent to it).",
        )
    return Check("Model", OK, f"Using the {settings.llm} model.")


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".outlawz-write-test-{uuid.uuid4().hex[:6]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check_folders(settings: Settings) -> Check:
    bad = [str(f) for f in (settings.library, settings.data_dir) if not _writable(f)]
    if bad:
        return Check(
            "Folders",
            FAIL,
            f"Can't write to: {', '.join(bad)}.",
            "Check the folder permissions, or choose another library folder (OUTLAWZ_LIBRARY).",
        )
    return Check("Folders", OK, f"Library: {settings.library}. Data: {settings.data_dir}.")


def check_disk(settings: Settings) -> Check:
    try:
        free = shutil.disk_usage(
            settings.library if settings.library.exists() else settings.library.parent
        ).free
    except OSError:
        return Check("Disk space", WARN, "Couldn't check the free space.")
    gb = free / 1024**3
    if free < MIN_FREE_BYTES:
        return Check(
            "Disk space",
            WARN,
            f"Only {gb:.1f} GB free.",
            "Free up some space: models, search data and backups need room.",
        )
    return Check("Disk space", OK, f"{gb:.0f} GB free.")


def check_lock(settings: Settings) -> Check:
    from outlawz.store.lock import InstanceLock

    lock = InstanceLock(settings.library_paths.lock_file)
    if not lock.path.exists():
        return Check("Library lock", OK, "No other copy has this library open.")
    info = lock._read()
    if lock._is_stale(info):
        return Check(
            "Library lock",
            OK,
            "A lock was left by a copy that stopped; it's cleared on the next start.",
        )
    return Check(
        "Library lock",
        WARN,
        lock._refusal(info),
        "Use the copy that's already running, or close it first.",
    )


def check_web() -> Check:
    index = Path(__file__).resolve().parent / "web" / "index.html"
    if index.exists():
        return Check("Web app", OK, "The web app is built in.")
    return Check(
        "Web app",
        WARN,
        "The web app isn't built into this install, so only the API is served.",
        "Install a release (pipx install outlawz), or build it: python scripts/build_web.py",
    )


def check_ocr() -> Check:
    from outlawz.ingest.extractors.pdf import ocr_available

    if ocr_available():
        return Check("OCR (optional)", OK, "Tesseract is installed: scanned PDF pages can be read.")
    return Check(
        "OCR (optional)",
        WARN,
        "Tesseract isn't installed, so scanned PDF pages are skipped.",
        "Optional: install Tesseract (brew install tesseract, apt install tesseract-ocr, "
        "or the Windows installer).",
    )


def run_checks(settings: Settings) -> list[Check]:
    return [
        check_python(),
        check_packages(),
        check_folders(settings),
        check_disk(settings),
        check_lock(settings),
        check_web(),
        check_models(settings),
        check_llm(settings),
        check_ocr(),
    ]


def print_report(checks: list[Check]) -> int:
    marks = {OK: "✓", WARN: "!", FAIL: "✗"}
    for check in checks:
        print(f"{marks[check.status]} {check.name}: {check.message}")
        if check.fix and check.status != OK:
            print(f"    → {check.fix}")
    failed = [c for c in checks if c.status == FAIL]
    warned = [c for c in checks if c.status == WARN]
    print()
    if failed:
        print(f"{len(failed)} problem(s) need fixing before Outlawz can run well.")
        return 1
    print("Everything needed is in place." + (f" {len(warned)} note(s) above." if warned else ""))
    return 0
