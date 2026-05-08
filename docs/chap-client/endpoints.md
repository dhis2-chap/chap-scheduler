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
    [`CHAP_SPEC_DRIFT.md`](https://github.com/dhis2-chap/chap-scheduler/blob/main/chap_client/CHAP_SPEC_DRIFT.md)
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

## Configured models with data source

A **configured-model-with-data-source** bundles a configured model
with the data sources used for predictions. This is the shape the
chap-scheduler Prefect flow consumes.

### `GET /v1/crud/configured-models-with-data-source`

```bash
curl http://localhost:8000/v1/crud/configured-models-with-data-source
```

```python
items = client.configured_models()  # (legacy method name — list)
for m in items:
    print(m.id, m.name, m.period_type, len(m.org_units))
```

### `GET /v1/crud/configured-models-with-data-source/{id}`

```bash
curl http://localhost:8000/v1/crud/configured-models-with-data-source/1
```

```python
m = client.configured_model_with_data_source(1)
```

### `POST /v1/crud/configured-models-with-data-source/from-backtest/{backtestId}`

Materialises a configured-model-with-data-source row from an existing
backtest — chap derives the body from the backtest's configured model
+ dataset.

```bash
curl -X POST \
  http://localhost:8000/v1/crud/configured-models-with-data-source/from-backtest/2
```

```python
created = client.create_configured_model_with_data_source_from_backtest(2)
print(created.id, created.name)
```

## Backtests / evaluations

A **backtest** runs a configured model against a historical dataset
and produces aggregate metrics + per-(orgUnit, period, quantile)
evaluation entries. This is how you "run an evaluation" in chap.

### `POST /v1/analytics/create-backtest`

Submits a backtest as a job. The result lands in `/v1/crud/backtests`
once the job reaches `SUCCESS`.

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
from chap_client import ChapMakeBacktestRequest

req = ChapMakeBacktestRequest(
    name="smoke-eval",
    modelId="chapkit-ewars-model",
    datasetId=1,
)
job = client.create_backtest(req)

# Poll until the job is terminal.
while True:
    status = client.job_status(job.id)
    if status.upper() not in {"PENDING", "RUNNING", "STARTED", "QUEUED", "PROCESSING"}:
        break
    time.sleep(3)
print(f"job {job.id} -> {status}")
```

### `GET /v1/crud/backtests`

```bash
curl http://localhost:8000/v1/crud/backtests
```

```python
backtests = client.list_backtests()
for b in backtests:
    print(b.id, b.name, b.aggregate_metrics.get("crps"))
```

### `GET /v1/crud/backtests/{id}/info`

The lighter "info" payload — same as a list entry, scoped to one id.
For the heavier `/full` payload (deeper config + sub-models embedded),
fall through to `client.get(...)` directly until a typed wrapper exists.

```bash
curl http://localhost:8000/v1/crud/backtests/1/info
```

```python
bt = client.get_backtest(1)
print(bt.aggregate_metrics)  # crps, mae, rmse, coverage_*, winkler_score_*, ...
```

### `DELETE /v1/crud/backtests/{id}`

```bash
curl -X DELETE http://localhost:8000/v1/crud/backtests/3
```

```python
client.delete_backtest(3)
```

### `GET /v1/analytics/evaluation-entry`

Per-row evaluation values for a finished backtest, optionally filtered
by split-period or org units.

```bash
curl 'http://localhost:8000/v1/analytics/evaluation-entry?backtestId=1&quantiles=0.1&quantiles=0.5&quantiles=0.9'
```

```python
entries = client.evaluation_entries(
    backtest_id=1,
    quantiles=[0.1, 0.5, 0.9],
    split_period="202401",   # optional
    org_units=["OU1", "OU2"],  # optional
)
for e in entries:
    print(e.org_unit, e.period, e.quantile, e.value, e.split_period)
```

## Predictions

### `POST /v1/analytics/make-prediction-with-data-source`

Forward prediction using a configured-model-with-data-source row.
Returns a job id; poll `job_status` and pull results once terminal.

```python
from chap_client import ChapMakePredictionRequest

req = ChapMakePredictionRequest(
    name="my-pred",
    geojson=...,             # FeatureCollection of org-unit polygons
    providedData=[...],      # list[ChapObservation]
    dataSources=[...],
    configuredModelWithDataSourceId=1,
    nPeriods=3,
)
job = client.submit_prediction(req)
```

### `GET /v1/jobs/{id}` (job status)

```bash
curl http://localhost:8000/v1/jobs/abc-123
```

```python
status = client.job_status("abc-123")
```

### `GET /v1/jobs` (job listing)

Used internally to resolve a job's `result` (the prediction id) since
the per-job endpoint only returns the status string.

```python
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

The following exist in chap but aren't typed by chap_client today.
Reach for `client.get(path, params=...)` / `client.post(path, json=...)`
until they get typed wrappers; or PR one in.

- `GET /v1/crud/model-templates` — list available templates (the
  source of valid `modelTemplateId` values).
- `GET /v1/analytics/data-sources` — registered data-source kinds.
- `POST /v1/analytics/make-dataset` — create a dataset for use in
  backtests.
- `POST /v1/analytics/create-backtest-with-data/` — backtest with
  inlined data instead of a saved dataset.
- `GET /v1/crud/backtests/{id}/full` — the richer backtest payload.
- `GET /v1/analytics/actualCases/{backtestId}` — actual cases vs the
  evaluation predictions (for plotting).
- `GET /v1/visualization/*` — chap-rendered plots.
- `GET /v2/services/*` — service registry.
- `GET /v1/crud/metric/csv` — metrics CSV export.
- All the `/v1/crud/predictions/*` CRUD beyond what's used today.
