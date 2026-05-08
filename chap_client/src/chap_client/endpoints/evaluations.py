"""Evaluation endpoints (chap UI: "Evaluations").

chap-core's REST URLs use ``/v1/crud/backtests`` and
``/v1/analytics/create-backtest``; the chap UI surfaces this concept
as "Evaluation" / "Create Evaluation". chap_client's public methods
follow the UI naming so callers reading the chap UI see the same
words in their code. Wire URLs are unchanged.
"""

from typing import Any

from chap_client._client_base import ChapClientBase
from chap_client.schemas import (
    ChapEvaluationEntry,
    ChapEvaluationRead,
    ChapJobResponse,
    ChapMakeEvaluationRequest,
)


class EvaluationsEndpoints(ChapClientBase):
    """Methods for ``/v1/crud/backtests`` + ``/v1/analytics/create-backtest`` + ``/v1/analytics/evaluation-entry``."""

    def list_evaluations(self) -> list[ChapEvaluationRead]:
        """List evaluations (``GET /v1/crud/backtests``; UI: "Evaluations").

        Each entry carries the evaluation's ``aggregate_metrics`` dict
        once the run has finished -- the canonical "evaluation result"
        surface.
        """
        raw = self.get("/v1/crud/backtests")
        return [ChapEvaluationRead.model_validate(item) for item in raw]

    def get_evaluation(self, id: int) -> ChapEvaluationRead:
        """Fetch a single evaluation by id (``GET /v1/crud/backtests/{id}/info``).

        Note: chap also exposes ``/v1/crud/backtests/{id}/full`` with
        a richer (and more expensive) payload. We only model ``/info``
        today; reach for ``client.get(...)`` for the full shape until
        a typed wrapper exists.

        Args:
            id: Numeric evaluation id from :meth:`list_evaluations`.

        Raises:
            ChapHttpError: chap returned a non-2xx response.
        """
        return ChapEvaluationRead.model_validate(self.get(f"/v1/crud/backtests/{id}/info"))

    def delete_evaluation(self, id: int) -> None:
        """Delete an evaluation by id (``DELETE /v1/crud/backtests/{id}``).

        chap returns no body; this method always returns ``None`` on
        success.

        Raises:
            ChapHttpError: chap returned a non-2xx response.
        """
        self.request("DELETE", f"/v1/crud/backtests/{id}")

    def create_evaluation(self, request: ChapMakeEvaluationRequest) -> ChapJobResponse:
        """Submit an evaluation job (``POST /v1/analytics/create-backtest``; UI: "Create Evaluation").

        Returns immediately with a job id; poll :meth:`job_status`
        until terminal, then fetch the finished evaluation with
        :meth:`get_evaluation` (whose ``aggregate_metrics`` is the
        summary) or :meth:`evaluation_entries` (per-row predictions).

        Args:
            request: Evaluation configuration. ``model_id`` is the
                **configured-model name** (a string), not the integer
                id from ``/v1/crud/configured-models``.

        Returns:
            The job-id wrapper.

        Raises:
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        body = request.model_dump(by_alias=True, mode="json", exclude_none=True)
        return ChapJobResponse.model_validate(self.post("/v1/analytics/create-backtest", json=body))

    def evaluation_entries(
        self,
        evaluation_id: int,
        quantiles: list[float],
        *,
        split_period: str | None = None,
        org_units: list[str] | None = None,
    ) -> list[ChapEvaluationEntry]:
        """Pull per-row evaluation values for a finished evaluation.

        Calls ``GET /v1/analytics/evaluation-entry``. The query
        parameter on the wire is still ``backtestId`` -- chap's REST
        terminology hasn't been updated to match the UI yet.

        Args:
            evaluation_id: Numeric id of the finished evaluation.
            quantiles: Quantiles to return (e.g. ``[0.1, 0.5, 0.9]``).
                chap requires at least one.
            split_period: Optional filter -- limit to a single
                evaluation split.
            org_units: Optional filter -- limit to specific org units.

        Returns:
            A list of :class:`~chap_client.schemas.ChapEvaluationEntry`;
            one row per ``(orgUnit, period, quantile, splitPeriod)``.

        Raises:
            ValueError: ``quantiles`` was empty (chap would reject the
                request anyway).
            ChapHttpError: chap returned a non-2xx response.
        """
        if not quantiles:
            raise ValueError("quantiles must contain at least one value")
        params: dict[str, Any] = {"backtestId": evaluation_id, "quantiles": quantiles}
        if split_period is not None:
            params["splitPeriod"] = split_period
        if org_units:
            params["orgUnits"] = org_units
        raw = self.get("/v1/analytics/evaluation-entry", params=params)
        return [ChapEvaluationEntry.model_validate(item) for item in raw]
