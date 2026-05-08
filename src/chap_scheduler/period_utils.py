"""DHIS2 ISO period-id math.

Vendored verbatim from `dhis2-client`'s `dhis2_client.utils.calendar`
when chap-scheduler migrated DHIS2 access to `dhis2w-client`. The
ISO-period helpers `next_period_id`, `period_key`, and
`period_start_end` are pure utilities with no calendar-system
dependencies (everything chap-scheduler probes uses ISO 8601 /
gregorian periods); we keep them in-tree rather than coupling to
dhis2-client just for these three functions.

Supported period shapes (`{period_id}` -> `{startDate, endDate}` / next):

- Daily            ``YYYYMMDD``
- Date range       ``YYYYMMDD_YYYYMMDD``  (period_start_end only)
- Weekly ISO       ``YYYYWww`` (Monday-anchored)
- Monthly          ``YYYYMM``
- Quarterly        ``YYYYQn``  (n in 1-4)
- Six-monthly      ``YYYYS1`` / ``YYYYS2``  (Jan-Jun / Jul-Dec)
- Yearly           ``YYYY``

Anything else raises `ValueError` with the offending id in the message.
"""

from __future__ import annotations

import calendar as _calendar
import re as _re
from datetime import date as _date
from datetime import datetime as _dt
from datetime import timedelta as _td


def period_start_end(period_id: str) -> dict[str, str]:
    """Return ``{"startDate": ..., "endDate": ...}`` (ISO yyyy-mm-dd) for ``period_id``.

    Raises:
        ValueError: ``period_id`` doesn't match any supported shape.
    """
    s = period_id.strip()

    m = _re.fullmatch(r"(\d{8})_(\d{8})", s)
    if m:
        sd = _dt.strptime(m.group(1), "%Y%m%d").date()
        ed = _dt.strptime(m.group(2), "%Y%m%d").date()
        if ed < sd:
            raise ValueError(f"Invalid period range (end<start): {s}")
        return {"startDate": sd.isoformat(), "endDate": ed.isoformat()}

    if _re.fullmatch(r"\d{8}", s):
        d = _dt.strptime(s, "%Y%m%d").date()
        return {"startDate": d.isoformat(), "endDate": d.isoformat()}

    m = _re.fullmatch(r"(\d{4})W(\d{2})", s)
    if m:
        y, w = int(m.group(1)), int(m.group(2))
        start = _dt.fromisocalendar(y, w, 1).date()
        end = start + _td(days=6)
        return {"startDate": start.isoformat(), "endDate": end.isoformat()}

    m = _re.fullmatch(r"(\d{4})Q([1-4])", s)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        sm = {1: 1, 2: 4, 3: 7, 4: 10}[q]
        em = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        start = _date(y, sm, 1)
        end = _date(y, em, _calendar.monthrange(y, em)[1])
        return {"startDate": start.isoformat(), "endDate": end.isoformat()}

    m = _re.fullmatch(r"(\d{4})S([12])", s)
    if m:
        y, half = int(m.group(1)), int(m.group(2))
        if half == 1:
            start = _date(y, 1, 1)
            end = _date(y, 6, 30)
        else:
            start = _date(y, 7, 1)
            end = _date(y, 12, 31)
        return {"startDate": start.isoformat(), "endDate": end.isoformat()}

    m = _re.fullmatch(r"(\d{4})(\d{2})", s)
    if m:
        y, mm = int(m.group(1)), int(m.group(2))
        if not 1 <= mm <= 12:
            raise ValueError(f"Invalid month in period id: {s}")
        start = _date(y, mm, 1)
        end = _date(y, mm, _calendar.monthrange(y, mm)[1])
        return {"startDate": start.isoformat(), "endDate": end.isoformat()}

    if _re.fullmatch(r"\d{4}", s):
        y = int(s)
        return {"startDate": _date(y, 1, 1).isoformat(), "endDate": _date(y, 12, 31).isoformat()}

    raise ValueError(f"Unsupported or unrecognized period id: {period_id}")


def next_period_id(period_id: str) -> str:
    """Return the period id immediately following ``period_id``.

    Raises:
        ValueError: ``period_id`` doesn't match any supported shape.
    """
    s = period_id.strip()

    if _re.fullmatch(r"\d{8}", s):
        d = _dt.strptime(s, "%Y%m%d").date() + _td(days=1)
        return d.strftime("%Y%m%d")

    m = _re.fullmatch(r"(\d{4})W(\d{2})", s)
    if m:
        y, w = int(m.group(1)), int(m.group(2))
        start = _dt.fromisocalendar(y, w, 1).date()
        nxt = start + _td(days=7)
        ny, nw, _ = nxt.isocalendar()
        return f"{ny}W{nw:02d}"

    m = _re.fullmatch(r"(\d{4})S([12])", s)
    if m:
        y, h = int(m.group(1)), int(m.group(2))
        return f"{y:04d}S2" if h == 1 else f"{y + 1:04d}S1"

    m = _re.fullmatch(r"(\d{4})Q([1-4])", s)
    if m:
        y, q = int(m.group(1)), int(m.group(2)) + 1
        return f"{y + 1:04d}Q1" if q == 5 else f"{y:04d}Q{q}"

    m = _re.fullmatch(r"(\d{4})(\d{2})", s)
    if m:
        y, mm = int(m.group(1)), int(m.group(2)) + 1
        return f"{y + 1:04d}01" if mm == 13 else f"{y:04d}{mm:02d}"

    if _re.fullmatch(r"\d{4}", s):
        return f"{int(s) + 1:04d}"

    raise ValueError(f"Unsupported or unrecognized period id: {period_id}")


def period_key(period_id: str) -> tuple[int, int, int]:
    """Sortable key so ``max(periods, key=period_key)`` gives the latest period.

    Maps each id to ``(start_ordinal, end_ordinal, 0)`` -- a tuple that
    orders correctly across the supported period shapes (daily up
    against weekly up against monthly etc.) since the start / end
    ordinals are always the canonical anchor + length of that period.
    """
    bounds = period_start_end(period_id)
    start = _date.fromisoformat(bounds["startDate"]).toordinal()
    end = _date.fromisoformat(bounds["endDate"]).toordinal()
    return (start, end, 0)
