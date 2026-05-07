# chap-scheduler

[![CI](https://github.com/dhis2-chap/chap-scheduler/actions/workflows/ci.yml/badge.svg)](https://github.com/dhis2-chap/chap-scheduler/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-mkdocs--material-blue.svg)](https://dhis2-chap.github.io/chap-scheduler/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Scheduled CHAP predictions for DHIS2 implementations. Drives
[chap](https://github.com/dhis2-chap/chap-core) disease-forecast runs against
a DHIS2 instance on a schedule, using
[Prefect](https://www.prefect.io/) for orchestration. Packaged for Docker.

> **Status: prototype only.** APIs, behaviour, dependencies, data shapes,
> and operational conventions will change without notice. Do not rely on
> it for operational, clinical, or otherwise critical workloads.

> **Network exposure.** The embedded Prefect UI is unauthenticated and has
> powerful control-plane endpoints (it can trigger flows that use saved DHIS2
> credentials). `compose.yml` binds the host port to `127.0.0.1` only by
> default. If you need to expose the service beyond localhost, put a reverse
> proxy with auth in front of it.

## Documentation

The full docs live at **<https://dhis2-chap.github.io/chap-scheduler/>**:

- [Prefect primer](https://dhis2-chap.github.io/chap-scheduler/prefect/) —
  flows, blocks, deployments, work pools.
- [Architecture](https://dhis2-chap.github.io/chap-scheduler/architecture/) —
  load-bearing decisions and the compose-stack diagram.
- [Operations](https://dhis2-chap.github.io/chap-scheduler/operations/) —
  triggering runs, scheduling via the Prefect UI, reading the run-report,
  rotating credentials, troubleshooting.

## Quick start

Install dependencies (creates `.venv` via [`uv`](https://docs.astral.sh/uv/)):

```bash
make install
```

Run the API locally with auto-reload (uses SQLite for Prefect's DB):

```bash
uv run chap-scheduler serve --reload
# chap-scheduler   → http://127.0.0.1:9090/health
# Swagger UI       → http://127.0.0.1:9090/docs
# Prefect UI       → http://127.0.0.1:9090/prefect/
```

Or bring up the full stack (Postgres + chap-scheduler + worker):

```bash
cp .env.example .env
make run
# chap-scheduler   → http://localhost:9090
# Prefect UI       → http://localhost:9090/prefect/
```

To skip the local build and use prebuilt images from GHCR (auto-published
on every push to `main`):

```bash
docker compose -f compose.ghcr.yml up -d
```

Before you can trigger a run, you need to create a `Dhis2Credentials` block
instance for your DHIS2 server — see
[Operations: before triggering a run](https://dhis2-chap.github.io/chap-scheduler/operations/#before-triggering-a-run).

## Development

```bash
make lint                 # ruff format + ruff check --fix + mypy + pyright
make check                # read-only equivalent (CI uses this)
make test                 # pytest
uv run mkdocs serve       # docs preview (http://127.0.0.1:8000)
```

Tracking issue: [CLIM-638](https://dhis2.atlassian.net/browse/CLIM-638).
Deferred / known-improvement list: [`ROADMAP.md`](./ROADMAP.md).
