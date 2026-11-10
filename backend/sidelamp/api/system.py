"""Health and public configuration. Both stay open (no token needed), so the UI can always tell
whether the app is running and whether it needs a token or first-run setup."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from sidelamp import APP_NAME, __version__
from sidelamp.api.deps import State

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
