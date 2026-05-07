"""Models and client for the chap routes exposed by DHIS2."""

from chap_scheduler.chap.client import ChapClient, ChapHttpError
from chap_scheduler.chap.models import (
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
    ChapPredictionResult,
    ChapPredictionValue,
    ChapRejection,
    ChapSystemInfo,
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
    "ChapPredictionResult",
    "ChapPredictionValue",
    "ChapRejection",
    "ChapSystemInfo",
    "Dhis2SystemInfo",
    "ModelRunEntry",
    "RunReport",
    "render_report",
]
