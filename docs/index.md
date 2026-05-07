# chap-scheduler

FastAPI service embedding [Prefect](https://www.prefect.io/) for CHAP workflow
orchestration.

## Status

Scaffolding stage. The runtime (FastAPI app, Typer CLI, Prefect server in
Docker compose, lint/type/docs tooling) is wired up — flow definitions are
intentionally empty so we can iterate on plumbing first.

## Run locally

```bash
make install
make serve
```

The API is then on <http://127.0.0.1:9090>:

- `GET /health` — liveness probe
- `GET /info`   — service metadata
- `GET /docs`   — Swagger UI

The Prefect server runs **inside** this FastAPI app — same process, same
port. By default it is mounted at `/prefect`, so:

- Prefect UI  → <http://127.0.0.1:9090/prefect/>
- Prefect API → <http://127.0.0.1:9090/prefect/api>

## Run the full stack

```bash
cp .env.example .env
make run
```

| Service        | URL                                      |
| -------------- | ---------------------------------------- |
| chap-scheduler | <http://localhost:9090>                  |
| Prefect UI     | <http://localhost:9090/prefect/>         |
| Prefect API    | <http://localhost:9090/prefect/api>      |
| Postgres       | localhost:5432 (internal only)           |

## CLI

```bash
chap-scheduler --version
chap-scheduler info
chap-scheduler serve
```

Settings come from environment / `.env`, all prefixed with `CHAP_SCHEDULER_`.
See [`.env.example`](https://github.com/dhis2-chap/chap-scheduler/blob/main/.env.example).
