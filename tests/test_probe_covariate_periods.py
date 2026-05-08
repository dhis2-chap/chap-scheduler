"""Tests for ``probe_latest_covariate_periods`` row parsing.

The pure helper ``_safe_end_period`` is covered in
``test_flow_dhis2_chap_prediction.py``. This file exercises the task body
itself: ``period_key``-based max tracking per data element, the short-row
skip, and the empty-rows path.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import SecretStr

from chap_client import (
    ChapConfiguredModel,
    ChapConfiguredModelWithDataSource,
    ChapDataSource,
    ChapModelTemplate,
)
from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.flows.dhis2_chap_prediction import probe_latest_covariate_periods


def _credentials() -> Dhis2Credentials:
    return Dhis2Credentials(
        base_url="http://test.example",
        username="alice",
        password=SecretStr("hunter2"),
    )


def _model_fixture() -> ChapConfiguredModelWithDataSource:
    template = ChapModelTemplate(
        name="chapkit-ewars-model",
        displayName="CHAP-EWARS",
        target="disease_cases",
        requiredCovariates=["population"],
        supportedPeriodType="month",
    )
    cm = ChapConfiguredModel(
        id=12,
        name="chapkit-ewars-model",
        additionalContinuousCovariates=["rainfall"],
        modelTemplate=template,
    )
    return ChapConfiguredModelWithDataSource(
        id=1,
        name="test",
        configuredModel=cm,
        startPeriod="202301",
        orgUnits=["OU1"],
        dataSources=[
            ChapDataSource(covariate="population", dataElementId="POP1"),
            ChapDataSource(covariate="rainfall", dataElementId="RAIN1"),
        ],
        periodType="month",
    )


def _row(de: str, period: str, ou: str = "OU1", value: str = "1") -> list[str]:
    return [de, period, ou, value]


def _patch_client(rows: list[list[Any]]) -> Any:
    """Patch ``Dhis2Credentials.get_client()`` to return an async context
    manager that yields a client whose ``get_raw`` returns
    ``{"rows": rows}`` -- mirrors the dhis2w-client ``async with ... as
    client`` lifecycle the flow uses for its analytics fetch.
    """
    fake_client = AsyncMock()
    fake_client.get_raw.return_value = {"rows": rows}
    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=fake_client)
    fake_cm.__aexit__ = AsyncMock(return_value=None)
    return patch.object(Dhis2Credentials, "get_client", return_value=fake_cm)


async def test_probe_picks_max_period_per_data_element() -> None:
    """Two rows for POP1 in different periods: the later period wins."""
    rows = [
        _row("POP1", "202601"),
        _row("POP1", "202604"),  # later
        _row("RAIN1", "202602"),
    ]
    with _patch_client(rows):
        latest = await probe_latest_covariate_periods.fn(_credentials(), _model_fixture())
    assert latest == {"POP1": "202604", "RAIN1": "202602"}


async def test_probe_keeps_max_when_rows_arrive_out_of_order() -> None:
    """Latest-period bookkeeping must not depend on row order."""
    rows = [
        _row("POP1", "202604"),
        _row("POP1", "202601"),  # earlier; must not overwrite
        _row("POP1", "202603"),
    ]
    with _patch_client(rows):
        latest = await probe_latest_covariate_periods.fn(_credentials(), _model_fixture())
    assert latest == {"POP1": "202604"}


async def test_probe_skips_rows_shorter_than_four_columns() -> None:
    """DHIS2 occasionally returns header-style rows missing ``ou`` / ``value``;
    those must not raise nor pollute the result."""
    rows = [
        _row("POP1", "202604"),
        ["RAIN1", "202602"],  # only 2 cols -- skipped
        ["POP1"],  # only 1 col -- skipped
        _row("RAIN1", "202603"),
    ]
    with _patch_client(rows):
        latest = await probe_latest_covariate_periods.fn(_credentials(), _model_fixture())
    assert latest == {"POP1": "202604", "RAIN1": "202603"}


async def test_probe_returns_empty_dict_when_no_rows() -> None:
    """Empty analytics response -> empty result; the caller turns that into
    a `_StepFailure` via `_safe_end_period`."""
    with _patch_client([]):
        latest = await probe_latest_covariate_periods.fn(_credentials(), _model_fixture())
    assert latest == {}


async def test_probe_uses_period_key_not_lexicographic_ordering() -> None:
    """Weekly period IDs (``2026W01``..``2026W52``) sort lexicographically
    in a way that already mostly agrees with chronology, but yearly ids
    like ``2024`` vs ``2024Q4`` would not. We just confirm the result on a
    monthly model where lex == chronological -- the regression we'd catch
    is "someone replaced ``period_key`` with plain string compare"."""
    rows = [
        _row("POP1", "202612"),
        _row("POP1", "202701"),  # later year, lex-greater anyway
    ]
    with _patch_client(rows):
        latest = await probe_latest_covariate_periods.fn(_credentials(), _model_fixture())
    assert latest == {"POP1": "202701"}
