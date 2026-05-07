"""ASGI-level tests for ``PrefectMountMiddleware``.

These exercise the dispatch logic without standing up a real Prefect
server. Two recording ``_RecordingApp`` instances stand in for the outer
FastAPI app and the embedded Prefect app; each test calls the middleware
as a plain ASGI callable and asserts which inner app received the scope
and what its ``path`` / ``raw_path`` looked like after dispatch.

This middleware is the most error-prone piece in the project (custom
prefix-strip + ``raw_path`` rewrite for the API sub-app, prefix
preservation for the UI). Without these tests a refactor that swaps the
two arms would silently break the embedded UI / API contract.
"""

from starlette.types import Message, Receive, Scope, Send

from chap_scheduler.api.prefect_embed import PrefectMountMiddleware


class _RecordingApp:
    """Minimal ASGI app that records the scope it was called with."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.received_scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Copy because the middleware may pass a mutated mapping; we want a
        # snapshot of what THIS app saw, not whatever shared dict ends up
        # being modified later.
        self.received_scopes.append(dict(scope))


async def _noop_receive() -> Message:
    return {"type": "http.disconnect"}


async def _noop_send(message: Message) -> None:
    return None


def _http_scope(path: str, raw_path: bytes | None = None) -> Scope:
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode(),
        "headers": [],
        "query_string": b"",
    }
    return scope


def _build() -> tuple[PrefectMountMiddleware, _RecordingApp, _RecordingApp]:
    outer = _RecordingApp("outer")
    prefect = _RecordingApp("prefect")
    middleware = PrefectMountMiddleware(outer, mount="/prefect", prefect_app=prefect)
    return middleware, outer, prefect


# --- non-http scopes pass through to outer app ----------------------------


async def test_lifespan_scope_passes_through_to_outer_app() -> None:
    """The lifespan event drives the outer FastAPI app, not Prefect."""
    middleware, outer, prefect = _build()
    await middleware({"type": "lifespan"}, _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 1
    assert len(prefect.received_scopes) == 0


# --- outside-mount paths route to outer app -------------------------------


async def test_path_outside_mount_routes_to_outer_app() -> None:
    """A request to /health (or anything not under /prefect) goes to FastAPI."""
    middleware, outer, prefect = _build()
    await middleware(_http_scope("/health"), _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 1
    assert len(prefect.received_scopes) == 0
    assert outer.received_scopes[0]["path"] == "/health"


async def test_path_starting_with_mount_but_not_mount_routes_to_outer() -> None:
    """`/prefectish` must not match the `/prefect` mount.

    Defensive: the dispatch test is `path == mount or path.startswith(mount + "/")`,
    not a naive `startswith(mount)`. Regression here would silently send
    unrelated /prefectish-ish traffic into the embedded server.
    """
    middleware, outer, prefect = _build()
    await middleware(_http_scope("/prefectish"), _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 1
    assert len(prefect.received_scopes) == 0


# --- mount UI paths: prefix preserved -------------------------------------


async def test_mount_root_routes_to_prefect_with_prefix_preserved() -> None:
    """The bare /prefect path is the UI's index; prefix stays intact."""
    middleware, outer, prefect = _build()
    await middleware(_http_scope("/prefect"), _noop_receive, _noop_send)
    assert len(prefect.received_scopes) == 1
    assert len(outer.received_scopes) == 0
    assert prefect.received_scopes[0]["path"] == "/prefect"


async def test_mount_ui_asset_keeps_prefix_in_path_and_raw_path() -> None:
    """UI asset URLs (e.g. /prefect/main.js) need the prefix preserved.

    The Prefect UI sub-app is mounted internally at the prefix when
    PREFECT_SERVER_UI_SERVE_BASE is set, so the SPA's asset URLs include
    the prefix and routing must keep it.
    """
    middleware, outer, prefect = _build()
    await middleware(_http_scope("/prefect/main.js"), _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 0
    assert len(prefect.received_scopes) == 1
    scope = prefect.received_scopes[0]
    assert scope["path"] == "/prefect/main.js"
    assert scope["raw_path"] == b"/prefect/main.js"


# --- mount API paths: prefix stripped -------------------------------------


async def test_mount_api_path_strips_prefix_in_path_and_raw_path() -> None:
    """`/prefect/api/health` -> Prefect API sub-app sees `/api/health`."""
    middleware, outer, prefect = _build()
    await middleware(_http_scope("/prefect/api/health"), _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 0
    assert len(prefect.received_scopes) == 1
    scope = prefect.received_scopes[0]
    assert scope["path"] == "/api/health"
    assert scope["raw_path"] == b"/api/health"


async def test_mount_api_root_strips_prefix() -> None:
    """`/prefect/api` (no trailing slash) -> `/api`."""
    middleware, outer, prefect = _build()
    await middleware(_http_scope("/prefect/api"), _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 0
    assert len(prefect.received_scopes) == 1
    assert prefect.received_scopes[0]["path"] == "/api"


# --- websocket scopes dispatch the same way as http -----------------------


async def test_websocket_under_mount_api_strips_prefix() -> None:
    """websocket scopes get the same dispatch logic as http."""
    middleware, outer, prefect = _build()
    ws_scope: Scope = {
        "type": "websocket",
        "path": "/prefect/api/socket",
        "raw_path": b"/prefect/api/socket",
        "headers": [],
        "query_string": b"",
    }
    await middleware(ws_scope, _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 0
    assert len(prefect.received_scopes) == 1
    assert prefect.received_scopes[0]["path"] == "/api/socket"


# --- raw_path edge cases --------------------------------------------------


async def test_raw_path_left_untouched_for_ui_paths() -> None:
    """UI dispatch must not mutate raw_path even if it's set."""
    middleware, outer, prefect = _build()
    await middleware(
        _http_scope("/prefect/main.js", raw_path=b"/prefect/main.js?v=1"),
        _noop_receive,
        _noop_send,
    )
    assert len(outer.received_scopes) == 0
    assert prefect.received_scopes[0]["raw_path"] == b"/prefect/main.js?v=1"


async def test_missing_raw_path_does_not_crash_on_api_path() -> None:
    """Some scopes don't include raw_path; the API rewrite must skip it cleanly."""
    middleware, outer, prefect = _build()
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/prefect/api/health",
        "headers": [],
        "query_string": b"",
        # Note: no raw_path key.
    }
    await middleware(scope, _noop_receive, _noop_send)
    assert len(outer.received_scopes) == 0
    assert len(prefect.received_scopes) == 1
    assert prefect.received_scopes[0]["path"] == "/api/health"
