from dhis2_client import DHIS2Client
from pydantic import SecretStr

from chap_scheduler.blocks.dhis2 import Dhis2Credentials


def test_get_client_returns_authenticated_client() -> None:
    creds = Dhis2Credentials(
        base_url="http://example.invalid",
        username="alice",
        password=SecretStr("hunter2"),
    )
    client = creds.get_client()
    assert isinstance(client, DHIS2Client)
