# Local stack runbook

Every command used to bring up + drive the chap-scheduler stack locally,
in roughly the order you'd run them. Reproduces the full DHIS2 + chap-core
+ chap-scheduler + Prefect roundtrip end-to-end.

## 1. Bring up the three stacks

```bash
# chap-scheduler stack (this repo). Builds the image + starts the
# embedded Prefect server on :9090.
docker compose -f compose.yml up -d --build

# chap-core stack (sibling repo). Starts FastAPI on :8000 plus its
# postgres + redis + worker containers.
( cd /Users/morteoh/dev/chap-sdk/chap-core && docker compose up -d )

# DHIS2 dev stack (typically already running). DHIS2 lands on :8080.
# admin/district credentials are the dev defaults.
```

## 2. Verify all three services are reachable

```bash
curl -s -o /dev/null -w "chap=%{http_code}\n"      http://localhost:8000/system/info
curl -s -o /dev/null -w "dhis=%{http_code}\n" -u admin:district http://localhost:8080/api/me
curl -s -o /dev/null -w "scheduler=%{http_code}\n" http://localhost:9090/prefect/api/health
```

All three should print `200`.

## 3. Connect DHIS2 to chap-core's docker network

DHIS2's container needs to be able to resolve the host that the chap
DHIS2-route proxies to. The route URL we use in this repo is
`http://host.docker.internal:8000/**` -- which only works if DHIS2's
container can reach the host loopback.

```bash
# Inspect what's where (run once, optional).
docker network ls
docker inspect chap  --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}: {{$v.Aliases}}{{"\n"}}{{end}}'
docker inspect dhis2 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}: {{$v.Aliases}}{{"\n"}}{{end}}'
```

If `host.docker.internal` doesn't resolve from inside the DHIS2
container (Linux hosts especially), connect DHIS2 to chap-core's
network and use the chap container's alias instead:

```bash
docker network connect chap-core_default dhis2
# Then in step 4 set the route URL to http://chap:8000/** instead.
```

## 4. Patch the chap DHIS2-route (URL + timeout)

Two settings on the chap route need bumping for chap-scheduler:

1. **URL** -- the chap route in DHIS2 ships with
   `url: http://chap-core:8000/**`, which only resolves on a docker
   setup where DHIS2 lives on chap-core's network with that hostname.
   For the host-loopback setup chap-scheduler prefers, patch it to
   `http://host.docker.internal:8000/**`.
2. **`responseTimeoutSeconds`** -- DHIS2 defaults this to 5 seconds.
   chap evaluation / prediction submissions are sync POSTs that can
   take 30+ seconds when the worker is warming up; the chap-frontend
   itself shows a "Low response timeout" warning recommending 30 s.
   Bump it to **30**.

The chap route UID is fixed (`E8OPcc45A22`) on the dev DHIS2 image. If
it differs on yours, look it up first:

```bash
curl -sS -u admin:district 'http://localhost:8080/api/routes?fields=id,code,name'
```

Both settings go in one JSON-Patch (RFC 6902) PATCH:

```bash
cat > /tmp/route_patch.json <<'EOF'
[
  {"op":"replace","path":"/url","value":"http://host.docker.internal:8000/**"},
  {"op":"replace","path":"/responseTimeoutSeconds","value":30}
]
EOF

curl -sS -u admin:district \
  -X PATCH http://localhost:8080/api/routes/E8OPcc45A22 \
  -H 'Content-Type: application/json-patch+json' \
  --data-binary @/tmp/route_patch.json

# Verify
curl -sS -u admin:district http://localhost:8080/api/routes/E8OPcc45A22 \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('url=', d.get('url'))
print('responseTimeoutSeconds=', d.get('responseTimeoutSeconds'))
"

# Smoke-test: DHIS2 proxies the call to chap-core.
curl -sS -u admin:district http://localhost:8080/api/routes/E8OPcc45A22/run/system/info
```

## 5. Save the Dhis2Credentials Prefect block

```bash
PREFECT_API_URL=http://127.0.0.1:9090/prefect/api uv run python <<'PY'
from pydantic import SecretStr
from chap_scheduler.blocks.dhis2 import Dhis2Credentials

bid = Dhis2Credentials(
    base_url="http://host.docker.internal:8080",
    username="admin",
    password=SecretStr("district"),
).save("local-dhis2", overwrite=True)
print("block_id =", bid)
PY
```

The `block_id` printed is the UUID of the saved block — you'll pass it
verbatim in step 6's flow-run trigger.

## 6. Trigger the Prefect deployment + watch + dump the artifact

Single block that finds the deployment, kicks a run, polls until
terminal, then prints the run-report markdown artifact.

Flow parameters: `credentials` (required) plus two optional fields --
`end` (a discriminated mode: `{"mode": "calculated"}` to probe DHIS2
for full covariate coverage, `{"mode": "fixed", "date": "YYYY-MM-DD"}`
for a pinned date, or `{"mode": "offset", "offset": N}` for N periods
back from today; defaults to `calculated` when omitted) and
`configured_model_id` (run only this one CMWDS row; null = all rows).

```bash
DEP=$(curl -s http://127.0.0.1:9090/prefect/api/deployments/filter \
        -H 'Content-Type: application/json' -d '{}' \
      | python3 -c "import sys, json; print(next(d['id'] for d in json.load(sys.stdin) if d['name']=='dhis2-chap-prediction'))")

FLOW=$(curl -sS -X POST http://127.0.0.1:9090/prefect/api/deployments/$DEP/create_flow_run \
        -H 'Content-Type: application/json' \
        -d '{"parameters": {"credentials": {"$ref": {"block_document_id": "<BLOCK_ID_FROM_STEP_5>"}}}}' \
      | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

# Variant: offset mode (1 period back from today) and scope to a single CMWDS row.
# (Identical to the above except for the -d payload.)
#   -d '{"parameters": {"credentials": {"$ref": {"block_document_id": "<BLOCK_ID>"}}, "end": {"mode": "offset", "offset": 1}, "configured_model_id": 1}}'
# Variant: fixed end date.
#   -d '{"parameters": {"credentials": {"$ref": {"block_document_id": "<BLOCK_ID>"}}, "end": {"mode": "fixed", "date": "2026-04-30"}}}'

echo "flow run id: $FLOW"

# Poll
for i in $(seq 1 60); do
  state=$(curl -s http://127.0.0.1:9090/prefect/api/flow_runs/$FLOW \
          | python3 -c "import sys, json; r=json.load(sys.stdin); print(r['state']['type'])")
  echo "[$i $(date +%H:%M:%S)] $state"
  case "$state" in COMPLETED|FAILED|CRASHED|CANCELLED) break ;; esac
  sleep 10
done

# Dump the markdown artifact the flow emits.
curl -sS -X POST http://127.0.0.1:9090/prefect/api/artifacts/filter \
  -H 'Content-Type: application/json' \
  -d "{\"artifacts\": {\"flow_run_id\": {\"any_\": [\"$FLOW\"]}}}" \
| python3 -c "
import sys, json
for a in json.load(sys.stdin):
    print(a.get('data', '')[:2000])
"
```

## 7. Dogfood the chap-client CLI against chap-core

```bash
# All chap-client calls take --base-url or CHAP_CLIENT_BASE_URL.
export CHAP_CLIENT_BASE_URL=http://localhost:8000

# System / discovery
uv run chap-client info
uv run chap-client datasets list
uv run chap-client models list
uv run chap-client models list-configured

# Evaluations (chap-frontend calls these "Evaluations"; chap-core URLs say "backtests")
uv run chap-client evaluations list
uv run chap-client evaluations create \
    --name 'cli-roundtrip' \
    --model-id chap_ewars_monthly \
    --dataset-id 1
# returns {"id": "<job-uuid>"}

# Job lifecycle
uv run chap-client jobs list
uv run chap-client jobs status <job-uuid>
uv run chap-client jobs description <job-uuid>

# Materialise a configured-model-with-data-source from a finished evaluation
uv run chap-client cmwds from-evaluation <eval-id>

# Pull stored prediction values
uv run chap-client predictions entries <prediction-id> -q 0.1 -q 0.5 -q 0.9
```

## 8. Repo dev tasks

```bash
# Lint + types
make check

# Tests + coverage gate
make coverage

# Docs site build (strict)
make docs-strict

# Auto-format
uv run ruff format src/ tests/ chap_client/

# Auto-fix imports / minor lints
uv run ruff check --fix src/ tests/ chap_client/

# Run a focused test
uv run pytest tests/test_flow_polling_and_routing.py -x -q

# Sync deps after a pyproject.toml change
uv sync
```

## 9. Cleanup

```bash
# Stop chap-scheduler stack but keep volumes.
docker compose -f compose.yml down

# Remove DHIS2's connection to chap-core's network when done.
docker network disconnect chap-core_default dhis2 || true

# Stop everything (chap-scheduler + chap-core + DHIS2).
docker compose -f compose.yml down
( cd /Users/morteoh/dev/chap-sdk/chap-core && docker compose down )
# DHIS2 stack: per the dhis2-docker repo's own teardown.
```

## Notes on what each command actually does

- **`docker network connect`** — adds a second NIC to the running
  container so it can talk to services on the other compose network.
  Used because DHIS2 lives in one compose stack and chap-core in
  another; without this the chap-route can't resolve `chap-core` /
  `chap` / `host.docker.internal` (depending on how chap was named).
- **`PATCH /api/routes/{uid}`** with
  `Content-Type: application/json-patch+json` — JSON-Patch RFC 6902
  against the route resource. The `chap` UID is fixed
  (`E8OPcc45A22`) on the dev DHIS2 image; if it differs on yours,
  swap the UID after a `GET /api/routes`.
- **`Dhis2Credentials.save("local-dhis2")`** — stores the DHIS2
  connection in Prefect's block-document store. The flow trigger then
  looks it up by document id, so we don't bake credentials into the
  trigger payload.
- **`/api/deployments/filter`** — Prefect's "find a deployment by
  filter" endpoint. We pass `{}` to get them all and pick by name in
  Python.
- **`/api/artifacts/filter`** — Prefect's artifact lookup. We filter
  by `flow_run_id` to pull the markdown the flow emitted.
