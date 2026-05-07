"""Helpers to embed the Prefect server (API + UI + services) inside our FastAPI app.

Mounting Prefect under a sub-path is awkward because:

- Prefect's UI sub-app uses ``PREFECT_SERVER_UI_SERVE_BASE`` *both* to register
  its internal ``SPAStaticFiles`` mounts AND as the SPA's HTML ``<base>``. So
  with ``serve_base=/prefect/``, the UI sub-app expects requests under the path
  ``/prefect/...`` and the SPA emits asset URLs starting with ``/prefect/``.
- Prefect's API sub-app, by contrast, is mounted at the *outer* path ``/api``,
  not ``/prefect/api``.
- A plain ``app.mount("/prefect", prefect_app)`` in Starlette strips
  ``/prefect`` before dispatch, which is wrong for UI requests (they need the
  prefix) and correct for API requests (they don't).

So we install a small ASGI dispatcher that:

- Routes anything under ``mount`` to Prefect.
- Strips ``mount`` from API requests (``/prefect/api/...`` → ``/api/...``).
- Leaves the prefix intact for everything else (the UI).

We also chain Prefect's lifespan into ours so its background services
(scheduler, triggers, task-run-recorder, ...) start with the FastAPI app.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send


def configure_prefect_env(mount_path: str, log_level: str = "INFO") -> None:
    """Set Prefect env vars so the embedded UI / API / logs are wired correctly.

    Must be called before any ``prefect`` module is imported: ``prefect/main.py``
    calls ``setup_logging()`` at import time, which reads these env vars (and
    the snapshotted Settings cache them).

    Notes:
        Prefect's ``logging.yml`` configures the ``uvicorn`` logger at
        ``${PREFECT_SERVER_LOGGING_LEVEL}`` (default ``WARNING``) with
        ``propagate: false`` — without raising it here, uvicorn's INFO startup
        banner and access logs would be silently swallowed.

    Args:
        mount_path: Path prefix where Prefect will be mounted, e.g. ``"/prefect"``.
        log_level: Level (``"DEBUG"``/``"INFO"``/``"WARNING"``/...) to set for
            Prefect's server-side ``uvicorn``/``fastapi`` loggers.
    """
    base = mount_path.rstrip("/") + "/"
    os.environ.setdefault("PREFECT_SERVER_UI_SERVE_BASE", base)
    os.environ.setdefault("PREFECT_UI_API_URL", f"{mount_path.rstrip('/')}/api")
    os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
    os.environ.setdefault("PREFECT_SERVER_UI_SHOW_PROMOTIONAL_CONTENT", "false")
    os.environ.setdefault("PREFECT_SERVER_LOGGING_LEVEL", log_level.upper())


def build_prefect_app() -> FastAPI:
    """Build the embedded Prefect FastAPI app with background services enabled."""
    # Imported lazily so configure_prefect_env() can run first.
    from prefect.server.api.server import create_app as create_prefect_app

    return create_prefect_app(ephemeral=False)


async def register_block_types() -> None:
    """Register chap-scheduler block *types* with the embedded Prefect server.

    Idempotent — safe to run on every startup. We only register the type so
    users can create instances via the UI / SDK; we do not seed any default
    instance, since real DHIS2 credentials are server-specific and per-user.
    """
    # Lazy import: keeps the prefect dependency edge inside the lifespan.
    from chap_scheduler.blocks.dhis2 import Dhis2Credentials

    await Dhis2Credentials.aregister_type_and_schema()


@asynccontextmanager
async def prefect_lifespan(prefect_app: FastAPI) -> AsyncGenerator[None, None]:
    """Run Prefect's lifespan (DB migrate, services, ...) for the duration of ours."""
    async with prefect_app.router.lifespan_context(prefect_app):
        await register_block_types()
        yield


class PrefectMountMiddleware:
    """ASGI middleware routing ``mount/*`` to the embedded Prefect app.

    For ``mount/api/*`` requests the prefix is stripped (Prefect's API sub-app
    is mounted at outer ``/api``). For all other ``mount/*`` paths the prefix
    is preserved, since Prefect's UI sub-app is mounted internally at ``mount``
    when ``PREFECT_SERVER_UI_SERVE_BASE`` is set.
    """

    def __init__(self, app: ASGIApp, mount: str, prefect_app: ASGIApp) -> None:
        self.app = app
        self.mount = mount.rstrip("/")
        self.api_prefix = f"{self.mount}/api"
        self.prefect_app = prefect_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path != self.mount and not path.startswith(self.mount + "/"):
            await self.app(scope, receive, send)
            return

        new_scope = dict(scope)
        if path == self.api_prefix or path.startswith(self.api_prefix + "/"):
            new_scope["path"] = path[len(self.mount) :]
            raw_path = scope.get("raw_path")
            if isinstance(raw_path, bytes):
                new_scope["raw_path"] = raw_path[len(self.mount.encode()) :]

        await self.prefect_app(new_scope, receive, send)
