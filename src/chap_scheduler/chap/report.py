"""Render a :class:`~chap_scheduler.chap.models.RunReport` as markdown."""

from datetime import datetime, timezone

from chap_scheduler.chap.models import ChapMissingValuesDetail, ModelRunEntry, RunReport


def _fmt_duration(start: datetime, end: datetime) -> str:
    seconds = max(0, int((end - start).total_seconds()))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec:02d}s"


def _render_error(error: str) -> list[str]:
    """Render an error as inline code if short, otherwise a fenced block."""
    if len(error) <= 200 and "\n" not in error:
        return [f"- **Error:** `{error}`"]
    return ["- **Error:**", "", "```", error, "```"]


def _render_rejection_summary(detail: ChapMissingValuesDetail) -> list[str]:
    """Aggregate chap's per-cell rejection list into a per-covariate summary.

    Groups the rejected cells by ``feature_name`` so the report shows
    "rainfall: 18 org units missing periods 202510..202512" instead of one
    line per (org_unit, period).
    """
    lines = [f"- **Error:** {detail.message} (imported {detail.imported_count})"]
    if not detail.rejected:
        return lines

    orgs_by_feature: dict[str, set[str]] = {}
    periods_by_feature: dict[str, set[str]] = {}
    reasons_by_feature: dict[str, set[str]] = {}
    for r in detail.rejected:
        orgs_by_feature.setdefault(r.feature_name, set()).add(r.org_unit)
        periods_by_feature.setdefault(r.feature_name, set()).update(r.time_periods)
        reasons_by_feature.setdefault(r.feature_name, set()).add(r.reason)

    lines.append("- **Rejected cells (grouped by covariate):**")
    for feature in sorted(orgs_by_feature):
        n_orgs = len(orgs_by_feature[feature])
        periods = sorted(periods_by_feature[feature])
        reasons = sorted(reasons_by_feature[feature])
        period_summary = (
            f"{periods[0]}..{periods[-1]} ({len(periods)} periods)" if len(periods) > 4 else ", ".join(periods)
        )
        reason_summary = "; ".join(reasons)
        lines.append(f"  - `{feature}`: {n_orgs} org units; periods affected: {period_summary} -- {reason_summary}")
    return lines


def _render_entry(entry: ModelRunEntry) -> list[str]:
    lines = [f"### `{entry.name}` -- {entry.template_name}", ""]
    if entry.status == "succeeded":
        lines.append("- **Status:** SUCCEEDED")
    else:
        step = entry.step_failed or "unknown step"
        lines.append(f"- **Status:** FAILED at `{step}`")
        if entry.rejection_detail is not None:
            lines.extend(_render_rejection_summary(entry.rejection_detail))
        elif entry.error:
            lines.extend(_render_error(entry.error))
    if entry.job_id:
        lines.append(f"- Job: `{entry.job_id}`")
    if entry.prediction_id is not None:
        lines.append(f"- Prediction: `{entry.prediction_id}`")
    if entry.org_units_covered is not None:
        lines.append(f"- Org units: {entry.org_units_covered}")
    if entry.periods_covered is not None:
        lines.append(f"- Input periods: {entry.periods_covered}")
    if entry.analytics_rows is not None:
        lines.append(f"- Analytics rows fetched: {entry.analytics_rows:,}")
    if entry.prediction_values is not None:
        lines.append(f"- Prediction values returned: {entry.prediction_values:,}")
    if entry.predicted_periods:
        if len(entry.predicted_periods) <= 6:
            ps = ", ".join(entry.predicted_periods)
        else:
            ps = f"{entry.predicted_periods[0]}..{entry.predicted_periods[-1]} ({len(entry.predicted_periods)} periods)"
        lines.append(f"- Predicted periods: {ps}")
    lines.append("")
    return lines


def render_report(report: RunReport, *, finished_at: datetime | None = None) -> str:
    """Render ``report`` as a markdown document.

    Args:
        report: The accumulated state from a single flow run.
        finished_at: When the run ended. Defaults to ``datetime.now(UTC)``.
    """
    finished_at = finished_at or datetime.now(timezone.utc)
    duration = _fmt_duration(report.started_at, finished_at)

    lines: list[str] = [
        "# chap-scheduler run report",
        "",
        f"- DHIS2 instance: `{report.dhis2_url}`",
        f"- Started: {report.started_at.isoformat()}",
        f"- Duration: {duration}",
        "",
        "## DHIS2",
        "",
    ]

    if report.dhis2 is not None:
        d = report.dhis2
        line = f"**REACHABLE** -- DHIS2 {d.version}"
        if d.system_name:
            line += f" ({d.system_name})"
        lines.append(line)
        lines.append("")
    else:
        err = report.dhis2_error or "(no error captured)"
        lines.append(f"**NOT REACHABLE** -- `{err}`")
        lines.append("")
        # No point listing chap or per-model state -- DHIS2 is the gateway.
        return "\n".join(lines).rstrip() + "\n"

    lines.append("## chap-core")
    lines.append("")

    if report.chap is not None:
        info = report.chap
        lines.append(f"**REACHABLE** -- chap-core {info.chap_core_version} (python {info.python_version})")
        lines.append("")
    else:
        err = report.chap_error or "(no error captured)"
        lines.append(f"**NOT REACHABLE** -- `{err}`")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    if report.models_error is not None:
        lines.append("## Configured models")
        lines.append("")
        lines.append(f"Could not list configured models: `{report.models_error}`")
        return "\n".join(lines).rstrip() + "\n"

    succeeded = sum(1 for e in report.entries if e.status == "succeeded")
    failed = len(report.entries) - succeeded
    lines.append(f"## Configured models ({succeeded} succeeded, {failed} failed)")
    lines.append("")

    if not report.entries:
        lines.append("No configured models were returned by chap.")
        return "\n".join(lines).rstrip() + "\n"

    for entry in report.entries:
        lines.extend(_render_entry(entry))

    return "\n".join(lines).rstrip() + "\n"
