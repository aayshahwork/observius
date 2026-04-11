"""
api/routes/local_files.py — Serve locally-stored replay/screenshot files (dev only).

This endpoint is only registered when ENVIRONMENT=development and R2 is not configured.
It serves files from the `replays/` directory with path-traversal protection.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

logger = structlog.get_logger("api.local_files")

router = APIRouter(prefix="/api/v1/local-files", tags=["Local Files (dev)"])

# Files are addressed as `replays/{task_id}/step_N.png`, so resolve from the
# project root (the parent of `replays/`). The path-traversal guard below
# restricts access to the `replays/` subtree.
_ROOT = Path(".").resolve()
_REPLAYS_ROOT = (_ROOT / "replays").resolve()

# Allowed extensions and their MIME types
_MIME_TYPES = {
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@router.get("/{file_path:path}")
async def serve_local_file(file_path: str) -> FileResponse:
    """Serve a file from the local replays directory.

    Only available in development mode. Protected against path traversal.
    """
    # Reject obviously malicious paths
    if ".." in file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path.",
        )

    # Resolve relative to project root and keep access inside `replays/`
    requested = (_ROOT / file_path).resolve()
    if not str(requested).startswith(str(_REPLAYS_ROOT) + "/") and str(requested) != str(_REPLAYS_ROOT):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    if not requested.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    suffix = requested.suffix.lower()
    media_type = _MIME_TYPES.get(suffix, "application/octet-stream")

    return FileResponse(path=requested, media_type=media_type)
