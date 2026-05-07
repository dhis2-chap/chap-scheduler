"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from chap_scheduler import __version__
from chap_scheduler.api.prefect_embed import (
    PrefectMountMiddleware,
    build_prefect_app,
    configure_prefect_env,
    prefect_lifespan,
)
from chap_scheduler.api.routes import health, info
from chap_scheduler.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    When ``settings.embed_prefect`` is true (default), the Prefect server
    (API + UI + background services) is mounted at ``settings.prefect_mount_path``.

    Args:
        settings: Optional pre-built settings. When omitted, settings are
            loaded from env / .env at import time.

    Returns:
        A configured FastAPI instance.
    """
    settings = settings or get_settings()

    prefect_app: FastAPI | None = None
    if settings.embed_prefect:
        configure_prefect_env(settings.prefect_mount_path, log_level=settings.log_level)
        prefect_app = build_prefect_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.settings = settings
        if prefect_app is not None:
            async with prefect_lifespan(prefect_app):
                yield
        else:
            yield

    app = FastAPI(
        title="chap-scheduler",
        description="FastAPI service embedding Prefect for CHAP workflow orchestration.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.include_router(health.router)
    app.include_router(info.router)

    root_target = f"{settings.prefect_mount_path.rstrip('/')}/" if settings.embed_prefect else "/docs"

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url=root_target)

    if prefect_app is not None:
        app.add_middleware(
            PrefectMountMiddleware,
            mount=settings.prefect_mount_path,
            prefect_app=prefect_app,
        )

    return app


app = create_app()
