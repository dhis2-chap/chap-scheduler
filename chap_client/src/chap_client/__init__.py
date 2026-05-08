"""chap-client: HTTP client and pydantic models for the chap REST API.

Code is being migrated here from chap-scheduler one stable layer at a
time.
"""

from importlib.metadata import PackageNotFoundError, version

from chap_client.client import ChapAuth, ChapClient
from chap_client.errors import ChapHttpError
from chap_client.schemas import (
    ChapConfiguredModel,
    ChapConfiguredModelCreate,
    ChapConfiguredModelDB,
    ChapConfiguredModelWithDataSource,
    ChapDataset,
    ChapDataSource,
    ChapEvaluationEntry,
    ChapEvaluationRead,
    ChapFeature,
    ChapFetchRequest,
    ChapJobDescription,
    ChapJobResponse,
    ChapMakeEvaluationRequest,
    ChapMakePredictionRequest,
    ChapMissingValuesDetail,
    ChapModelSpec,
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
    "ChapConfiguredModelCreate",
    "ChapConfiguredModelDB",
    "ChapConfiguredModelWithDataSource",
    "ChapDataSource",
    "ChapDataset",
    "ChapEvaluationEntry",
    "ChapEvaluationRead",
    "ChapFeature",
    "ChapFetchRequest",
    "ChapHttpError",
    "ChapJobDescription",
    "ChapJobResponse",
    "ChapMakeEvaluationRequest",
    "ChapMakePredictionRequest",
    "ChapMissingValuesDetail",
    "ChapModelSpec",
    "ChapModelTemplate",
    "ChapObservation",
    "ChapPredictionEntry",
    "ChapRejection",
    "ChapSystemInfo",
    "__version__",
]
