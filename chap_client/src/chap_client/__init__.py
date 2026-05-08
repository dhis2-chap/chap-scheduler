"""chap-client: HTTP client and pydantic models for the chap REST API.

Code is being migrated here from chap-scheduler one stable layer at a
time.
"""

from importlib.metadata import PackageNotFoundError, version

from chap_client.client import ChapAuth, ChapClient
from chap_client.errors import ChapHttpError
from chap_client.models import (
    ChapConfiguredModel,
    ChapConfiguredModelWithDataSource,
    ChapDataSource,
    ChapFetchRequest,
    ChapJobDescription,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapMissingValuesDetail,
    ChapModelTemplate,
    ChapObservation,
    ChapPredictionEntry,
    ChapRejection,
    ChapSystemInfo,
)

try:
    __version__ = version("chap-client")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "ChapAuth",
    "ChapClient",
    "ChapConfiguredModel",
    "ChapConfiguredModelWithDataSource",
    "ChapDataSource",
    "ChapFetchRequest",
    "ChapHttpError",
    "ChapJobDescription",
    "ChapJobResponse",
    "ChapMakePredictionRequest",
    "ChapMissingValuesDetail",
    "ChapModelTemplate",
    "ChapObservation",
    "ChapPredictionEntry",
    "ChapRejection",
    "ChapSystemInfo",
    "__version__",
]
