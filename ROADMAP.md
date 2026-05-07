# Roadmap

This file tracks deferred items from the post-prototype review on
2026-05-07. Completed items are removed once landed; the rest is
parked here so it isn't lost.

Severity is informal — pick what's worth doing next based on context.
Numbering matches the original review for traceability (gaps are
intentional — they're items already shipped).

## Docs / discoverability

- **#16 — No `CHANGELOG.md`.** Hand-curated would do; tag releases
  to anchor it.
- **#17 — No `CONTRIBUTING.md` / PR template / `SECURITY.md`.**
  Defer until the project graduates from prototype; the security
  note (loopback-only, unauthenticated Prefect UI) belongs in
  `SECURITY.md` when it lands.
- **#18 — `pyproject.toml` docstring suppressions hide intended
  tool strictness.** D102/D105/D107/D104 are blanket-suppressed
  across `src/**/*.py`. Document which checks are deliberately
  relaxed (in `CLAUDE.md` or as a comment in pyproject) so readers
  don't assume "ruff D selected → docstrings everywhere".

## Test coverage

- **#21 — `probe_latest_covariate_periods` task untested.** Pure-
  helper `_safe_end_period` is covered; the row-parsing inside the
  task (`len(row) < 4` skip, period_key max-tracking) is dark.
- **#22 — `_default_prediction_name` no-end-period path untested.**
  Only the explicit `end_date` test exists; the fallback through
  `_resolve_end_period` is dark.
- **#23 — `Dhis2SystemInfo.revision` set vs unset.** Renderer's
  `if d.revision:` branch is uncovered.
## Operational maturity

- **#26 — Reproducible Docker image.** `python:3.13-slim` is a
  rolling tag; pin to a digest. CI doesn't build the image at all.
- **#27 — Worker registration race.** Two concurrent workers would
  both register the block type (Prefect handles it idempotently,
  but the failure mode isn't documented).
- **#29 — No image-build / e2e in CI.** CI is `make check` +
  `make test`. The compose stack and live-DHIS2 paths are tested
  by hand only.
- **#30 — Print-driven flow logging.** Adopt `prefect.get_run_logger()`
  for structured logs (level, timestamps owned by Prefect, not us).

## Security / threat-model

- **#31 — `SECURITY.md` covering the loopback-only / no-auth-in-Prefect-UI
  story.** Once we have a proxy-with-auth recipe to recommend.
- **#32 — `Dhis2Credentials` rotation story.** Document how an
  operator rotates the DHIS2 password (edit the block in the UI;
  flows pick up the new value on next run).
- **#33 — `compose.yml` Postgres password is the literal `prefect`.**
  Fine on loopback; document as a footgun for anyone copying the
  compose file.
- **#34 — No request-size limits on the embedded Prefect API.**
  Prefect defaults apply. Bound this when we know what kinds of
  requests are actually possible.

## Larger items

- **Extend `ChapClient` into a full-coverage chap SDK.** Today it
  only covers the slice this scheduler needs (system info, configured
  models, submit prediction, job status/description, prediction
  entries). Grow it to a single in-tree client that also handles
  evaluations / backtests, configured-model-with-data-source CRUD
  (read + write), make-prediction long-poll convenience helpers,
  and any other chap routes worth using. Keep it inside this repo
  for now; once it's stable and there's a second consumer (chap
  frontend, another scheduler-style tool) we extract it into its
  own package (`chap-client`?). Until then it lives in
  `src/chap_scheduler/chap/client.py`.

  **Important:** the same client must also work against chap
  *directly*, not only via DHIS2's `/api/routes/chap/run/*` routes.
  This scheduler uses the route API for convenience (single auth
  surface against DHIS2), but chap exposes the same endpoints on
  its own port. Make the route prefix and auth strategy
  parameterisable so consumers can construct either a
  `ChapClient(via_dhis2=Dhis2Credentials(...))` or a
  `ChapClient(direct="http://chap.internal:8000", auth=...)`.

## Open architectural questions

These came up during build-out and are documented elsewhere; pasting
the headlines so they're visible from the roadmap.

- **Flow state for partial-failure runs.** Today: always Completed.
  Plan file `~/.claude/plans/will-it-still-create-iridescent-waffle.md`
  has a worked-out proposal (Failed when zero entries succeeded;
  Completed-with-message when mixed) — never executed because the
  user wanted to think more about it.
- **chap response moving from 400 to 200 for partial rejections.**
  Upstream chap PR pending; once merged, parse the same
  `ChapMissingValuesDetail` shape from a success body and treat as
  partial-success rather than failure. TODO already in
  `chap/models.py` (`ChapMissingValuesDetail` docstring) and
  `flows/dhis2_chap_prediction.py` (the `_StepFailure` branch).
