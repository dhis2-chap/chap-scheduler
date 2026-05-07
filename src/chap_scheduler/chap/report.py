"""Render a :class:`~chap_scheduler.chap.models.RunReport` as markdown."""

from datetime import datetime, timezone

from chap_scheduler.chap.models import ModelRunEntry, RunReport


def _fmt_duration(start: datetime, end: datetime) -> str:
    seconds = max(0, int((end - start).total_seconds()))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec:02d}s"


def _render_error(error: str) -> list[str]:
    """Render an error as inline code if short, otherwise a fenced block."""
    if len(error) <= 200 and "\n" not in error:
        return [f"- **Error:** `{error}`"]
    return ["- **Error:**", "", "```", error, "```"]


def _render_entry(entry: ModelRunEntry) -> list[str]:
    lines = [f"### `{entry.name}` -- {entry.template_name}", ""]
    if entry.status == "succeeded":
        lines.append("- **Status:** SUCCEEDED")
    else:
        step = entry.step_failed or "unknown step"
        lines.append(f"- **Status:** FAILED at `{step}`")
        if entry.error:
            lines.extend(_render_error(entry.error))
    if entry.job_id:
        lines.append(f"- Job: `{entry.job_id}`")
    if entry.org_units_covered is not None:
        lines.append(f"- Org units: {entry.org_units_covered}")
    if entry.periods_covered is not None:
        lines.append(f"- Periods: {entry.periods_covered}")
    if entry.analytics_rows is not None:
        lines.append(f"- Analytics rows fetched: {entry.analytics_rows:,}")
    if entry.prediction_values is not None:
        lines.append(f"- Prediction values returned: {entry.prediction_values:,}")
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
        if d.revision:
            lines.append(f"- revision: `{d.revision}`")
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
        return "\n".join(lines)

    succeeded = sum(1 for e in report.entries if e.status == "succeeded")
    failed = len(report.entries) - succeeded
    lines.append(f"## Configured models ({succeeded} succeeded, {failed} failed)")
    lines.append("")

    if not report.entries:
        lines.append("No configured models were returned by chap.")
        return "\n".join(lines)

    for entry in report.entries:
        lines.extend(_render_entry(entry))

    return "\n".join(lines).rstrip() + "\n"
