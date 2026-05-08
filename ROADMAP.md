# Roadmap

This file tracks deferred items. Completed items are removed once landed.
Numbering is monotonic for traceability — gaps are intentional (items
already shipped or explicitly declined).

Last full review pass: **2026-05-09** (after the chap_client extraction
+ Typer CLI + rich tables + the chap-core/chap-frontend drift sweep
landed, plus the migration from `dhis2-client` to `dhis2w-client` /
async flow tasks; 33 upstream findings collected in
`chap_client/CHAP_SPEC_DRIFT.md`).

Severity is informal — pick what's worth doing next based on context.

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
- **#52 — Preflight cardinality estimate.** Today the flow finds out
  how big the analytics response is by fetching it; an oversized
  configured model can OOM the worker before we have a chance to bail.
  A preflight `GET /api/analytics?...&dimension=...&aggregationType=COUNT`
  (or similar) would let us check the row count against a budget and
  fail with a clear diagnostic, instead of OOMKilling. The
  "Scalability envelope" section in `docs/operations.md` documents
  the current limits and operator workarounds in the meantime.
- **#51 — Stay on sync tasks for now.** Reviewed 2026-05-08. The
  primary blocker is that `dhis2-client` is sync-only with no async
  API or custom-transport hook, so an async migration would either
  wrap analytics / org-unit calls in `asyncio.to_thread` (zero
  concurrency benefit) or bypass the library and duplicate its work
  via `httpx.AsyncClient` directly. The current sync design is correct
  on Prefect's thread-pool runner with 1-3 configured models per run
  and a polling loop that blocks one worker thread, not the engine
  event loop. Reconsider when *any* of these change:
  (a) dhis2-client gains native async support upstream;
  (b) per-flow-run concurrency hits thread-pool pressure (much larger
  configured-model lists, or many concurrent flow runs on one worker);
  (c) flow runs move in-process with the FastAPI app and start sharing
  an event loop with the embedded Prefect server.

## chap_client (extracted, in-tree path-dep)

`chap_client/` lives as a sibling package wired in via uv path-dep.
Public surface is the typed `ChapClient` (six endpoint mixins), 18
pydantic schemas, a `chap-client` Typer CLI with rich/colour output
and tables, and `CHAP_SPEC_DRIFT.md` cataloguing 33 upstream
findings. Coverage today is 19/65 chap-core endpoints (29%).

The actions below are the punch list from
[`chap_client/CHAP_SPEC_DRIFT.md`](chap_client/CHAP_SPEC_DRIFT.md)'s
"What we can do in this repo" section -- recommended ordering is
A → B → C+D → coverage sweep → externalise.

- **#56 — Workarounds for chap-core's broken response shapes.** Group
  C. Two specific moves: (a) don't model `evaluation_result` /
  `prediction_result` / `/df` (they're unusable; finding #20 / #26),
  prefer the working sibling endpoints; (b) for `/csv` if/when
  modelled, remap chap's 500-with-"not found" body to a typed
  `ChapHttpError(status=404)` so callers can branch on `e.status`
  (finding #27).

- **#57 — Docstring drift notes.** Group D. One-liner notes in
  chap_client method docstrings pointing at the relevant
  `CHAP_SPEC_DRIFT.md` finding for issues with no client-side
  mitigation. Today some are already there (#2, #4, #8, #23). The
  remaining touches are #11, #12, #24, #25 plus a sweep to make sure
  every method has the link if it has a known surprise.

- **#58 — Coverage sweep to ~35%.** Today 19/65 endpoints. Add
  `list_model_templates()` (also unblocks #54's preflight against
  the `modelTemplateId` id space), the `/v1/visualization/{kind}-plots/`
  catalogue endpoints (they return a useful catalogue list, not
  byte blobs), and the `/v1/analytics/data-sources` discovery
  endpoint. Skips the actually-broken ones (#20 etc.).

- **#59 — Externalise `chap_client/` to its own repo.** Group F. The
  README already flags this as the eventual goal. Concrete blockers:

  - [ ] Land #54-#58 first (better public API for an external
    consumer).
  - [ ] Switch from uv path-dep to a published version on PyPI.
        chap-scheduler then depends on the published `chap-client`.
  - [ ] CI/release pipeline on the new repo (versioning, CHANGELOG,
        deprecation policy -- today the README says "experimental").
  - [ ] Document the four deployment shapes (direct chap, via DHIS2
        proxy, OIDC behind a gateway, k8s ingress with token auth)
        and verify each with the existing `auth=` parameter.
  - [ ] `CHAP_SPEC_DRIFT.md` travels with the package on extraction
        (already lives in `chap_client/`, so it's a `git mv`).

- **#60 — File the `CHAP_SPEC_DRIFT.md` findings against
  chap-core / chap-frontend upstream.** Currently 33 findings tracked
  in-tree, nothing filed. Triage list is in the doc footer; first
  tier is #17 (CORS reflective + credentials -- security), #20
  (`response_model` broken on two endpoints), #26 (`/df` 500s on
  every dataset with NaN), and #28 (broken bug-report mailto link).
  As issues are filed, drop the issue/PR link next to the finding
  number in the markdown.

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
  `chap_client/src/chap_client/schemas.py` (`ChapMissingValuesDetail`
  docstring) and `src/chap_scheduler/flows/dhis2_chap_prediction.py`
  (the `_StepFailure` branch).
