# chap-client

Python HTTP client for the chap REST API.

The package is a sibling of `chap-scheduler` in this repo, wired in
via a uv path-dep. It will eventually move to its own repo and
publish independently — until then, treat the API as **experimental**:
shapes can shift between merges.

## What is chap-core?

[chap-core](https://github.com/dhis2-chap/chap-core) is the
disease-forecasting ML platform behind the
[CHAP](https://dhis2.org/chap) project (Climate Health Analytics
Platform). It runs forecasting models — Bayesian INLA, ARIMA, neural
nets, custom Python models written with
[chapkit](https://github.com/dhis2-chap/chapkit) — against historical
disease and climate data, producing probabilistic predictions per
org-unit and time period. It's typically reached through DHIS2 as a
custom route (`/api/routes/chap/run/*`) but can also be hit directly
on its own port.

chap-core's own architecture in one paragraph: a FastAPI server that
stores models, datasets, and runs in Postgres; a job runner that
executes training + prediction asynchronously; integration with
chapkit (HISP's model-packaging library) so externally-authored
models slot in without forking chap-core. Most chap-core endpoints
return immediately with a job id; you poll until the job is terminal
and then fetch the result.

## Mental model: how the resources fit together

chap's domain has six main resource shapes you'll deal with through
this client. The relationship is a chain:

```text
   model template          (a packaged forecasting algorithm; e.g.
        |                   "chapkit-ewars-model" v1.0.0. lives at
        v                   /v1/crud/model-templates)
   configured model        (a model template + chosen
        |                   hyperparameters / option values)
        v
   configured model        (the configured model + DHIS2 data-source
   with data source         mappings; the "deployable" form chap
        |                   uses for predictions)
        |
        +-->  evaluation   (run the configured model against a
        |                   historical *dataset* to score its
        |                   predictions vs ground truth; UI:
        |                   "Evaluation"; URL: /v1/crud/backtests)
        |
        +-->  prediction   (run the configured model forward in time;
                            URL: /v1/crud/predictions; results land
                            at /v1/analytics/prediction-entry/{id})
```

A few naming gotchas worth flagging up front:

- The chap UI calls them **"Evaluations"**; the REST URLs say
  `/v1/crud/backtests` and `/v1/analytics/create-backtest`. Same
  thing. `chap_client` follows the UI naming
  (`client.create_evaluation(...)`); the wire URLs are unchanged.
- `/v1/crud/models` and `/v1/crud/configured-models` currently return
  the **same** payload — chap hasn't separated the registry view from
  the configured view yet. `/v1/crud/model-templates` is a third,
  smaller, **distinct** endpoint and is the one that gives you the
  ids accepted as `modelTemplateId`.
- A configured-model-with-data-source is **not** automatically
  derived from a configured-model. You either build it explicitly or
  use `create_configured_model_with_data_source_from_backtest(...)`
  which materialises it from an existing evaluation.

See [Endpoints](endpoints.md) for the curl/Python examples and
[CHAP_SPEC_DRIFT.md](https://github.com/dhis2-chap/chap-scheduler/blob/main/chap_client/CHAP_SPEC_DRIFT.md)
for cases where chap's actual behaviour differs from its OpenAPI
spec.

## Typical workflow

What you do with chap-core through this client, in order:

1. **List datasets** — find or create the historical input
   (`client.list_datasets()`). Datasets carry a `type` field
   (`"evaluation"` for backtests, `"prediction"` for forward
   predictions); pick one whose period range covers what you need.
2. **List models** — see what algorithms are available
   (`client.list_models()`).
3. **Create a configured model** — bind a model template to specific
   options (`client.create_configured_model(spec)`).
4. **Run an evaluation** — score the configured model against
   historical data (`client.create_evaluation(req)`); chap returns a
   job id. Poll `client.job_status(id)` until terminal, then fetch
   `client.get_evaluation(eval_id)` for `aggregate_metrics` (CRPS,
   MAE, RMSE, coverage, …) or
   `client.evaluation_entries(eval_id, quantiles=[…])` for per-row
   predictions.
5. **Materialise a configured-model-with-data-source** — the
   "deployable" form (`create_configured_model_with_data_source_from_backtest`).
6. **Run forward predictions** — `client.submit_prediction(req)`,
   poll the job, and fetch `client.prediction_entries(...)`.

The chap-scheduler Prefect flow in this repo automates step 6 against
all configured-models-with-data-source on a schedule.

## Coverage of the chap REST API

`chap_client` models the endpoints chap-scheduler actively uses plus
the eval-flow CRUD. Today (2026-05-08) that's roughly **18 of chap's
~65 documented endpoints (28%)**:

| Tag             | Modelled | Total |
|-----------------|---------:|------:|
| System          |  1       |  2    |
| Datasets        |  2       |  9    |
| Models          |  6       |  10   |
| Backtests / evaluations | 5  |  13   |
| Predictions     |  2       |  8    |
| Jobs            |  2       |  8    |
| Visualizations  |  0       |  7    |
| Services (v2)   |  0       |  5    |
| Debug / Metrics |  0       |  3    |

The big unmodelled blocks are visualisations (chap-rendered plots
returned as PNG/SVG bytes) and the v2 services registry. See
[Endpoints](endpoints.md) for the full list of what's modelled and
the explicit list of what isn't.

## Install (today)

`chap-client` is not yet on PyPI. From a checkout of this repo:

```bash
uv sync  # installs chap-client editable from chap_client/
```

Or as a path-dep in your own project:

```toml
dependencies = ["chap-client"]

[tool.uv.sources]
chap-client = { path = "../chap-scheduler/chap_client", editable = true }
```

## Two construction shapes

`ChapClient` is constructed with primitives so it has no opinion
about where credentials come from:

```python
ChapClient(base_url, auth, *, route_prefix="", ...)
```

### Direct against chap

If you have chap reachable on its own port (e.g. `localhost:8000`):

```python
from chap_client import ChapClient

with ChapClient(base_url="http://localhost:8000") as client:
    info = client.system_info()
    print(info.chap_core_version)
```

Pass `auth=httpx.Auth(...)` or `auth=("user", "pw")` if your chap
deployment requires authentication.

### Via DHIS2's chap-route proxy

When chap is exposed through DHIS2 (the chap-scheduler default), use
`route_prefix="/api/routes/chap/run"` and DHIS2 basic auth:

```python
from chap_client import ChapClient

with ChapClient(
    base_url="https://dhis.example.org",
    auth=("admin", "district"),
    route_prefix="/api/routes/chap/run",
) as client:
    info = client.system_info()
```

Inside chap-scheduler, the `Dhis2Credentials.chap_client()` method
wires that shape automatically:

```python
from chap_scheduler.blocks.dhis2 import Dhis2Credentials

creds = Dhis2Credentials.load("local-dhis2")
with creds.chap_client() as client:
    info = client.system_info()
```

## Pages in this section

- **[Endpoints](endpoints.md)** — every chap REST endpoint we model,
  with curl + Python examples. Read this first if you're trying to
  figure out what chap can do or why it returned what it returned.
- **[CLI](cli.md)** — `chap-client` shell entry point that mirrors the
  Python API. Useful for quick pokes against chap from a terminal.
- **[API reference](api.md)** — auto-generated reference for the
  `ChapClient` class, request / response models, and exceptions.

## Behaviour you should know

- **Connection pooling.** A single `httpx.Client` is held for the
  lifetime of each `ChapClient` instance. Use it as a context manager
  so the pool is closed cleanly.
- **Retries.** GET / HEAD retry on transient transport errors and 5xx
  responses with exponential backoff + jitter, default 3 attempts.
  POST is **never** retried — chap's mutating endpoints aren't
  idempotent and a retry on a connection blip would risk a duplicate.
  Disable retries for tests with `max_attempts=1`.
- **Errors.** Every non-2xx response raises `ChapHttpError`, carrying
  `method`, `path`, `status`, and the parsed (or raw) response body
  as `detail`.

## chap-core spec drift

While integrating chap_client we've found a handful of cases where
chap-core's actual behaviour differs from its OpenAPI spec, or is
non-obvious. Running notes:
[`chap_client/CHAP_SPEC_DRIFT.md`](https://github.com/dhis2-chap/chap-scheduler/blob/main/chap_client/CHAP_SPEC_DRIFT.md)
in the repo. File a chap-core ticket if any of those is news to you.
