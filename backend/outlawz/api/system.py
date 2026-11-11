"""Health and public configuration. Both stay open (no token needed), so the UI can always tell
whether the app is running and whether it needs a token or first-run setup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from outlawz import APP_NAME, __version__
from outlawz.api.deps import State, reopen
from outlawz.config import save_library_choice, switch_library
from outlawz.errors import AppError
from outlawz.llm.base import shown_url
from outlawz.paths import default_library

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(state: State) -> dict[str, Any]:
    return {"status": "ok" if state.opened else "starting", "version": __version__}


@router.get("/config")
def public_config(state: State) -> dict[str, Any]:
    s = state.settings
    warnings: list[dict[str, str]] = []
    if s.is_cloud_llm:
        warnings.append(
            {
                "code": "cloud_model",
                "message": "A cloud model is selected: your questions and the passages used "
                "to answer them are sent to it.",
            }
        )
    if state.migration is not None and state.migration.migrated:
        warnings.append(
            {
                "code": "upgraded",
                "message": f"The library was upgraded to format {state.migration.to_version}. "
                f"A backup was saved first.",
            }
        )
    return {
        "app": APP_NAME,
        "version": __version__,
        "library": str(s.library),
        "model": {
            "llm": s.llm,
            "llm_model": s.llm_model,
            "embedder": s.embedder,
            "embed_model": s.embed_model,
            "reranker": s.reranker,
        },
        "cloud": s.is_cloud_llm,
        "token_required": bool(s.access_token),
        "needs_setup": not state.data.config_file.exists() and "library" not in s.locked,
        "warnings": warnings,
    }


RECOMMENDED_MODEL = "llama3.1:8b"


class Setup(BaseModel):
    library: str = Field(min_length=1, max_length=1000)


@router.get("/setup/detect")
async def detect(state: State) -> dict[str, Any]:
    """First run: what's already here. Looks for Ollama on this computer only."""
    s = state.settings
    ollama: dict[str, Any] = {"url": shown_url(s.ollama_url), "reachable": False, "models": []}
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.get(f"{s.ollama_url}/api/tags")
        if response.status_code == 200:
            ollama["reachable"] = True
            ollama["models"] = [m.get("name", "") for m in response.json().get("models", [])][:50]
    except (httpx.HTTPError, ValueError):
        pass
    return {
        "library": str(s.library),
        "default_library": str(default_library()),
        "library_locked": "library" in s.locked,
        "ollama": ollama,
        "recommended_model": RECOMMENDED_MODEL,
        "pull_command": f"ollama pull {RECOMMENDED_MODEL}",
        "llm": s.llm,
        "llm_model": s.llm_model,
    }


@router.post("/setup")
async def setup(body: Setup, request: Request, state: State) -> dict[str, Any]:
    """Choose (or confirm) the library folder. Switching opens the new library right away."""
    library = Path(body.library.strip()).expanduser()
    if not library.is_absolute():
        raise AppError(
            "invalid_request", "Give the full path to a folder, like ~/Documents/Notes.", 422
        )
    current = state.settings.library.resolve()
    if "library" in state.settings.locked and library.resolve() != current:
        raise AppError("setting_locked", "The library folder is fixed by OUTLAWZ_LIBRARY.", 409)
    if library.exists() and not library.is_dir():
        raise AppError("invalid_request", "That path is a file, not a folder.", 422)
    data_dir = state.settings.data_dir.resolve()
    if library.resolve() == data_dir or data_dir in library.resolve().parents:
        raise AppError("invalid_request", "Choose a folder outside Outlawz's own data folder.", 422)
    try:
        library.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise AppError("invalid_request", "That folder can't be created here.", 422) from None
    save_library_choice(state.settings.data_dir, library)
    if library.resolve() != current:
        state = await reopen(request, switch_library(state.settings, library))
    return public_config(state)
