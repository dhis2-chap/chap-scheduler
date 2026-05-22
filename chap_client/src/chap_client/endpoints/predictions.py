"""Prediction job-polling + prediction-entry endpoints.

chap predictions run as jobs: `PredictionSetupsEndpoints.run_prediction_setup`
returns a job id and the actual values land at
``/v1/analytics/prediction-entry/{id}`` once the job has finished. The
job lookup endpoints (``job_status``, ``job_description``, ``list_jobs``,
``wait_for_job``) live here because they're exercised primarily by the
prediction polling loop.
"""

import time
from collections.abc import Callable

from chap_client.base import ChapClientBase
from chap_client.errors import ChapHttpError
from chap_client.schemas import (
    ChapJobDescription,
    ChapPredictionEntry,
)

# Statuses that mean "still working; poll again". Anything else
# (SUCCESS, FAILURE, FAILED, CANCELLED, ERROR, ...) is terminal.
# Compared case-insensitively so chap variations don't slip through.
_TRANSIENT_JOB_STATUSES = frozenset({"PENDING", "RUNNING", "STARTED", "QUEUED", "PROCESSING"})


class PredictionsEndpoints(ChapClientBase):
    """Methods for job polling + prediction-entry fetch."""

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

        Note: chap-core ignores unknown / pagination query params
        (``?limit``, ``?status``, ``?type``) and always returns the
        full table -- see ``CHAP_SPEC_DRIFT.md`` finding 12. Plan for
        client-side pagination once the table grows.
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

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = 5.0,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Poll a chap job until it reaches a terminal status, with a membership check first.

        Mitigates `CHAP_SPEC_DRIFT.md` finding #7: ``GET /v1/jobs/{id}``
        returns 200 ``"PENDING"`` for a non-existent id, so a typo'd
        UUID would otherwise loop forever (or until the caller's
        timeout). This helper lists jobs once, refuses synchronously
        with ``ValueError`` when the id isn't present, and only then
        enters the polling loop.

        Sync `time.sleep` between polls. This is fine inside a
        Prefect sync task (the sleep blocks one worker thread, not
        the engine event loop) and inside ad-hoc CLI use; convert to
        ``asyncio.sleep`` if/when chap_client grows an async API.

        Args:
            job_id: The job id returned from `run_prediction_setup()` /
                `create_evaluation()`.
            timeout: Total wait budget in seconds (default 600s,
                matching the chap-scheduler flow's prior default).
            poll_interval: Seconds between polls. Default 5s.
            on_status: Optional callback invoked once per *change* in
                status -- the flow uses this for its `Status: ...`
                log lines without chap_client needing to know about
                Prefect's logger.

        Returns:
            The terminal status string (e.g. ``"SUCCESS"``,
            ``"FAILED"``, ``"CANCELLED"``).

        Raises:
            ValueError: ``job_id`` does not appear in
                ``/v1/jobs``. Catches typos / stale ids before any
                wasted polling.
            TimeoutError: ``timeout`` elapsed while the status was
                still transient.
            ChapHttpError: chap returned a non-2xx response on a
                ``job_status`` poll.
        """
        if self.job_description(job_id) is None:
            raise ValueError(
                f"unknown job id: {job_id!r}. chap-core has no record of this job. "
                f"Note: chap returns 200 'PENDING' for unknown ids -- this helper "
                f"checks /v1/jobs membership first to avoid a timeout-long loop "
                f"on typos (see CHAP_SPEC_DRIFT.md finding #7)."
            )

        deadline = time.monotonic() + timeout
        last: str | None = None
        while True:
            status = self.job_status(job_id)
            if status != last:
                if on_status is not None:
                    on_status(status)
                last = status
            if status.upper() not in _TRANSIENT_JOB_STATUSES:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(f"chap job {job_id} did not finish within {timeout}s (last status: {status!r})")
            time.sleep(poll_interval)

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
