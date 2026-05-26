# Endpoint reference

Every chap REST endpoint that `chap_client` models, with a `curl`
example (against direct chap on `http://localhost:8000`) and the
matching Python call. URL paths assume direct chap; reaching the
same endpoint through DHIS2's proxy means prepending
`/api/routes/chap/run` to every path.

Examples assume:

```python
from chap_client import ChapClient
client = ChapClient(base_url="http://localhost:8000")
```

## System

### `GET /system/info`

System metadata for the chap host (chap-core version, server
timezone, etc.). Useful as a connectivity probe.

```bash
curl http://localhost:8000/system/info
```

```python
info = client.system_info()
print(info.chap_core_version, info.python_version)
```

## Datasets

A **dataset** is the historical input that backtests and predictions
run against. Each dataset is tagged with `type` — `"evaluation"` for
backtests, `"prediction"` for forward predictions.

### `GET /v1/crud/datasets`

```bash
curl http://localhost:8000/v1/crud/datasets
```

```python
datasets = client.list_datasets()
eval_sets = [d for d in datasets if d.type == "evaluation"]
```

### `GET /v1/crud/datasets/{id}`

```bash
curl http://localhost:8000/v1/crud/datasets/1
```

```python
dataset = client.get_dataset(1)
print(dataset.first_period, dataset.last_period, len(dataset.org_units))
```

## Models

chap has **three** model-related concepts that are easy to confuse:

| Endpoint | Returns | Used for |
|---|---|---|
| `GET /v1/crud/model-templates` | `ModelTemplateRead[]` | The templates configured-models can be based on. **The valid id space for `modelTemplateId`.** |
| `GET /v1/crud/models` | `ModelSpecRead[]` | A merged read view. Same shape as `configured-models`. |
| `GET /v1/crud/configured-models` | `ModelSpecRead[]` | The configured models registered with chap. |

!!! warning "id-space gotcha"
    The numeric `id` you get from `/v1/crud/models` is **not** a
    valid `modelTemplateId` for `POST /v1/crud/configured-models`.
    Pull templates from `/v1/crud/model-templates` and use **those**
    ids when creating a configured model. See
    [`CHAP_CORE_ISSUES.md`](https://github.com/dhis2-chap/chap-scheduler/blob/main/chap_client/CHAP_CORE_ISSUES.md)
    finding 3.

### `GET /v1/crud/models`

```bash
curl http://localhost:8000/v1/crud/models
```

```python
models = client.list_models()
for m in models:
    print(m.id, m.name, m.target.name)
```

### `GET /v1/crud/configured-models`

Same wire shape as `list_models`; chap returns identical data on a
fresh instance.

```bash
curl http://localhost:8000/v1/crud/configured-models
```

```python
configured = client.list_configured_models()
```

### `POST /v1/crud/configured-models`

Create a configured model. **`modelTemplateId` must come from
`/v1/crud/model-templates`**, not from `/v1/crud/models`.

!!! warning "name rewrite"
    chap silently rewrites the supplied `name` to
    `{template_name}:{your_name}`. Don't rely on the saved name
    matching what you sent.

```bash
curl -X POST http://localhost:8000/v1/crud/configured-models \
  -H 'content-type: application/json' \
  -d '{"name": "my-config", "modelTemplateId": 1}'
```

```python
from chap_client import ChapConfiguredModelCreate

spec = ChapConfiguredModelCreate(name="my-config", modelTemplateId=1)
created = client.create_configured_model(spec)
print(created.id, created.name, created.model_template_id)
```

## Prediction setups

A **prediction setup** is a 1-1 child of a backtest. It bundles the
DHIS2 covariate-source + quantile-target mappings, a snapshot of the
backtest's dataset shape (start period, org units, period type), and
the cron schedule + enabled flag (consumed by chap-scheduler, not
chap-core). This is the resource the chap-scheduler Prefect flow
iterates and the resource that receives prediction-run requests at
`POST .../{id}/run`.

### `GET /v1/crud/prediction-setups`

```bash
curl http://localhost:8000/v1/crud/prediction-setups
```

```python
setups = client.list_prediction_setups()
for s in setups:
    print(s.id, s.name, s.period_type, len(s.org_units))
```

### `GET /v1/crud/prediction-setups/{id}`

```bash
curl http://localhost:8000/v1/crud/prediction-setups/1
```

```python
s = client.get_prediction_setup(1)
```

## Evaluations

An **evaluation** runs a configured model against a historical dataset
and produces aggregate metrics + per-(orgUnit, period, quantile)
evaluation entries. This is how you "run an evaluation" in chap.

!!! note "URL terminology"
    chap's REST URLs use `/v1/crud/backtests` and
    `/v1/analytics/create-backtest`; the chap UI calls the same
    concept "Evaluation". chap_client's public method and schema
    names follow the **UI** terminology; the wire URLs are unchanged.

### `POST /v1/analytics/create-backtest` (UI: "Create Evaluation")

Submits an evaluation as a job. The result lands in
`/v1/crud/backtests` once the job reaches `SUCCESS`.

!!! info "modelId is a string"
    `modelId` is the configured-model **name** (e.g.
    `"chapkit-ewars-model"`), not its integer id. chap's OpenAPI
    correctly types it as `string`, but the naming is easy to
    misread.

```bash
curl -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'content-type: application/json' \
  -d '{"name": "smoke-eval", "modelId": "chapkit-ewars-model", "datasetId": 1}'
```

```python
import time
from chap_client import ChapMakeEvaluationRequest

req = ChapMakeEvaluationRequest(
    name="smoke-eval",
    modelId="chapkit-ewars-model",
    datasetId=1,
)
job = client.create_evaluation(req)

# Poll until the job is terminal.
while True:
    status = client.job_status(job.id)
    if status.upper() not in {"PENDING", "RUNNING", "STARTED", "QUEUED", "PROCESSING"}:
        break
    time.sleep(3)
print(f"job {job.id} -> {status}")
```

### `GET /v1/crud/backtests` (UI: list "Evaluations")

```bash
curl http://localhost:8000/v1/crud/backtests
```

```python
evaluations = client.list_evaluations()
for e in evaluations:
    print(e.id, e.name, e.aggregate_metrics.get("crps"))
```

### `GET /v1/crud/backtests/{id}/info`

The lighter "info" payload — same as a list entry, scoped to one id.
For the heavier `/full` payload (deeper config + sub-models embedded),
fall through to `client.get(...)` directly until a typed wrapper exists.

```bash
curl http://localhost:8000/v1/crud/backtests/1/info
```

```python
ev = client.get_evaluation(1)
print(ev.aggregate_metrics)  # crps, mae, rmse, coverage_*, winkler_score_*, ...
```

### `DELETE /v1/crud/backtests/{id}`

```bash
curl -X DELETE http://localhost:8000/v1/crud/backtests/3
```

```python
client.delete_evaluation(3)
```

### `GET /v1/analytics/evaluation-entry`

Per-row evaluation values for a finished evaluation, optionally
filtered by split-period or org units. The wire query parameter is
still `backtestId`; chap's REST naming hasn't been updated to match
the UI yet.

```bash
curl 'http://localhost:8000/v1/analytics/evaluation-entry?backtestId=1&quantiles=0.1&quantiles=0.5&quantiles=0.9'
```

```python
entries = client.evaluation_entries(
    evaluation_id=1,
    quantiles=[0.1, 0.5, 0.9],
    split_period="202401",   # optional
    org_units=["OU1", "OU2"],  # optional
)
for e in entries:
    print(e.org_unit, e.period, e.quantile, e.value, e.split_period)
```

## Predictions

### `POST /v1/crud/prediction-setups/{id}/run`

Forward prediction using a prediction setup. The setup id rides in
the URL path; the body carries the input observations + geojson +
horizon. The configured model is derived from the setup. Returns a
job id; poll `job_status` and pull results once terminal.

```python
from chap_client import ChapRunPredictionSetupRequest

req = ChapRunPredictionSetupRequest(
    name="my-pred",
    geojson=...,             # FeatureCollection of org-unit polygons
    providedData=[...],      # list[ChapObservation]
    nPeriods=3,
)
job = client.run_prediction_setup(setup_id=1, request=req)
```

### `GET /v1/jobs/{id}` (job status)

```bash
curl http://localhost:8000/v1/jobs/abc-123
```

```python
status = client.job_status("abc-123")
```

### `GET /v1/jobs` (job listing)

Returns every job chap currently has on record. Used internally by
`job_description` to resolve a single job's `result` (the prediction
id) since the per-job endpoint only returns the status string.

```bash
curl http://localhost:8000/v1/jobs
```

```python
# All jobs (typed)
jobs = client.list_jobs()
running = [j for j in jobs if j.status in {"PENDING", "RUNNING", "STARTED"}]

# Resolve a single job (uses /v1/jobs internally)
desc = client.job_description("abc-123")
if desc and desc.result:
    prediction_id = int(desc.result)
```

### `GET /v1/analytics/prediction-entry/{predictionId}`

```bash
curl 'http://localhost:8000/v1/analytics/prediction-entry/42?quantiles=0.5'
```

```python
entries = client.prediction_entries(42, quantiles=[0.1, 0.5, 0.9])
```

## Endpoints we don't model yet

Generated 2026-05-08 from chap-core's `/openapi.json`. Reach for
`client.get(path, params=...)` / `client.post(path, json=...)` /
`client.request(method, path, ...)` until any of these get typed
wrappers; PRs welcome.

### Backtests / evaluations (8 unmodelled)

- `POST   /v1/crud/backtests` — backend-only "create-row" variant; the
  real entry point is `/v1/analytics/create-backtest`.
- `DELETE /v1/crud/backtests` — delete-batch variant of the
  per-id delete we already model.
- `PATCH  /v1/crud/backtests/{id}` — update a stored evaluation
  (e.g. rename).
- `GET    /v1/crud/backtests/{id}/full` — richer payload than `/info`
  (includes the predicted entries inlined).
- `POST   /v1/analytics/create-backtest-with-data/` — submit an
  evaluation with the input data inlined in the request, instead of
  referencing a stored dataset.
- `GET    /v1/analytics/actualCases/{backtestId}` — the ground-truth
  values an evaluation was scored against (useful for plotting actual
  vs predicted).
- `GET    /v1/analytics/backtest-overlap/{id1}/{id2}` — periods two
  evaluations have in common.
- `GET    /v1/analytics/compatible-backtests/{id}` — other
  evaluations comparable to this one (same model + dataset shape).

### Datasets (7 unmodelled)

- `POST   /v1/crud/datasets` — create a dataset from an inline JSON
  body.
- `POST   /v1/crud/datasets/csvFile` — create a dataset from a CSV
  upload.
- `POST   /v1/analytics/make-dataset` — convenience builder.
- `DELETE /v1/crud/datasets/{id}` — delete by id.
- `GET    /v1/crud/datasets/{id}/csv` — fetch the dataset rows as
  CSV.
- `GET    /v1/crud/datasets/{id}/df` — fetch the dataset rows as a
  serialised pandas DataFrame.
- `GET    /v1/analytics/data-sources` — list the data-source kinds
  chap knows about (DHIS2 covariate slots, climate sources, etc.).

### Models (4 unmodelled)

- `GET    /v1/crud/model-templates` — **the canonical id space for
  `modelTemplateId`**. Worth modelling soon -- without it callers have
  to guess valid template ids.
- `GET    /v1/crud/configured-models/{id}` — fetch a single
  configured model. Symmetrical with our list method but missing.
- `DELETE /v1/crud/configured-models/{id}` — delete a configured
  model.
- `POST   /v1/crud/models` — register a new model template.

### Predictions (6 unmodelled)

- `POST   /v1/analytics/make-prediction` — direct submit variant;
  chap-scheduler runs predictions through
  `POST /v1/crud/prediction-setups/{id}/run` instead (the setup-bound
  variant chap-core PR #354 introduced), so this path is unmodelled.
- `GET    /v1/analytics/prediction-entry` — list-all prediction
  entries (no id). The id'd variant is modelled.
- `GET    /v1/crud/predictions` — list all predictions.
- `GET    /v1/crud/predictions/{id}` — fetch a single prediction.
- `POST   /v1/crud/predictions` — backend "create-row" variant of the
  analytics submit.
- `DELETE /v1/crud/predictions/{id}` — delete a stored prediction.

### Jobs (6 unmodelled)

- `DELETE /v1/jobs/{id}` — delete a finished job entry.
- `POST   /v1/jobs/{id}/cancel` — cancel a running job.
- `GET    /v1/jobs/{id}/logs` — stream the job's stdout/stderr.
- `GET    /v1/jobs/{id}/database_result` — typed wrapper around the
  job's resulting DB row.
- `GET    /v1/jobs/{id}/evaluation_result` — full evaluation payload
  (alternative to `evaluation_entries` once the evaluation finishes).
- `GET    /v1/jobs/{id}/prediction_result` — full prediction payload
  (alternative to `prediction_entries`).

### Visualizations (7 unmodelled)

chap-rendered plots returned as image bytes. Probably want to expose
these as raw `bytes` rather than parsed payloads.

- `GET    /v1/visualization/backtest-plots/` and
  `/{visualization_name}/{backtest_id}`
- `GET    /v1/visualization/dataset-plots/` and
  `/{visualization_name}/{dataset_id}`
- `GET    /v1/visualization/metric-plots/{backtest_id}` and
  `/{visualization_name}/{backtest_id}/{metric_id}`
- `GET    /v1/visualization/metrics/{backtest_id}`

### Services (5 unmodelled, v2)

A separate service registry under `/v2/services` for chap to discover
external model runners. Not relevant to the chap-scheduler use case.

- `GET    /v2/services`, `GET /v2/services/{id}`
- `POST   /v2/services/$register`, `DELETE /v2/services/{id}`
- `PUT    /v2/services/{id}/$ping`

### Other (3 unmodelled)

- `GET    /health` — chap's plain liveness probe (we use
  `system_info` instead).
- `POST   /v1/crud/debug` and `GET /v1/crud/debug/{id}` — chap's
  debug-entry storage.
- `GET    /v1/crud/metric/csv` — metrics across evaluations as CSV.
