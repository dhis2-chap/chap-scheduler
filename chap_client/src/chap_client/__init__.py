"""chap-client: HTTP client for the chap REST API.

Stub package today; code is being migrated here from chap-scheduler one
stable layer at a time.
"""

from importlib.metadata import PackageNotFoundError, version

from chap_client.errors import ChapHttpError

try:
    __version__ = version("chap-client")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["ChapHttpError", "__version__"]
