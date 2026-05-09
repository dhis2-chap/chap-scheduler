import httpx
from dhis2_client import DHIS2Client
from pydantic import SecretStr

from chap_client import ChapClient
from chap_scheduler.blocks.dhis2 import DHIS2_CHAP_ROUTE_PREFIX, Dhis2Credentials


def _credentials() -> Dhis2Credentials:
    return Dhis2Credentials(
        base_url="http://example.invalid",
        username="alice",
        password=SecretStr("hunter2"),
    )


def test_get_client_returns_authenticated_dhis2_client() -> None:
    assert isinstance(_credentials().get_client(), DHIS2Client)


def test_chap_client_returns_chap_client_with_dhis2_proxy_prefix() -> None:
    """``Dhis2Credentials.chap_client()`` is the canonical factory for
    a chap client routed via this DHIS2 instance's chap proxy."""
    seen: dict[str, str | None] = {"path": None, "auth": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(204)

    creds = _credentials()
    with creds.chap_client(transport=httpx.MockTransport(handler)) as client:
        assert isinstance(client, ChapClient)
        client.get("/system/info")

    # DHIS2 proxy prefix is wired in.
    assert seen["path"] == f"{DHIS2_CHAP_ROUTE_PREFIX}/system/info"
    # Block's username/password becomes basic auth on outgoing requests.
    assert seen["auth"] is not None and seen["auth"].startswith("Basic ")


def test_chap_client_forwards_kwargs_like_max_attempts() -> None:
    """Constructor knobs (timeout, transport, max_attempts) pass through."""
    creds = _credentials()
    client = creds.chap_client(max_attempts=1, retry_min_wait=0.0, retry_max_wait=0.0)
    assert client._max_attempts == 1
