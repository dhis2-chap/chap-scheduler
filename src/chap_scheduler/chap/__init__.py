"""chap-side models, error type, and client used by the scheduler.

Most chap-side types now live in :mod:`chap_client`; the imports below
re-export them at their historical location so existing call sites
(``from chap_scheduler.chap import ChapSystemInfo`` etc.) keep working.
"""

from chap_client import (
    ChapClient,
    ChapConfiguredModel,
    ChapConfiguredModelWithDataSource,
    ChapDataSource,
    ChapFetchRequest,
    ChapHttpError,
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
from chap_scheduler.chap.models import (
    Dhis2AnalyticsHeader,
    Dhis2AnalyticsResponse,
    Dhis2OrgUnit,
    Dhis2OrgUnitsResponse,
    Dhis2SystemInfo,
    ModelRunEntry,
    RunReport,
)
from chap_scheduler.chap.report import render_report

__all__ = [
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
    "Dhis2AnalyticsHeader",
    "Dhis2AnalyticsResponse",
    "Dhis2OrgUnit",
    "Dhis2OrgUnitsResponse",
    "Dhis2SystemInfo",
    "ModelRunEntry",
    "RunReport",
    "render_report",
]
