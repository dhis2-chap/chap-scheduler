"""Endpoints for ``/v1/crud/prediction-setups/*``.

A prediction setup is a 1-1 child of a backtest. It owns the DHIS2
covariate-source and quantile-target mappings, a snapshot of the
backtest's dataset shape (start period, org units, period type), and
the cron schedule + enabled flag (consumed by chap-scheduler, not
chap-core). This is the resource the chap-scheduler Prefect flow
iterates and the resource that receives prediction-run requests at
``POST .../{id}/run``.

Replaces the old ``configured-models-with-data-source`` endpoints after
chap-core PR #354.
"""

from chap_client.base import ChapClientBase
from chap_client.schemas import ChapJobResponse, ChapPredictionSetup, ChapRunPredictionSetupRequest


class PredictionSetupsEndpoints(ChapClientBase):
    """Methods for ``/v1/crud/prediction-setups``."""

    def list_prediction_setups(self) -> list[ChapPredictionSetup]:
        """List every prediction setup (``GET /v1/crud/prediction-setups``)."""
        raw = self.get("/v1/crud/prediction-setups")
        return [ChapPredictionSetup.model_validate(item) for item in raw]

    def get_prediction_setup(self, id: int) -> ChapPredictionSetup:
        """Fetch a single prediction setup by id.

        Calls ``GET /v1/crud/prediction-setups/{id}``.

        Note: chap returns a richer ``PredictionSetupReadWithPredictions``
        shape on this endpoint (it embeds the prediction list); we only
        model the common fields and silently ignore the rest via
        ``extra="ignore"``. Add ``predictions`` to
        `ChapPredictionSetup` if/when callers need it.

        Args:
            id: Numeric id from `list_prediction_setups()`.

        Raises:
            ChapHttpError: chap returned a non-2xx response.
        """
        return ChapPredictionSetup.model_validate(self.get(f"/v1/crud/prediction-setups/{id}"))

    def run_prediction_setup(
        self,
        setup_id: int,
        request: ChapRunPredictionSetupRequest,
    ) -> ChapJobResponse:
        """Fire a prediction job from this setup.

        Calls ``POST /v1/crud/prediction-setups/{setup_id}/run``. Returns
        immediately with a job id; poll via `PredictionsEndpoints.job_status`
        until terminal, then fetch the result with `prediction_entries()`
        (after looking up the prediction id via `job_description()`).

        Args:
            setup_id: Numeric id from `list_prediction_setups()`.
            request: The prediction body (input observations + geojson +
                horizon). The configured model is derived from the setup;
                do not pass it in the body.

        Raises:
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        body = request.model_dump(by_alias=True, mode="json")
        return ChapJobResponse.model_validate(self.post(f"/v1/crud/prediction-setups/{setup_id}/run", json=body))
