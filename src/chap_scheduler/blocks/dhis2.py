"""DHIS2 credentials block.

Stores the connection details for a DHIS2 instance and produces an
authenticated ``DHIS2Client`` (from the upstream ``dhis2-client`` library at
https://github.com/dhis2/dhis2-python-client).

The block *type* is auto-registered with the embedded Prefect server on
startup. *Instances* are created by the user — one per DHIS2 server they
want to talk to — via the Prefect UI at ``/prefect/blocks/catalog`` or the
SDK:

    Dhis2Credentials(
        base_url="https://...",
        username="...",
        password=SecretStr("..."),
    ).save("my-dhis2-instance")
"""

from dhis2_client import DHIS2Client
from prefect.blocks.core import Block
from pydantic import Field, SecretStr


class Dhis2Credentials(Block):
    """Credentials for a DHIS2 instance."""

    _block_type_name = "DHIS2 Credentials (chap-scheduler)"
    _block_type_slug = "chap-dhis2-credentials"
    _description = "Credentials for connecting to a DHIS2 instance to fetch analytics."

    base_url: str = Field(description="DHIS2 instance base URL, e.g. https://dhis.example.org")
    username: str = Field(description="DHIS2 username.")
    password: SecretStr = Field(description="DHIS2 password.")

    def get_client(self) -> DHIS2Client:
        """Return an authenticated ``DHIS2Client``."""
        return DHIS2Client(
            self.base_url,
            username=self.username,
            password=self.password.get_secret_value(),
        )
