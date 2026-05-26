"""Evaluation endpoints (chap UI: "Evaluations").

chap-core's REST URLs use ``/v1/crud/backtests`` and
``/v1/analytics/create-backtest``; the chap UI surfaces this concept
as "Evaluation" / "Create Evaluation". chap_client's public methods
follow the UI naming so callers reading the chap UI see the same
words in their code. Wire URLs are unchanged.
"""

from typing import Any

from chap_client.base import ChapClientBase
from chap_client.errors import ChapHttpError
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

        Note: chap-core ignores unknown / pagination query params
        (``?limit``, ``?modelId``, ``?datasetId``) and always returns
        the full table -- see ``CHAP_CORE_ISSUES.md`` finding 12.
        Filter client-side until upstream adds real pagination.
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
            id: Numeric evaluation id from `list_evaluations()`.

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

    def create_evaluation(
        self,
        request: ChapMakeEvaluationRequest,
        *,
        validate: bool = True,
    ) -> ChapJobResponse:
        """Submit an evaluation job (``POST /v1/analytics/create-backtest``; UI: "Create Evaluation").

        Returns immediately with a job id; poll `job_status()`
        until terminal, then fetch the finished evaluation with
        `get_evaluation()` (whose ``aggregate_metrics`` is the
        summary) or `evaluation_entries()` (per-row predictions).

        With ``validate=True`` (the default), this method preflights
        ``request.model_id`` against `list_configured_models()` and
        ``request.dataset_id`` against `get_dataset()`; if either
        misses, raises ``ValueError`` synchronously. chap-core itself
        does no FK validation here (`CHAP_CORE_ISSUES.md` findings #5,
        #6) -- without the preflight, a typo'd ``modelId`` succeeds
        at submission and only fails the job 1-3 minutes later.

        Pass ``validate=False`` to skip the round-trip when the caller
        already has the live lists in hand or wants to test against a
        deliberately-broken id.

        Args:
            request: Evaluation configuration. ``model_id`` is the
                **configured-model name** (a string), not the integer
                id from ``/v1/crud/configured-models``.
            validate: When True (default), preflight ``model_id`` and
                ``dataset_id`` against chap before submitting.

        Returns:
            The job-id wrapper.

        Raises:
            ValueError: ``validate=True`` and the supplied ``model_id``
                or ``dataset_id`` does not resolve.
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        if validate:
            self._validate_evaluation_request(request)
        body = request.model_dump(by_alias=True, mode="json", exclude_none=True)
        return ChapJobResponse.model_validate(self.post("/v1/analytics/create-backtest", json=body))

    def _validate_evaluation_request(self, request: ChapMakeEvaluationRequest) -> None:
        """Preflight a `create_evaluation` request against current chap state.

        Mitigates `CHAP_CORE_ISSUES.md` findings #5 (modelId unvalidated)
        and #6 (datasetId unvalidated) by failing fast with a clear
        message instead of letting the chap worker discover the mistake
        asynchronously.
        """
        # `list_configured_models` is on `ModelsEndpoints`, but ChapClient
        # composes this mixin in alongside ours so the call resolves at
        # runtime via MRO. Cast suppresses the warning on the typed
        # mixin boundary.
        configured = self.list_configured_models()  # type: ignore[attr-defined]
        names = {m.name for m in configured}
        if request.model_id not in names:
            sample = sorted(names)[:8]
            raise ValueError(
                f"modelId={request.model_id!r} does not match any configured model on this chap "
                f"(first {len(sample)} known: {sample}). "
                f"Pass validate=False to skip this check."
            )
        try:
            self.get_dataset(request.dataset_id)  # type: ignore[attr-defined]
        except ChapHttpError as e:
            if e.status == 404:
                raise ValueError(
                    f"datasetId={request.dataset_id} does not exist on this chap. "
                    f"Pass validate=False to skip this check."
                ) from e
            raise

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
            A list of `ChapEvaluationEntry`;
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
