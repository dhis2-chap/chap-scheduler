# Roadmap

This file tracks deferred items. Completed items are removed once landed.
Numbering is monotonic for traceability — gaps are intentional (items
already shipped or explicitly declined).

Last full review pass: **2026-05-07** (after the runtime hardening +
GHCR publishing landed; e2e against local DHIS2 + GHCR image both green).

Severity is informal — pick what's worth doing next based on context.

## Test coverage gaps

- **#41 — `PrefectMountMiddleware` is completely untested.** This is
  arguably the most error-prone code in the project (custom ASGI
  dispatcher with prefix-strip and `raw_path` rewrite logic) and has
  zero direct tests. A handful of TestClient-based or pure-ASGI
  unit tests would prove the dispatch logic and lock in the contract.
  **High priority.**
- **#42 — Flow body early-return paths uncovered.** The three guards in
  `dhis2_chap_prediction` (DHIS2 unreachable → return early; chap
  unreachable → return early; `fetch_configured_models` fails →
  return early) all set their respective `_error` field on the
  report and are well-tested in the **renderer** but the flow-side
  branches that populate them are not.
- **#43 — `_resolve_end_period_for_run` missing-covariate branch.**
  When the freshness probe returns partial coverage, the helper raises
  `_StepFailure("probe_latest_covariate_periods")` from a `RuntimeError`
  naming the missing covariates. Untested directly.
- **#44 — `fetch_prediction_result` two-step lookup untested.** The
  task body fetches `job_description` (which lists all jobs) and parses
  the `result` field as an int. Both the "job not in listing" and
  "result is not an int" branches are dark.

## Operational maturity

- **#29 — No live-DHIS2 e2e step in CI.** PR #13 added image-build,
  coverage gating, and strict-docs steps; what's still missing is a
  CI lane that exercises the full DHIS2 → chap → prediction round-
  trip. Hard part: getting a DHIS2 fixture (with a chap install + a
  configured-model row) reachable from the CI runner.
- **#48 — No `make e2e` target.** The "save a `Dhis2Credentials`
  block + trigger the deployment + wait + dump the artifact" recipe is
  what proves a runtime / image / compose change actually works. Wrap
  it as a Make target so it's discoverable. Needs a
  `host.docker.internal:8080` + `admin/district` test DHIS2 reachable;
  document the prereq in the target's `## ` help text.

## Security / threat-model

- **#34 — Bound request-size limits on the embedded Prefect API.**
  `docs/operations.md` documents this as a known footgun. The actual
  TODO is to *bound it* once we know what request shapes are real
  (Block document writes are bounded; flow-run parameter blobs are the
  unknown).

## Defer / opportunistic

- **#49 — Parallel model fan-out.** The flow's `for model in models:`
  loop is sequential. Prefect's `task.submit()` would let independent
  models run concurrently, capped by the worker's task-runner. Worth
  doing only when an operator actually has enough configured models
  for sequential runs to hurt — today most stacks have 1-3.

## Larger items

- **Extend `ChapClient` into a full-coverage chap SDK.** Today it only
  covers the slice this scheduler needs (system info, configured
  models, submit prediction, job status/description, prediction
  entries). Grow it to a single in-tree client that also handles
  evaluations / backtests, configured-model-with-data-source CRUD
  (read + write), make-prediction long-poll convenience helpers, and
  any other chap routes worth using. Keep it inside this repo for
  now; once it's stable and there's a second consumer (chap-frontend,
  another scheduler-style tool), extract to its own package
  (`chap-client`?). Until then it lives in
  `src/chap_scheduler/chap/client.py`.

  **Important:** the same client must also work against chap
  *directly*, not only via DHIS2's `/api/routes/chap/run/*` routes.
  This scheduler uses the route API for convenience (single auth
  surface against DHIS2), but chap exposes the same endpoints on its
  own port. Make the route prefix and auth strategy parameterisable so
  consumers can construct either a
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
