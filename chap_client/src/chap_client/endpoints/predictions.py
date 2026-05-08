"""Prediction + job endpoints.

chap predictions run as jobs: ``submit_prediction`` returns a job id
and the actual values land at ``/v1/analytics/prediction-entry/{id}``
once the job has finished. The job lookup endpoints
(``job_status``, ``job_description``) are also exposed here because
they're exercised primarily by the prediction polling loop.
"""

from chap_client.base import ChapClientBase
from chap_client.errors import ChapHttpError
from chap_client.schemas import (
    ChapJobDescription,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapPredictionEntry,
)


class PredictionsEndpoints(ChapClientBase):
    """Methods for prediction submit + job polling + prediction-entry fetch."""

    def submit_prediction(self, request: ChapMakePredictionRequest) -> ChapJobResponse:
        """Submit a prediction job (``POST /v1/analytics/make-prediction-with-data-source``).

        Returns immediately with a job id; poll `job_status()`
        until terminal, then fetch the result with
        `prediction_entries()` (after looking up the prediction id
        via `job_description()`).

        Args:
            request: The prediction body, including the org-unit
                GeoJSON and the input observations.

        Raises:
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        body = request.model_dump(by_alias=True, mode="json")
        return ChapJobResponse.model_validate(self.post("/v1/analytics/make-prediction-with-data-source", json=body))

    def job_status(self, job_id: str) -> str:
        """Poll a single chap job; returns the bare status string.

        ``GET /v1/jobs/{id}`` returns a quoted-JSON string -- e.g. the
        bytes ``"SUCCESS"`` (length 9, including the quotes) -- which
        httpx parses back to a Python ``str``. We strip whitespace
        defensively in case chap ever surrounds the value with padding.
        """
        body = self.get(f"/v1/jobs/{job_id}")
        if not isinstance(body, str):
            raise ChapHttpError(
                method="GET",
                path=f"/v1/jobs/{job_id}",
                status=200,
                detail=f"expected string job-status response, got {type(body).__name__}: {body!r}",
            )
        return body.strip()

    def list_jobs(self) -> list[ChapJobDescription]:
        """List every job chap currently has on record.

        Calls ``GET /v1/jobs``. Each entry exposes the job's id, type,
        status, and -- for finished jobs -- the ``result`` (the
        prediction or evaluation id chap stored).
        """
        return [ChapJobDescription.model_validate(entry) for entry in self.get("/v1/jobs")]

    def job_description(self, job_id: str) -> ChapJobDescription | None:
        """Find a single job's full description (incl. ``result``) by id.

        ``GET /v1/jobs/{id}`` returns only the status string, so to
        read the ``result`` field (which holds the prediction or
        evaluation id once the job has succeeded) we list
        ``GET /v1/jobs`` and filter client-side. Cheap as long as the
        job table stays small.
        """
        for job in self.list_jobs():
            if job.id == job_id:
                return job
        return None

    def prediction_entries(
        self,
        prediction_id: int,
        quantiles: list[float],
    ) -> list[ChapPredictionEntry]:
        """Fetch the actual predicted values for a prediction at the given quantiles.

        Uses ``GET /v1/analytics/prediction-entry/{id}?quantiles=...``
        -- the same endpoint the chap-frontend uses. chap requires at
        least one quantile, otherwise it returns 422.
        """
        if not quantiles:
            raise ValueError("quantiles must contain at least one value")
        raw = self.get(
            f"/v1/analytics/prediction-entry/{prediction_id}",
            params={"quantiles": quantiles},
        )
        return [ChapPredictionEntry.model_validate(item) for item in raw]
