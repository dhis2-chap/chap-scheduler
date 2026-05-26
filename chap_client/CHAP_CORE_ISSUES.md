# chap-core API issues — current state

Live re-test of every finding from the prior `CHAP_SPEC_DRIFT.md` /
`CHAP_ISSUES.md` (both now removed; see git history for the old
versions) against chap-core `2.0.0.dev1` on the `feat/prediction-setup` branch
(chap-core PR #354), probed on **2026-05-22** at
`http://localhost:8000`. Each finding keeps its original number so
existing code-comment cross-refs stay valid.

Status legend:
- **FIXED** — re-probe confirms the behaviour described no longer reproduces.
- **SUPERSEDED** — the resource or endpoint the finding referenced has been removed; the question doesn't apply any more.
- **OPEN** — the behaviour still reproduces and the chap_client mitigation is still load-bearing.

Tag legend (for OPEN items, copied from the old `CHAP_ISSUES.md`):
- `[trivial]` — single annotation / validator / status-code swap
- `[medium]` — handler refactor, shape change, or shared-util fix
- `[hard]` — schema migration, security rewrite, or hosting rework

---

## Summary

| #  | Title                                                                      | Status         |
|----|----------------------------------------------------------------------------|----------------|
| 1  | `ModelSpecRead.target` / `.covariates` typed `string` in spec, object on wire | **FIXED**    |
| 2  | `/v1/crud/models` and `/v1/crud/configured-models` return same data        | **FIXED**      |
| 3  | `POST /v1/crud/configured-models` 500s on bogus `modelTemplateId`          | **FIXED**      |
| 4  | `POST /v1/crud/configured-models` mutates the supplied `name`              | **OPEN**       |
| 5  | Evaluation `modelId` is unvalidated free-form string                       | **OPEN**       |
| 6  | `datasetId` on `create-backtest` is unvalidated                            | **FIXED**      |
| 7  | `GET /v1/jobs/{id}` returns 200 `"PENDING"` for unknown ids                | **FIXED**      |
| 8  | `GET /v1/crud/backtests/{id}` returns 405                                  | **FIXED**      |
| 9  | `DELETE /v1/crud/configured-models-with-data-source/{id}` returns 405      | **SUPERSEDED** |
| 10 | `POST /v1/crud/configured-models` 500s when `userOptionValues` omitted     | **FIXED**      |
| 11 | `POST /v1/crud/configured-models` is silently idempotent                   | **OPEN**       |
| 12 | List endpoints silently ignore unknown query params                        | **OPEN**       |
| 13 | Visualization endpoints return 200 with `{"error": ...}` body              | **FIXED**      |
| 14 | `POST /v1/analytics/create-backtest` accepts empty `name`                  | **OPEN**       |
| 15 | `POST /v1/analytics/create-backtest` accepts negative `nPeriods`           | **FIXED**      |
| 16 | POST endpoints silently ignore unknown top-level fields                    | **OPEN**       |
| 17 | CORS reflects any Origin with `allow-credentials: true`                    | **OPEN**       |
| 18 | Most endpoints have no `description` in OpenAPI                            | **OPEN**       |
| 19 | `POST /v1/jobs/<bogus>/cancel` returns 200                                 | **FIXED**      |
| 20 | `/v1/jobs/{id}/{prediction,evaluation}_result` returned bare int           | **FIXED**      |
| 21 | `/v1/jobs/<bogus>/{...}_result` leaked `TaskRevokedError` as 500           | **FIXED**      |
| 22 | `/v1/jobs/<bogus>/logs` returned 200 with empty string                     | **FIXED**      |
| 23 | `/info` and `/full` on backtests have overlapping, confusing shapes        | **OPEN**       |
| 24 | `/v1/analytics/backtest-overlap/{a}/{b}` error message used path position  | **FIXED**      |
| 25 | `/v1/crud/datasets/csvFile` was shadowed by `/datasets/{id}`               | **FIXED**      |
| 26 | `GET /v1/crud/datasets/{id}/df` 500s on `NaN`                              | **FIXED**      |
| 27 | CSV / DF dataset endpoints returned 500 (not 404) for unknown ids          | **FIXED**      |

**Net:** 17 fixed, 1 superseded, 9 open.

---

## FIXED

### 1. `ModelSpecRead.target` / `.covariates` schema fixed in spec

The OpenAPI spec now references `FeatureType` (an object schema with
`displayName` / `description` / `name`) instead of bare `string`. The
wire still returns the object shape; spec and wire agree. `chap_client`'s
`ChapFeature` model lines up with the new spec.

### 2. `/v1/crud/models` is gone

The duplicate endpoint was removed. `/v1/crud/models` returns
`HTTP 404 {"detail":"Not Found"}`. `/v1/crud/configured-models` is the
single source of truth.

### 3. Bad `modelTemplateId` returns 404

```bash
curl -sS -X POST $CHAP/v1/crud/configured-models -d '{"name":"p","modelTemplateId":99999}'
# -> HTTP 404 {"detail":"Model template not found"}
```

The `AssertionError`-as-500 leak is gone.

### 6. Bad `datasetId` on create-backtest returns 404

```bash
curl -sS -X POST $CHAP/v1/analytics/create-backtest -d '{"name":"p","modelId":"chapkit-ewars-model","datasetId":99999}'
# -> HTTP 404 {"detail":"Dataset 99999 not found"}
```

### 7. Unknown job id returns 404

```bash
curl -sS $CHAP/v1/jobs/00000000-0000-0000-0000-000000000000
# -> HTTP 404 {"detail":"Job '00000000-...' not found"}
```

The `chap_client.ChapClient.wait_for_job` membership-pre-check (originally
written to mitigate the silent `"PENDING"` loop) is now belt-and-braces.
We keep it: it still saves a `/v1/jobs` round-trip on the polling-loop
hot path.

### 8. `GET /v1/crud/backtests/{id}` works

Returns the full backtest read shape; method-not-allowed is gone.

### 10. `userOptionValues` defaults to `{}`

```bash
curl -sS -X POST $CHAP/v1/crud/configured-models -d '{"name":"p","modelTemplateId":1}'
# -> HTTP 200, response includes "userOptionValues":{}
```

The jsonschema-rejects-None crash is gone.

### 13. Visualization endpoints raise 404, not 200+error

```bash
curl -sS $CHAP/v1/visualization/backtest-plots/no-such-viz/99999
# -> HTTP 404 {"detail":"Visualization no-such-viz not found. Available: ..."}
```

### 15. Negative `nPeriods` rejected at validation

```bash
curl -sS -X POST $CHAP/v1/analytics/create-backtest -d '{"name":"p","modelId":"...","datasetId":1,"nPeriods":-3}'
# -> HTTP 422 {"detail":[{"type":"greater_than","loc":["body","nPeriods"],"msg":"Input should be greater than 0",...}]}
```

### 19. `POST /v1/jobs/<bogus>/cancel` returns 404

```bash
curl -sS -X POST $CHAP/v1/jobs/00000000-0000-0000-0000-000000000000/cancel
# -> HTTP 404 {"detail":"Job '00000000-...' not found"}
```

### 20. `/prediction_result` and `/evaluation_result` return their declared shape

`/prediction_result` declares `response_model=PredictionInfo` and now
returns the rich `PredictionInfo` object (`{datasetId, modelId, nPeriods,
name, orgUnits, configuredModel, dataset, ...}`). `/evaluation_result`
declares `response_model=BacktestRead` and returns the rich
`BacktestRead`. `/database_result` continues to return `DataBaseResponse`
(`{id: int}`) — but that's now spec-aligned and intentional. The bare-int
leak is gone.

### 21. `/v1/jobs/<bogus>/{...}_result` returns 404

```bash
curl -sS $CHAP/v1/jobs/00000000-0000-0000-0000-000000000000/database_result
curl -sS $CHAP/v1/jobs/00000000-0000-0000-0000-000000000000/prediction_result
# both -> HTTP 404 {"detail":"Job '00000000-...' not found"}
```

The `TaskRevokedError` leak is gone.

### 22. `/v1/jobs/<bogus>/logs` returns 404

```bash
curl -sS $CHAP/v1/jobs/00000000-0000-0000-0000-000000000000/logs
# -> HTTP 404 {"detail":"Job '00000000-...' not found"}
```

### 24. `backtest-overlap` error message names the actual id

```bash
curl -sS $CHAP/v1/analytics/backtest-overlap/8888/9999
# -> HTTP 404 {"detail":"Backtest 8888 not found"}
```

(Was literal "Backtest 1 not found" / "Backtest 2 not found".)

### 25. `/v1/crud/datasets/csvFile` is no longer shadowed

```bash
curl -sS -X POST $CHAP/v1/crud/datasets/csvFile
# -> HTTP 422 {"detail":[{"loc":["body","csv_file"],"msg":"Field required",...}]}
```

The POST now reaches its own handler and reports missing form fields
(instead of being matched by `GET /datasets/{id}` first).

### 26. `NaN` in `/datasets/{id}/df` rendered as `null`

Sample run against a real dataset: 432 rows returned, 70 of them with at
least one `None` value, `HTTP 200`. Pandas-`NaN` -> JSON crash is gone.

### 27. Unknown-id 404 on `/datasets/{id}/df` and `/csv`

Both endpoints now return `HTTP 404 {"detail":"Dataset not found"}`
instead of a leaked `ValueError`.

---

## SUPERSEDED

### 9. DELETE on `configured-models-with-data-source`

The entire `configured-models-with-data-source` resource is removed
(chap-core PR #354). The replacement, `/v1/crud/prediction-setups`,
exposes `DELETE /v1/crud/prediction-setups/{id}` and returns
`HTTP 404 {"detail":"PredictionSetup 9999 not found"}` for unknown ids
(verified). The original concern doesn't apply any more.

---

## OPEN

### 4. `POST /v1/crud/configured-models` mutates the supplied `name`  [trivial]

```bash
curl -sS -X POST $CHAP/v1/crud/configured-models -d '{"name":"my-name","modelTemplateId":1}'
# -> {"name":"chap_ewars_monthly:my-name", ...}
```

The handler prefixes the template name onto whatever the caller
supplied. Caller-visible mismatch between request `name` and response
`name`. Fix at `chap_core/database/database.py:166-172`
(`SessionWrapper.add_configured_model`) — either document the prefix
behaviour or split namespace from supplied name.

### 5. Evaluation `modelId` is unvalidated free-form string  [medium]

```bash
curl -sS -X POST $CHAP/v1/analytics/create-backtest -d '{"name":"p","modelId":"no-such-model","datasetId":1}'
# -> HTTP 200 {"id":"<job-uuid>"}
```

The job is queued; failure surfaces only when the worker tries to
resolve the model and the job fails async. Fix at
`chap_core/rest_api/v1/routers/analytics.py:328-340` (`create_backtest`)
— resolve `request.model_id` against `ConfiguredModelDB.name` and raise
404 synchronously. chap_client's `EvaluationsEndpoints.create_evaluation`
still preflights the `model_id` against `list_configured_models()` to
mitigate this.

### 11. `POST /v1/crud/configured-models` is silently idempotent  [medium]

Posting `{"name":"X","modelTemplateId":1}` twice returns the same id
both times (`HTTP 200`). No 409 on duplicate `(name, modelTemplateId)`.
Fix at `chap_core/database/database.py:174-178` — raise in
`add_configured_model()` when an existing row is found (caller turns it
into a 409).

### 12. List endpoints silently ignore unknown query params  [medium]

```bash
curl -sS "$CHAP/v1/jobs?bogus=42&limit=1"
# -> returns the entire job list (3 rows), HTTP 200
curl -sS "$CHAP/v1/crud/backtests?bogus=42&limit=1"
# -> returns the entire backtest list, HTTP 200
```

Neither `limit` (recognised) nor `bogus` (unknown) is respected.
chap_client and chap-scheduler still rely on this and pull the whole
table; pagination is a known follow-up. Fix at the relevant
list-endpoint handlers in
`chap_core/rest_api/v1/{jobs.py,routers/crud.py}`.

### 14. `POST /v1/analytics/create-backtest` accepts empty `name`  [trivial]

```bash
curl -sS -X POST $CHAP/v1/analytics/create-backtest -d '{"name":"","modelId":"chapkit-ewars-model","datasetId":1}'
# -> HTTP 200 {"id":"<job-uuid>"}
```

Same likely for `create-backtest-with-data` and the new
`POST /v1/crud/prediction-setups/{id}/run` (untested for empty `name`
here; worth a single-line `Field(min_length=1)` sweep across all
mutating request models in `chap_core/rest_api/data_models.py`).

### 16. POST endpoints silently ignore unknown top-level fields  [trivial]

```bash
curl -sS -X POST $CHAP/v1/analytics/create-backtest -d '{"name":"p","modelId":"chapkit-ewars-model","datasetId":1,"nPriods":7}'
# (typo'd "nPriods") -> HTTP 200 {"id":"<job-uuid>"}
```

A mistyped key (`nPriods` for `nPeriods`) is accepted on the wire and
produces a job with the default value. **Notable exception:** the new
`POST /v1/crud/prediction-setups/{id}/run` body sets
`extra="forbid"` and **does** reject unknown fields (verified during
the PR #354 implementation). The remaining `create-backtest`,
`create-backtest-with-data`, `configured-models`, `datasets`, and
similar mutating endpoints still accept unknown fields. chap_client's
request models all set `extra="forbid"` locally to mitigate.

### 17. CORS reflects any Origin with `allow-credentials: true`  [hard]

```bash
curl -sI -H 'Origin: https://evil.example.com' $CHAP/system/info
# access-control-allow-origin: https://evil.example.com
# access-control-allow-credentials: true
# vary: Origin
```

Any browser tab from any origin can make credentialed requests to chap.
Fix at `chap_core/rest_api/app.py:33-45` — replace the wildcard origin
list with an explicit allowlist (or drop `allow_credentials=True`).
Tagged `[hard]` because it crosses deployment + auth + frontend
concerns and may need an env-driven allowlist.

### 18. Most endpoints have no `description` in OpenAPI  [medium]

Re-counted on the live spec: **36 of 66** endpoints have no description
(was reported as 35/65 in the old doc). Mostly the CRUD GET routes on
backtests, predictions, configured-models, model-templates,
prediction-setups. Mechanical sweep on each `@router.get/post/...`
decorator across `chap_core/rest_api/v1/routers/*.py`.

### 23. `/info` and `/full` on backtests have overlapping, confusing shapes  [medium]

```text
/info keys: aggregateMetrics, configuredModel, created, dataset, datasetId, id, modelId, modelTemplateVersion, name, orgUnits, predictionSetupId, splitPeriods (12 keys)
/full keys: aggregateMetrics, created, datasetId, id, modelDbId, modelId, modelTemplateVersion, name, orgUnits, splitPeriods (10 keys)
```

`/info` is now the **richer** payload (it embeds `configuredModel`,
`dataset`, and the new `predictionSetupId`). `/full` is mostly a strict
subset, distinguished only by `modelDbId` (an integer FK rather than
the resolved model). The naming is backwards from operator
expectations. Either rename `/full` to `/raw` or make `/full` a real
superset (eager-load relations + add forecasts/metrics on top of
`BacktestRead`).

---

## How to re-run these probes

The probes are pure curl + python one-liners; the live chap-core in
this repo's `compose.yml` sibling stack listens on `http://localhost:8000`
when up. There's no script — the canonical record is this doc. If a
finding's status flips, re-probe with the curl in its section and edit
the table at the top.

Some probes need real state: backtest #1, dataset #1, prediction-setup
#1, and a SUCCESS job id. The end-to-end smoke in
`docs/local-stack-runbook.md` leaves all of those behind on a fresh
stack and gives you the job id you need.
