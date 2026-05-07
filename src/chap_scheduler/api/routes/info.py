"""Service info endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chap_scheduler import __version__

router = APIRouter(tags=["info"])


class InfoResponse(BaseModel):
    """Service metadata response."""

    name: str
    version: str
    prefect_embedded: bool
    prefect_ui_path: str | None
    prefect_api_path: str | None


@router.get("/info", response_model=InfoResponse)
async def info(request: Request) -> InfoResponse:
    """Return service metadata, including the embedded Prefect mount paths."""
    settings = request.app.state.settings
    mount = settings.prefect_mount_path.rstrip("/")
    return InfoResponse(
        name="chap-scheduler",
        version=__version__,
        prefect_embedded=settings.embed_prefect,
        prefect_ui_path=f"{mount}/" if settings.embed_prefect else None,
        prefect_api_path=f"{mount}/api" if settings.embed_prefect else None,
    )
