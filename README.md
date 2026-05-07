# chap-scheduler

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

> **Status: prototype only.** This repository is exploratory and is **not
> intended for production use**. APIs, behaviour, dependencies, data shapes,
> and operational conventions will change without notice. Do not rely on it
> for operational, clinical, or otherwise critical workloads.

Tracking issue: [CLIM-638](https://dhis2.atlassian.net/browse/CLIM-638).

FastAPI service that drives [chap](https://github.com/dhis2-chap/chap-core)
predictions against a DHIS2 instance on a schedule, using
[Prefect](https://www.prefect.io/) for orchestration. Packaged for Docker.

## Architecture

A few load-bearing decisions worth knowing up front:

- **Prefect runs in-process.** The full Prefect server (API + UI + scheduler
  / triggers / task-run-recorder) is mounted inside the FastAPI app at
  `/prefect` — no separate Prefect container. A small ASGI dispatcher
  (`PrefectMountMiddleware`) handles the prefix: `/prefect/api/*` is stripped
  before it reaches Prefect's API sub-app, `/prefect/*` UI traffic preserves
  the prefix so the SPA's asset URLs resolve.
- **Worker is a sibling container.** Flow code lives in
  `src/chap_scheduler/flows/`; a separate `dhis2-chap-prediction` container
  runs `flow.serve()` and talks to the chap-scheduler API over HTTP. Block
  type registration happens from the worker, so the API container never
  triggers Prefect's ephemeral mode (which would spawn a second in-process
  Prefect server).
- **DHIS2 vs chap traffic is split.** DHIS2 native endpoints (analytics,
  organisationUnits, system info) use `dhis2-client`. The chap routes
  (`/api/routes/chap/run/*`) go through a thin `ChapClient` we own, on top
  of plain `httpx`, so chap error bodies surface verbatim (dhis2-client
  rolls them up to `UNKNOWN`).
- **Blocks supply credentials.** A `Dhis2Credentials` Prefect block holds
  base URL + auth. The worker auto-registers the block type; operators
  create one block instance per DHIS2 server they want to talk to and pick
  it from the dropdown when triggering a run.
- **Per-run UI is minimal.** Two parameters in the Prefect quick-run dialog:
  the credentials block and an optional `end_date`. Forecast horizon,
  dataset type, and polling timeout are derived per configured model or
  set via `pydantic-settings` (env-driven, not flow-parameter-driven).
- **End period auto-picks the freshest "all covariates have data" point.**
  Production DHIS2 instances often have lagging climate covariates. Before
  each prediction the flow probes the analytics API, takes the min of the
  latest reported period across all covariates, and uses that as the
  cut-off. An explicit `end_date` overrides.
- **Every run emits a markdown run-report artifact.** Captures DHIS2 +
  chap-core versions, per-model outcomes (succeeded / failed at which step),
  prediction ids, and grouped chap rejection details. Always written, even
  when DHIS2 or chap was unreachable.

## Layout

```
chap-scheduler/
├── src/chap_scheduler/         # Python package (src layout)
│   ├── api/                    # FastAPI app + routes (Prefect mounted at /prefect)
│   ├── blocks/                 # Prefect blocks (DHIS2 credentials)
│   ├── chap/                   # Pydantic models, ChapClient (httpx), run report
│   ├── cli/                    # Typer CLI (entry point: chap-scheduler)
│   ├── flows/                  # Prefect flow + tasks
│   └── config.py               # pydantic-settings configuration
├── tests/
├── docs/                       # mkdocs-material site
├── compose.yml                 # Postgres + chap-scheduler + dhis2-chap-prediction worker
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## Quick start

Install dependencies (creates `.venv` via [`uv`](https://docs.astral.sh/uv/)):

```bash
make install
```

Run the API locally with auto-reload (defaults to SQLite for Prefect's DB):

```bash
uv run chap-scheduler serve --reload
# chap-scheduler   → http://127.0.0.1:9090/health
# Swagger UI       → http://127.0.0.1:9090/docs
# Prefect UI       → http://127.0.0.1:9090/prefect/
# Prefect API      → http://127.0.0.1:9090/prefect/api
```

Bring up the full stack (Postgres + chap-scheduler with embedded Prefect):

```bash
cp .env.example .env
make run
# chap-scheduler   → http://localhost:9090
# Prefect UI       → http://localhost:9090/prefect/
```

## CLI

The package exposes a `chap-scheduler` Typer command:

```bash
chap-scheduler --version
chap-scheduler info     # show resolved settings
chap-scheduler serve    # run the FastAPI server (uvicorn)
```

All settings can be overridden via env vars prefixed with `CHAP_SCHEDULER_`
(see `.env.example`).

## DHIS2 credentials block

A Prefect Block (`chap_scheduler.blocks.dhis2.Dhis2Credentials`) stores DHIS2
connection details and hands back an authenticated client from
[`dhis2-client`](https://github.com/dhis2/dhis2-python-client).

The block **type** is auto-registered on app startup. Create one **instance**
per DHIS2 server you want to talk to — either via the UI at
<http://localhost:9090/prefect/blocks/catalog> or programmatically:

```python
from pydantic import SecretStr
from chap_scheduler.blocks.dhis2 import Dhis2Credentials

Dhis2Credentials(
    base_url="https://dhis.example.org",
    username="api-user",
    password=SecretStr("..."),
).save("my-dhis2-instance")
```

## Flow: `dhis2-chap-prediction`

A Prefect deployment that pulls an analytics series from DHIS2 and emits a
naive next-period prediction. **No schedule** — runs are triggered manually
from the Prefect UI or API.

`make run` brings up a worker container (`dhis2-chap-prediction`) that
calls `flow.serve(name="dhis2-chap-prediction")`, which registers the
deployment and polls for runs.

The flow's `credentials` parameter is typed as `Dhis2Credentials` and is
**required** — the Prefect UI shows a dropdown of saved block instances when
triggering a run, and the user picks which DHIS2 server to query. Trigger
runs from <http://127.0.0.1:9090/prefect/deployments>.

## Development

```bash
make lint   # ruff format + ruff check --fix + mypy + pyright
make test   # pytest
```

Other one-liners (no Make wrapper needed):

```bash
uv sync                  # install deps
uv run mkdocs serve      # docs preview
docker compose logs -f   # tail container logs
```
