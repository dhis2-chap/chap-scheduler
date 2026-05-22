"""chap-client: HTTP client and pydantic schemas for the chap REST API.

Public surface:

- `ChapClient` -- the typed HTTP client (mixin-composed; one mixin
  per chap resource group).
- `ChapClientBase` -- HTTP plumbing only, for code that wants to
  subclass without inheriting all endpoints.
- The `Chap*` pydantic schemas covering chap's request and response
  shapes.
- `ChapHttpError` -- raised on every non-2xx chap response.

The CLI entry point (``chap-client ...``) lives in
`chap_client.cli` and mirrors `ChapClient` 1:1.
"""

from importlib.metadata import PackageNotFoundError, version

from chap_client.base import ChapAuth, ChapClientBase
from chap_client.client import ChapClient
from chap_client.errors import ChapHttpError
from chap_client.schemas import (
    ChapConfiguredModel,
    ChapConfiguredModelCreate,
    ChapConfiguredModelDB,
    ChapDataset,
    ChapDataSource,
    ChapEvaluationEntry,
    ChapEvaluationRead,
    ChapFeature,
    ChapFetchRequest,
    ChapJobDescription,
    ChapJobResponse,
    ChapMakeEvaluationRequest,
    ChapMissingValuesDetail,
    ChapModelSpec,
    ChapModelTemplate,
    ChapObservation,
    ChapPredictionEntry,
    ChapPredictionSetup,
    ChapQuantileTarget,
    ChapRejection,
    ChapRunPredictionSetupRequest,
    ChapSystemInfo,
)

try:
    __version__ = version("chap-client")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "ChapAuth",
    "ChapClient",
    "ChapClientBase",
    "ChapConfiguredModel",
    "ChapConfiguredModelCreate",
    "ChapConfiguredModelDB",
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
    "ChapMissingValuesDetail",
    "ChapModelSpec",
    "ChapModelTemplate",
    "ChapObservation",
    "ChapPredictionEntry",
    "ChapPredictionSetup",
    "ChapQuantileTarget",
    "ChapRejection",
    "ChapRunPredictionSetupRequest",
    "ChapSystemInfo",
    "__version__",
]
