"""DHIS2 credentials block.

Stores the connection details for a DHIS2 instance and exposes
factories for both DHIS2's native client (``Dhis2Client`` from the
``dhis2w-client`` library) and the chap REST client
(``chap_client.ChapClient``) routed via this DHIS2 instance's chap
proxy.

The block *type* is auto-registered with the embedded Prefect server
on startup. *Instances* are created by the user — one per DHIS2 server
they want to talk to — via the Prefect UI at
``/prefect/blocks/catalog`` or the SDK:

    Dhis2Credentials(
        base_url="https://...",
        username="...",
        password=SecretStr("..."),
    ).save("my-dhis2-instance")
"""

from typing import Any

from dhis2w_client import Dhis2Client
from dhis2w_client.auth.basic import BasicAuth
from prefect.blocks.core import Block
from pydantic import Field, SecretStr

from chap_client import ChapClient

# Path prefix where DHIS2 proxies the chap REST API. chap_scheduler
# always reaches chap *through* DHIS2 (single auth surface), so every
# ChapClient produced from a Dhis2Credentials carries this prefix.
DHIS2_CHAP_ROUTE_PREFIX = "/api/routes/chap/run"


class Dhis2Credentials(Block):
    """Credentials for a DHIS2 instance."""

    _block_type_name = "DHIS2 Credentials (chap-scheduler)"
    _block_type_slug = "chap-dhis2-credentials"
    _description = "Credentials for connecting to a DHIS2 instance to fetch analytics."

    base_url: str = Field(description="DHIS2 instance base URL, e.g. https://dhis.example.org")
    username: str = Field(description="DHIS2 username.")
    password: SecretStr = Field(description="DHIS2 password.")

    def get_client(self) -> Dhis2Client:
        """Return an unconnected ``dhis2w_client.Dhis2Client`` for this instance.

        The client is async; callers must use it via ``async with``
        (or call ``await client.connect()`` explicitly). Auth is
        ``BasicAuth(username, password)`` -- DHIS2 PAT / OAuth2
        deployment shapes will be wired up via separate auth-typed
        blocks when needed.
        """
        return Dhis2Client(
            base_url=self.base_url,
            auth=BasicAuth(username=self.username, password=self.password.get_secret_value()),
        )

    def chap_client(self, **kwargs: Any) -> ChapClient:
        """Return a `chap_client.ChapClient` routed via this DHIS2 instance.

        Forwards keyword arguments (e.g. ``timeout``, ``transport``,
        ``max_attempts``) to the underlying constructor.
        """
        return ChapClient(
            base_url=self.base_url,
            auth=(self.username, self.password.get_secret_value()),
            route_prefix=DHIS2_CHAP_ROUTE_PREFIX,
            **kwargs,
        )
