# chap-scheduler

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

FastAPI service that embeds [Prefect](https://www.prefect.io/) for orchestrating
CHAP scheduling workflows. Packaged for Docker.

The full Prefect server (API + UI + scheduler / triggers / task-run-recorder)
is mounted **inside** this FastAPI app — there is no separate Prefect process.
By default it lives at `/prefect` so the UI is at <http://localhost:9090/prefect/>
and the API at <http://localhost:9090/prefect/api>.

> Status: scaffolding only. The plumbing is in place; flow definitions are
> intentionally left empty.

## Layout

```
chap-scheduler/
├── src/chap_scheduler/         # Python package (src layout)
│   ├── api/                    # FastAPI app + routes (Prefect mounted at /prefect)
│   ├── blocks/                 # Prefect blocks (DHIS2 credentials)
│   ├── cli/                    # Typer CLI (entry point: chap-scheduler)
│   ├── flows/                  # Prefect flows
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
