# chap-client CLI

`chap-client` ships a [Typer](https://typer.tiangolo.com/) CLI that
mirrors the `ChapClient` Python API one command per method. Use it to
poke at chap-core from a shell, script ad-hoc evaluations, or sanity-
check that your DHIS2 chap-route proxy is wired up — without writing
Python.

The entry point is registered as `chap-client` by `pyproject.toml`'s
`[project.scripts]`:

```bash
chap-client --help
```

## Configuration

Every command needs to know **where chap is** and (optionally) how to
authenticate. Both come from the top-level options or matching env
vars:

| Option           | Env var                     | Purpose                                                                           |
| ---------------- | --------------------------- | --------------------------------------------------------------------------------- |
| `--base-url`     | `CHAP_CLIENT_BASE_URL`      | Origin of the chap-serving host. Required.                                        |
| `--user`         | `CHAP_CLIENT_USER`          | Basic-auth username; omit if chap is unauthenticated.                             |
| `--password`     | `CHAP_CLIENT_PASSWORD`      | Basic-auth password.                                                              |
| `--route-prefix` | `CHAP_CLIENT_ROUTE_PREFIX`  | Path prefix; set to `/api/routes/chap/run` when reaching chap via DHIS2's proxy.  |
| `--max-attempts` | `CHAP_CLIENT_MAX_ATTEMPTS`  | Total attempts for retryable (GET / HEAD) requests. Default `3`.                  |

Two common shapes:

```bash
# 1. Direct against chap on its own port
chap-client --base-url http://localhost:8000 info

# 2. Via DHIS2's chap-route proxy
chap-client \
  --base-url https://dhis.example.org \
  --user admin --password district \
  --route-prefix /api/routes/chap/run \
  info
```

The env-var form is friendlier for repeat use:

```bash
export CHAP_CLIENT_BASE_URL=http://localhost:8000
chap-client info
chap-client datasets list
chap-client evaluations list
```

Output is always pretty-printed JSON on stdout; errors land on stderr
with a non-zero exit code. Pipe through `jq` for filtering:

```bash
chap-client datasets list | jq '.[] | {id, name, type}'
```

When stdout is a terminal, output is rendered with
[rich](https://github.com/Textualize/rich) (ships with Typer):

- **List commands** (`datasets list`, `models list`,
  `models list-configured`, `prediction-setups list`,
  `evaluations list`, `jobs list`) render as **multi-row tables**
  with the most useful columns picked per resource (id, name, key
  metadata, summary metrics for evaluations, colour-coded status
  for jobs, etc.).
- **Single-resource commands** (`info`, `datasets get`,
  `prediction-setups get`, `evaluations get`,
  `models create-configured`) render as **two-column key/value
  tables**. Short lists / dicts of scalars render inline; deeper
  nested values are summarised as ``<N items>`` / ``<N fields>`` —
  pipe through `| cat` to see the full JSON shape.
- **Entry-level commands** (`evaluations entries`, `predictions
  entries`) and `jobs description` render as **syntax-highlighted
  JSON** — these payloads are tabular but high-cardinality, so the
  raw rows are easier to consume.
- `jobs status` colour-codes the bare status string (green for
  `SUCCESS`, red for `FAILED` / `ERROR`, yellow for transient states
  like `RUNNING` / `PENDING`).

When stdout is piped or redirected the output drops back to **plain
JSON** exactly as before for *every* command — including the list
commands. So `| jq`, file redirects, and CI consumers see no
behaviour change. Set `NO_COLOR=1` to render in a TTY without colour
(tables still draw, just monochrome); pipe through `| cat` to force
the plain-JSON path.

## Command tree

```text
chap-client
├── info                          system info (chap-core version, server time)
├── datasets
│   ├── list                      all datasets
│   └── get   ID                  one dataset
├── models
│   ├── list                      model registry
│   ├── list-configured           configured models
│   └── create-configured         POST a configured model
├── prediction-setups             prediction setups (the deployable form;
│   │                              create via chap-frontend or a direct
│   │                              POST /v1/crud/prediction-setups)
│   ├── list
│   └── get   ID
├── evaluations                   chap UI: 'Evaluations' / wire URL: /v1/crud/backtests
│   ├── list
│   ├── get      ID
│   ├── delete   ID
│   ├── create   --name ... --model-id ... --dataset-id ...
│   └── entries  ID  -q 0.1 -q 0.5 [--split-period ...] [--org-unit ...]
├── jobs
│   ├── list                      every chap job + status + result + timing
│   ├── status        JOB_ID      bare status string ('SUCCESS' / 'PENDING' / ...)
│   └── description   JOB_ID      full description JSON, or 'null'
└── predictions
    └── entries   PREDICTION_ID  -q 0.5 [-q ...]
```


## Walkthroughs

### Run an evaluation, then pull entries

The five-step recipe from
[the overview](index.md#typical-workflow), reachable from a shell:

```bash
export CHAP_CLIENT_BASE_URL=http://localhost:8000

# 1. find an evaluation-typed dataset
chap-client datasets list | jq '[.[] | select(.type=="evaluation")] | .[0]'

# 2. submit the evaluation
chap-client evaluations create \
  --name 'cli-smoke' \
  --model-id chapkit-ewars-model \
  --dataset-id 1
# {"id": "abc-123-..."}

# 3. poll until terminal
chap-client jobs status abc-123-...
# PENDING
# (a minute later)
chap-client jobs status abc-123-...
# SUCCESS

# 4. find the resulting evaluation row + aggregate metrics
chap-client evaluations list | jq '.[-1]'

# 5. pull per-row predicted values for the median
chap-client evaluations entries 5 -q 0.5 | jq '.[0]'
```

### Submit + fetch a forward prediction via Prefect's preferred path

```bash
# Inspect the prediction setups chap-core has (create via the
# chap-frontend or a direct POST /v1/crud/prediction-setups with a
# `backtestId`; chap-client doesn't expose a create command today).
chap-client prediction-setups list
chap-client prediction-setups get 1

# (chap-scheduler's Prefect flow then runs the prediction; once it's
# stored, fetch entries.)
chap-client predictions entries 17 -q 0.5
```

### Use against a DHIS2-proxied chap with persistent env

```bash
cat > .envrc <<'EOF'
export CHAP_CLIENT_BASE_URL=https://dhis.example.org
export CHAP_CLIENT_USER=admin
export CHAP_CLIENT_PASSWORD=district
export CHAP_CLIENT_ROUTE_PREFIX=/api/routes/chap/run
EOF
direnv allow

chap-client info
```

## Limitations

- The CLI is a thin shell over `ChapClient`; whatever the Python API
  doesn't model isn't reachable from the CLI either. Today that's the
  visualization, v2-services, and debug/metrics endpoint groups (see
  [Endpoints](endpoints.md) for the unmodelled list).
- Output is JSON-only. There's no `--output table` mode yet — pipe
  through `jq` if you want filtering or shaping.
- `--quantile`/`-q` is required for `evaluations entries` and
  `predictions entries` — chap returns a different row per quantile,
  so there's no sensible default.
- POST commands (`evaluations create`, `models create-configured`)
  are **not** retried on transport errors. See the retry semantics
  in [the overview](index.md#behaviour-you-should-know).
