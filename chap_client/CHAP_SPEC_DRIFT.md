# chap-core OpenAPI ↔ wire drift notes

Things we've found while writing chap_client where the OpenAPI spec at
`/openapi.json` doesn't match what chap actually returns / accepts.
Each entry is one finding plus the smallest proof. The intent is for
the chap-core team to either fix the spec, fix the wire shape, or
document why they differ.

Filed against chap-core 2.0.0.dev1 (per `/system/info`), tested
against `http://localhost:8000` on **2026-05-08**.

---

## 1. `ModelSpecRead.target` and `.covariates` are typed as `string` but wire is an object

**Endpoint:** `GET /v1/crud/models` (and `/v1/crud/configured-models`,
which returns the same schema)

**Spec says:**
```yaml
ModelSpecRead:
  properties:
    target: {type: string}
    covariates:
      type: array
      items: {type: string}
```

**Wire actually returns:**
```json
{
  "target": {"displayName": "Disease cases", "description": "...", "name": "disease_cases"},
  "covariates": [
    {"displayName": "Rainfall", "description": "...", "name": "rainfall"},
    {"displayName": "Population", "description": "...", "name": "population"}
  ]
}
```

**Likely fix:** introduce a named `Feature` (or `FeatureRef`) component
in the spec with `{name, displayName, description}` and have
`ModelSpecRead.target` reference it (and `.covariates` be an array of
it). chap_client now models it as `ChapFeature`.

**Caught by:** `pydantic_core.ValidationError: Input should be a valid
string` when calling `client.list_models()` against a real chap on
2026-05-08.

---

## 2. `/v1/crud/models` and `/v1/crud/configured-models` return the same data

**Endpoints:** `GET /v1/crud/models`, `GET /v1/crud/configured-models`

**Observed:** Both endpoints return the same 12-item list with the
same `ModelSpecRead` schema and the same numeric ids (1-12) on a fresh
chap instance. There's no observable difference between the two from
the wire.

**Confusion this causes for callers:** the names suggest a meaningful
distinction ("models" vs "configured models"), but the endpoints
behave identically. A consumer can't tell from the API surface which
one to call, or whether the IDs are interchangeable with downstream
endpoints (see finding 3).

**Likely fix:** either (a) clarify in the spec that these are aliases,
(b) actually differentiate them (e.g. one returns templates and the
other returns configured instances), or (c) deprecate one in favour of
the other.

---

## 3. `POST /v1/crud/configured-models` 500s on a `modelTemplateId` that came from `/v1/crud/models`

**Endpoint:** `POST /v1/crud/configured-models`

**Reproduction:** Pick any id from `/v1/crud/models` (e.g. 12). POST
`{"name": "smoke-test", "modelTemplateId": 12}`.

**Wire returns:**
```
HTTP 500
{
  "detail": "Internal server error",
  "error": "Model template with id 12 not found",
  "type": "AssertionError"
}
```

**Root cause:** The id space of `/v1/crud/models` is **not** the id
space of `modelTemplateId`. The valid id space is `/v1/crud/model-templates`,
which on the same instance returns 11 entries (ids 1-11). Picking from
the wrong endpoint silently looks correct (the schema validates) but
fails at chap's persistence layer.

**Two issues stacked:**

1. **The id-space confusion.** Three model-related endpoints
   (`/v1/crud/models`, `/v1/crud/configured-models`,
   `/v1/crud/model-templates`) all expose `id` fields in seemingly
   overlapping ranges, but only one is valid for `modelTemplateId`.
   Either rename for clarity or document the cross-references in the
   schemas.
2. **The 500-instead-of-400.** A user-supplied invalid foreign key
   should return `404 Not Found` or `400 Bad Request`, not `500
   Internal Server Error` with an `AssertionError` leaking out. chap
   knows perfectly well "id 12 not found" -- that's a client error,
   not a server error.

**Likely fix:** validate `modelTemplateId` at request boundaries and
return a typed 4xx with a clear error body. As a bonus, the spec for
this endpoint should reference `ModelTemplateRead.id` so generated
clients can hint about the right id-space.

---

## 4. `POST /v1/crud/configured-models` mutates the supplied `name`

**Endpoint:** `POST /v1/crud/configured-models`

**Reproduction:**
```python
spec = ChapConfiguredModelCreate(name="smoke-test-from-template-1", modelTemplateId=1)
created = client.create_configured_model(spec)
# created.name == "chap_ewars_monthly:smoke-test-from-template-1"
```

**Observed:** chap rewrites the supplied `name` to
`{template_name}:{supplied_name}`. Nothing in the request schema or
endpoint description mentions this -- a caller that needs the saved
name to match the supplied name (e.g. for downstream lookup) gets a
quiet surprise.

**Likely fix:** either (a) document the prefix behaviour in the
endpoint description, or (b) drop the rewrite and let callers pick
their own names. If the prefix is namespacing for collision avoidance,
make it an explicit field (`namespace: str`) so callers know what's
happening.

---

## 5. Evaluation `modelId` is a free-form string with no validation; UI falls back to a numeric id when the name no longer resolves

**Endpoints:** `POST /v1/analytics/create-backtest` (request shape) and
`GET /v1/crud/backtests` (response shape).

**Spec / wire shape:** `BacktestCreate.modelId` and `BacktestRead.modelId`
are both typed as `string`. The name `modelId` strongly implies a
foreign-key reference to one of the model-related id endpoints, but in
practice the field stores a configured-model **name** (e.g.
`"chap_ewars_monthly"`), not an id from anywhere.

**Reproduction:**
```bash
# Create an evaluation referencing a configured-model name that
# exists today.
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"x","modelId":"chap_ewars_monthly","datasetId":1}'
# -> {"id": "<job-uuid>"}; chap accepts the call and the job runs.

# Now create another evaluation with a string that does NOT match any
# configured model on this instance.
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"x","modelId":"completely-made-up","datasetId":1}'
# chap stores the string verbatim with no validation error.
```

**Two issues stacked:**

1. **No referential integrity at submission time.** `modelId` is
   stored on the evaluation row exactly as supplied. There's no
   foreign-key check against `/v1/crud/configured-models`. A typo or
   a configured model that's later removed leaves an evaluation row
   pointing at a name that no longer resolves.
2. **The UI silently falls back to a numeric id.** chap's modeling
   app renders the "Model" column by looking the `modelId` up against
   `/v1/crud/configured-models` and showing the resolved
   `modelTemplate.displayName`. When the lookup fails, the UI
   displays a bare integer (the configured-model id chap had
   captured at evaluation-creation time, sourced from somewhere
   else on the row). Two evaluations created on the same chap
   instance can therefore render as `"Monthly CHAP-EWARS model"` and
   `"12"` despite both being legitimate -- one resolves, the other
   doesn't, and there's no UI hint that "12" is a fallback.

**Caught by:** running an end-to-end via the chap-scheduler CLI on
2026-05-08; four evaluations on the same chap rendered with three
different "Model" column shapes (a displayName, a numeric id, and an
explicit name) depending on whether their `modelId` string still
resolved to a live configured model.

**Likely fix:**

- (a) **At submission:** validate `modelId` against
  `/v1/crud/configured-models` and return `400` / `404` if it
  doesn't resolve. Or rename to `configuredModelName` so the
  intent is unambiguous.
- (b) **At persistence:** store the resolved configured-model **id**
  alongside the supplied name on the evaluation row, so the UI can
  link by id (stable) and label by name (humane) without a runtime
  lookup. This also gives the UI a reliable fallback when the
  configured model is later renamed.
- (c) **At rendering:** if the UI must keep its current model-name
  lookup, it should at minimum surface "model `chap_ewars_monthly`
  (no longer registered)" rather than a bare integer.

**Live proof (2026-05-08):** chap accepts literally any string for
`modelId`. Two consecutive submissions, both returned a job id with
no validation error:

```bash
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"drift-test-abcwtf","modelId":"abcwtf","datasetId":1}'
# -> {"id": "c0c6a6df-a877-454d-9049-1931fde32b6c"}

curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"drift-test","modelId":"rainbow-unicorn-pony","datasetId":1}'
# -> {"id": "576039ce-317a-4f9f-97d7-2443c415cfae"}
```

Both jobs reach status `FAILURE` ~1 second later (the worker discovers
the unresolvable `modelId` at execution time) and chap never persists
a row to `/v1/crud/backtests`. The async failure means the caller has
to poll the job to discover the typo, where a synchronous 4xx at
submission would have caught it instantly.

---

## 6. `datasetId` on `create-backtest` is also unvalidated

**Endpoint:** `POST /v1/analytics/create-backtest`

While reproducing finding 5 we tried sending a clearly non-existent
`datasetId`:

```bash
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"drift-test","modelId":"chap_ewars_monthly","datasetId":99999}'
# -> {"id": "ff6d1150-9e2a-43e8-ac9b-6be731f8aace"}
```

chap accepts the call with no validation error. The job reaches
status `FAILURE` ~1s later when the worker tries to load dataset
`99999` and finds nothing, but again the failure is asynchronous and
the caller only learns about it via job polling.

**Likely fix:** the same submission-time validation as finding 5 --
look up `datasetId` against `/v1/crud/datasets` and return `404` /
`400` synchronously. The dataset id space *is* a real foreign key
(unlike `modelId` which is a name string), so the fix here is more
straightforward.

---

---

## 7. `GET /v1/jobs/{id}` returns 200 `"PENDING"` for non-existent jobs

**Endpoint:** `GET /v1/jobs/{job_id}`

**Reproduction:**

```bash
curl http://localhost:8000/v1/jobs/no-such-job-id-at-all
# -> HTTP 200
# -> "PENDING"
```

**Why this matters:** code that polls a non-existent job id (typo,
race, accidentally truncated UUID) loops forever -- `"PENDING"` is
indistinguishable from a job that is genuinely waiting. chap_client's
`wait_for_prediction` loop in chap-scheduler has a 600-second
timeout, so the failure mode is "deadline exceeded after 10 minutes
of phantom polling" instead of the immediate 404 it should be.

**Likely fix:** look the id up in the jobs table; return `404` if it
isn't there. The handler clearly knows there's no record because it
falls through to the default (`PENDING`) -- it just isn't 404ing.

---

## 8. `GET /v1/crud/backtests/{id}` returns 405 (Method Not Allowed)

**Endpoint:** `GET /v1/crud/backtests/{backtestId}`

**Reproduction:**

```bash
curl -i http://localhost:8000/v1/crud/backtests/1
# -> HTTP/1.1 405 Method Not Allowed
# -> {"detail":"Method Not Allowed"}
```

**Why this matters:** GET *is* allowed on this resource -- the
endpoints chap actually implements are `/v1/crud/backtests/{id}/info`
and `/v1/crud/backtests/{id}/full`. The bare path has no handler so
FastAPI returns 405. A naive consumer reading the spec sees
"`/v1/crud/backtests/{backtestId}` exists" (because the path prefix
matches `/{id}/info`) and assumes a bare GET will work.

**Likely fix:** either (a) implement the bare GET as an alias for
`/info`, (b) return 404 with a hint pointing to `/info` and `/full`,
or (c) document explicitly that the bare path is not a resource URL
and the canonical view is `/info`.

---

## 9. `DELETE /v1/crud/configured-models-with-data-source/{id}` returns 405

**Endpoint:** `DELETE /v1/crud/configured-models-with-data-source/{id}`

**Reproduction:**

```bash
curl -i -X DELETE http://localhost:8000/v1/crud/configured-models-with-data-source/1
# -> HTTP/1.1 405 Method Not Allowed
# -> {"detail":"Method Not Allowed"}
```

**Why this matters:** every other CRUD resource (`datasets`,
`backtests`, `configured-models`) supports `DELETE` and returns the
expected 404 / 204. cmwds is the odd one out -- the only way to
"remove" a cmwds row is at the database layer. chap-scheduler wires a
new cmwds row each time `from-backtest` is called, so over time the
list grows monotonically.

**Likely fix:** add a `DELETE` handler. If there's a reason cmwds
rows are append-only (e.g. predictions reference them by id and chap
wants to preserve history), document that and either (a) add a
`POST /archive` endpoint or (b) document the workaround.

---

## 10. `POST /v1/crud/configured-models` returns 500 when `userOptionValues` is omitted

**Endpoint:** `POST /v1/crud/configured-models`

**Reproduction:**

```bash
curl -i -X POST http://localhost:8000/v1/crud/configured-models \
  -H 'Content-Type: application/json' \
  -d '{"name":"test","modelTemplateId":1}'
# -> HTTP/1.1 500 Internal Server Error
# -> {"detail":"Internal server error","error":"Invalid user options: None is not of type 'object'","type":"ValueError"}

# Sending the field explicitly succeeds:
curl -X POST http://localhost:8000/v1/crud/configured-models \
  -H 'Content-Type: application/json' \
  -d '{"name":"test","modelTemplateId":1,"userOptionValues":{}}'
# -> HTTP/1.1 200
```

**Why this matters:** the spec types `userOptionValues` as optional
with default `{}`, but the implementation crashes with a `ValueError`
when it's missing instead of applying the default. A 500 with
`type: ValueError` shouldn't reach the client for a validation
problem.

**Likely fix:** either (a) default `userOptionValues` to `{}`
server-side at the request boundary, or (b) make the field required
in the spec and return 400 (not 500) when it's missing.

---

## 11. `POST /v1/crud/configured-models` is silently idempotent on `(name, modelTemplateId)`

**Endpoint:** `POST /v1/crud/configured-models`

**Reproduction:**

```bash
# First POST -> creates row with id 14.
curl -X POST http://localhost:8000/v1/crud/configured-models \
  -H 'Content-Type: application/json' \
  -d '{"name":"dup-test","modelTemplateId":1,"userOptionValues":{}}'
# -> {"id":14, "name":"chap_ewars_monthly:dup-test", ...}

# Second POST with the same body -> returns the SAME id, not a 409.
curl -X POST http://localhost:8000/v1/crud/configured-models \
  -H 'Content-Type: application/json' \
  -d '{"name":"dup-test","modelTemplateId":1,"userOptionValues":{}}'
# -> {"id":14, "name":"chap_ewars_monthly:dup-test", ...}
```

**Why this matters:** the endpoint behaves as an upsert (looks up an
existing row by `name + modelTemplateId` and returns it if it exists)
but the spec types it as a `POST /create`. Standard REST semantics
say a duplicate POST should 409 or 422; behaviour here is more like
`PUT`. A caller that retries a POST after a connection blip can't
tell whether they double-submitted, hit the upsert, or got the same
row back twice.

**Likely fix:** either (a) document the upsert behaviour explicitly
in the endpoint description, (b) reject duplicates with a 409, or
(c) split into separate `POST /create` (rejects dups) and
`PUT /upsert` endpoints.

---

## 12. List endpoints silently ignore unknown query params

**Endpoints:** every CRUD list endpoint we tested
(`/v1/jobs`, `/v1/crud/backtests`, `/v1/crud/datasets`).

**Reproduction:**

```bash
# These all return the FULL list, ignoring the params.
curl 'http://localhost:8000/v1/jobs?limit=2'        # -> 33 rows, not 2
curl 'http://localhost:8000/v1/jobs?status=SUCCESS' # -> 33 rows incl. PENDING / FAILURE
curl 'http://localhost:8000/v1/crud/backtests?limit=1' # -> 4 rows, not 1
curl 'http://localhost:8000/v1/crud/datasets?type=evaluation' # -> 27 rows incl. type=prediction
```

**Why this matters:** chap silently accepts query parameters and
ignores them. A caller assumes `?limit=10` is bounding their request
and only discovers otherwise when the response gets unexpectedly
large. There's no pagination story today, so the list endpoints have
to return the entire table -- this becomes a real problem as the
jobs table grows (already 33 rows on a dev instance).

**Likely fix:** either (a) implement `limit` / `offset` /
`status` / `type` as documented filters, or (b) reject unknown query
parameters with a `422`. Silently ignoring them is the worst option.

---

## 13. Visualization endpoints return HTTP 200 with `{"error": ...}` body on missing ids

**Endpoint:** `GET /v1/visualization/backtest-plots/{visualization_name}/{backtest_id}`
(and presumably the sibling visualization paths).

**Reproduction:**

```bash
curl -i 'http://localhost:8000/v1/visualization/backtest-plots/horizon_location_grid/99999'
# -> HTTP/1.1 200 OK
# -> Content-Type: application/json
# -> {"error":"Backtest not found"}
```

**Why this matters:** a successful HTTP status code is the wire
contract for "this is the resource you asked for". Returning 200 with
an `{"error": ...}` body forces every caller to inspect the body to
distinguish success from failure, defeating the whole point of HTTP
status codes. It also breaks the rest of chap-core's error
convention (`{"detail": "..."}` with the matching 4xx code).

**Likely fix:** return `404` with `{"detail":"Backtest not found"}`
to match the rest of the API. Same fix applies to all
`/v1/visualization/**/{id}` endpoints with the same shape.

---

---

## 14. `POST /v1/analytics/create-backtest` accepts empty `name`

**Endpoint:** `POST /v1/analytics/create-backtest`

**Reproduction:**

```bash
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"","modelId":"chap_ewars_monthly","datasetId":1}'
# -> HTTP 200
# -> {"id": "748c44c5-b220-4e0b-a54a-62ed2acfe5e1"}
```

**Why this matters:** `name` is the only thing distinguishing rows in
the chap UI's evaluation list. An empty-name evaluation renders as a
blank row that can't be selected by name. Same family as findings 5 and
6: chap silently accepts a degenerate value at submission, then
materialises the bad row when the job completes.

**Likely fix:** require `name` to be non-empty (Pydantic
`min_length=1`); return 422 at submission. Optional follow-on:
trim whitespace and enforce a max length to avoid DoS-by-very-long-name.

---

## 15. `POST /v1/analytics/create-backtest` accepts negative `nPeriods`

**Endpoint:** `POST /v1/analytics/create-backtest`

**Reproduction:**

```bash
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"probe","modelId":"chap_ewars_monthly","datasetId":1,"nPeriods":-5}'
# -> HTTP 200
# -> {"id": "7eba1fab-2c22-4462-a669-c06dc3996b33"}
```

**Why this matters:** `nPeriods` is a positive count of forecast
horizons; a negative value has no semantic meaning. Same async-failure
pattern as findings 5/6/14: the worker fails downstream instead of
chap rejecting at submission.

**Likely fix:** Pydantic `Field(gt=0)` on `nPeriods`, `nSplits`, and
`stride`. Return 422 synchronously with a clear field-level error.

---

## 16. POST endpoints silently ignore unknown top-level fields

**Endpoints:** all the `POST /v1/...` mutating endpoints we tested.

**Reproduction:**

```bash
curl -sS -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"probe","modelId":"chap_ewars_monthly","datasetId":1,"weirdExtraField":"ignored?"}'
# -> HTTP 200, no warning that `weirdExtraField` was dropped
```

**Why this matters:** typos in field names (e.g. `dataSetId` vs
`datasetId`) silently fall through to chap's defaults, which can
produce a job that does the wrong thing without any error. Pydantic
defaults to `extra="ignore"` so chap is just inheriting that
behaviour, but for mutating endpoints `extra="forbid"` is the safer
default -- a typo'd `nPriods` is more useful as a 422 than as a
silently-defaulted job.

**Likely fix:** set `model_config = ConfigDict(extra="forbid")` on
the request models for mutating endpoints (`BacktestCreate`,
`PredictionCreate`, `ConfiguredModelCreate`, ...). Read endpoints can
keep `extra="ignore"` for forward compatibility.

---

## 17. CORS reflects any `Origin` with `allow-credentials: true` (security)

**Endpoint:** every endpoint behind chap-core's CORS middleware.

**Reproduction:**

```bash
curl -sS -i -X OPTIONS http://localhost:8000/v1/crud/datasets \
  -H 'Origin: http://example.com' \
  -H 'Access-Control-Request-Method: GET'
# -> HTTP/1.1 200 OK
# -> access-control-allow-origin: http://example.com   <-- reflected!
# -> access-control-allow-credentials: true
# -> access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
```

**Why this matters:** the combination of (a) reflecting an arbitrary
`Origin` header back as `Access-Control-Allow-Origin` and (b)
`Access-Control-Allow-Credentials: true` is the textbook CSRF setup.
Any malicious site that can convince a chap user's browser to make a
cross-origin request can read the response with the user's cookies /
basic-auth attached. On a public chap deployment this would let a
third-party page exfiltrate the user's datasets, evaluations, and
predictions.

When chap is reached via the DHIS2 proxy `(/api/routes/chap/run)` the
DHIS2 layer enforces same-origin so this is moot; but anyone running
chap on its own port (the docker compose default) is exposed.

**Likely fix:** in chap-core's FastAPI app config, restrict
`allow_origins` to a known list (e.g. the DHIS2 instance origin) or
disable `allow_credentials` and require explicit token auth instead
of cookies. **Do not** combine `allow_origin_regex=".*"` with
`allow_credentials=True` -- Starlette's CORS docs flag this exact
combination as unsafe.

This is the only **security** finding in this file; everything else
is correctness / UX.

---

## 18. 35 of 65 endpoints have no `description` field in the OpenAPI spec

**Endpoint:** the `/openapi.json` document itself.

**Reproduction:**

```bash
curl -s http://localhost:8000/openapi.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
miss = sum(
    1
    for methods in d['paths'].values()
    for m, op in methods.items()
    if m in ('get','post','put','delete','patch') and not op.get('description')
)
total = sum(1 for ms in d['paths'].values() for m in ms if m in ('get','post','put','delete','patch'))
print(f'{miss}/{total} endpoints missing description')
# -> 35/65 endpoints missing description
```

**Why this matters:** chap-core's spec is the contract for every
generated client (chap_client included). Endpoints with only a one-line
`summary` and no `description` give a consumer no idea what the
endpoint actually does, what its preconditions are, or how its
behaviour relates to other endpoints. The drift findings 5-13 in this
file are exactly the kind of "you'd only know this from running it"
behaviours that a `description` could surface.

**Likely fix:** add a one-paragraph `description=` to every endpoint's
FastAPI decorator. The chapkit team has a similar convention worth
borrowing. Doesn't have to be exhaustive; even "Returns the raw row;
see ../{id}/info for the merged read view" would resolve finding 8.

---

---

## 19. `POST /v1/jobs/<bogus-id>/cancel` returns 200 "cancelled"

**Endpoint:** `POST /v1/jobs/{job_id}/cancel`

**Reproduction (via chap_client):**

```python
from chap_client import ChapClient
with ChapClient(base_url="http://localhost:8000") as client:
    body = client.post("/v1/jobs/no-such-job-id/cancel")
    # -> 200 OK
    # -> {"message": "Job no-such-job-id has been cancelled"}
```

**Why this matters:** chap acknowledges the cancellation of a job
that doesn't exist. Combined with finding 7 (`GET /v1/jobs/<bogus>`
returns 200 `"PENDING"`), the cancel endpoint is even worse: real
job ids that can't be cancelled get a `400`, but bogus ids get a
`200`. The contract is inverted from what callers expect.

**Likely fix:** validate the job id against the jobs table; return
`404` when the id isn't there. Same fix family as findings 7 and 22.

---

## 20. `/v1/jobs/{id}/evaluation_result` and `/prediction_result` return 500 on real success jobs (broken `response_model`)

**Endpoints:**
- `GET /v1/jobs/{job_id}/evaluation_result`
- `GET /v1/jobs/{job_id}/prediction_result`

**Reproduction (via chap_client):**

```python
from chap_client import ChapClient
with ChapClient(base_url="http://localhost:8000") as client:
    job = next(j for j in client.list_jobs() if j.status == "SUCCESS")
    client.get(f"/v1/jobs/{job.id}/evaluation_result")
# raises ChapHttpError(status=500, detail={"detail": "Internal server error",
#   "error": "1 validation error:\n  {'type': 'model_attributes_type',
#             'loc': ('response',), 'msg': 'Input should be a valid dictionary
#             or object to extract fields from', 'input': 7}", ...})
```

The error trace points at chap-core itself
(`File "/app/chap_core/rest_api/v1/jobs.py", line 120`).

**Why this matters:** chap-core's own response_model declarations
don't match what the endpoint actually returns. The handler returns
a bare integer (`7`, the prediction id) but the declared
`response_model` is something dict-shaped. FastAPI's response
validation fires *after* the handler runs, so chap returns 500 to
clients on a successful internal operation.

**Compare with the sibling endpoint that works:**
`/v1/jobs/{id}/database_result` correctly returns
`{"id": 7}`. The fix on the broken pair is presumably to wrap the
return value in the same `{"id": ...}` envelope, or to relax the
response_model.

**Likely fix:** either correct the handler return shape to match the
declared `response_model`, or update the response_model to match
what the handler actually returns. Both endpoints are unusable as-is.

---

## 21. `/v1/jobs/<bogus-id>/{database,evaluation,prediction}_result` returns 500 with internal `TaskRevokedError` leaked

**Endpoints:** the three `*_result` sub-endpoints under `/v1/jobs/{id}`.

**Reproduction (via chap_client):**

```python
client.get("/v1/jobs/no-such-job-id/database_result")
# -> 500 ChapHttpError, detail.error contains:
#   "1 validation error for DataBaseResponse
#    id
#      Input should be a valid integer
#      [type=int_type, input_value=TaskRevokedError('revoked'), input_type=TaskRevokedError]"
```

**Why this matters:** the handler probes Celery/whatever-runs-jobs
for a job by id, gets back a `TaskRevokedError` object when the id
doesn't exist, and then tries to serialise that error object as the
response body. The result is a 500 with chap-core's internal
exception class name leaked to the wire. From a caller's perspective
"this id doesn't exist" is the same response shape as "chap is broken".

**Likely fix:** check whether the job exists in the jobs table
*first*; return `404` synchronously. Don't ask the task runner about
ids that aren't yours, and don't let `TaskRevokedError` ever reach
FastAPI's response serialisation path.

---

## 22. `/v1/jobs/<bogus-id>/logs` returns 200 with empty string

**Endpoint:** `GET /v1/jobs/{job_id}/logs`

**Reproduction (via chap_client):**

```python
client.get("/v1/jobs/no-such-job-id/logs")
# -> 200 OK
# -> ""
```

**Why this matters:** another "phantom-success" sibling of finding 7.
A typo'd job id silently returns an empty string; the caller has no
way to distinguish "this job has no log output yet" from "this job
doesn't exist".

**Likely fix:** 404 on unknown id (same pattern as 7, 19).

---

## 23. `/info` and `/full` on backtests have unrelated, partially-overlapping shapes

**Endpoints:**
- `GET /v1/crud/backtests/{id}/info`
- `GET /v1/crud/backtests/{id}/full`

**Reproduction (via chap_client):**

```python
info = client.get("/v1/crud/backtests/1/info")
full = client.get("/v1/crud/backtests/1/full")

# info has but full lacks: ['configuredModel', 'dataset']
# full has but info lacks: ['modelDbId']
# size of info: 3498 bytes
# size of full: 1044 bytes
```

**Why this matters:** the names imply `/full ⊇ /info`, which is the
intuitive REST convention: `/info` is a summary, `/full` adds detail.
Reality is the opposite: `/info` includes the embedded
`configuredModel` and `dataset` blocks (3.5 kB) while `/full` is
1 kB and adds only one field (`modelDbId`). Neither is a strict
superset; choosing between them requires reading the response body
schemas.

**Likely fix:** rename the endpoints to reflect what they actually
return, or align them so `/full` is a strict superset. A consumer
who asks for `/full` and gets less data than `/info` cannot
realistically be expected to know that.

---

## 24. `/v1/analytics/backtest-overlap/{a}/{b}` "not found" message uses path position, not id

**Endpoint:** `GET /v1/analytics/backtest-overlap/{backtestId1}/{backtestId2}`

**Reproduction (via chap_client):**

```python
client.get("/v1/analytics/backtest-overlap/1/99999")
# -> 404 {"detail": "Backtest 2 not found"}    <-- "2" is the URL position

client.get("/v1/analytics/backtest-overlap/99999/1")
# -> 404 {"detail": "Backtest 1 not found"}    <-- "1" is the URL position
```

**Why this matters:** the error message looks like it's referencing
backtest id `2` (or `1`), but it's actually telling the caller "the
second (or first) backtest in your URL was not found". A caller
checking `if "Backtest 2" in detail: ...` is reading garbage.

**Likely fix:** include the actual offending id, e.g. `"Backtest
99999 not found"`. Or split into two distinct error keys
(`"firstBacktestNotFound"` / `"secondBacktestNotFound"`) so callers
can branch programmatically.

---

## Filing status (2026-05-08)

None of these findings have been filed against
[chap-core](https://github.com/dhis2-chap/chap-core) or chap-frontend
yet. This file is the working set; once a finding is filed upstream,
add the issue / PR link next to its number so we can prune fixes as
they land.

- Findings 1-4 caught early in the chap_client extraction.
- Findings 5-6 caught while answering a UX question on PR #25.
- Findings 7-13 caught in a deliberate debug-session probe of
  chap-core after PR #25 merged. Each was reproduced with an
  explicit `curl` that's reproducible against `localhost:8000` on
  chap-core 2.0.0.dev1.
- Findings 14-18 caught in a follow-on validation / config probe
  on the same chap-core instance. Notably **#17 is a security
  issue** (CORS reflective + credentials) and should be filed first
  if these are being triaged by impact.
- Findings 19-24 caught while dogfooding the probe through
  `chap_client` itself (raw `client.get()` / `client.post()` for
  unmodelled endpoints + `list_jobs()` for the typed-method side).
  Notable: **#20 is a chap-core 500 on its own response model** --
  `/v1/jobs/{id}/evaluation_result` and `/prediction_result` are
  unusable for callers regardless of input.
- Findings 25-27 caught in the dataset-export / route-ordering
  pass. Notable: **#26 is a real production crash** -- `/df` 500s
  on any dataset with `NaN` cells, which is most of them.

---

## 25. Route ordering: `/v1/crud/datasets/csvFile` is shadowed by `/v1/crud/datasets/{datasetId}`

**Endpoints:**
- `POST /v1/crud/datasets/csvFile` (in the spec, used to upload CSV)
- `GET /v1/crud/datasets/{datasetId}` (catches `csvFile` as a path param)

**Reproduction (via chap_client):**

```python
client.get("/v1/crud/datasets/csvFile")
# -> 422 ChapHttpError
# -> {'detail': [{'type': 'int_parsing', 'loc': ['path', 'datasetId'],
#                 'msg': 'Input should be a valid integer, unable to
#                         parse string as an integer', 'input': 'csvFile'}]}
```

**Why this matters:** the dynamic `{datasetId}` route is registered
ahead of the static `csvFile` route and matches *any* string -- so
chap tries to parse the literal string `"csvFile"` as an int and
fails with a `path` validation error. A user reading the OpenAPI
spec sees a `csvFile` endpoint and reasonably tries to GET it (e.g.
to introspect what shape it accepts), and gets a confusing 422 about
integer parsing instead of a proper 405 / "this endpoint is POST-only".

**Likely fix:** in chap-core's FastAPI app, register the static
`csvFile` route *before* the parametric `{datasetId}` route. FastAPI
matches in registration order, so reordering is a one-line fix.

---

## 26. `GET /v1/crud/datasets/{id}/df` returns 500 on any dataset containing `NaN` values

**Endpoint:** `GET /v1/crud/datasets/{datasetId}/df`

**Reproduction (via chap_client):**

```python
client.get("/v1/crud/datasets/1/df")
# -> 500 ChapHttpError
# -> {'detail': 'Internal server error',
#     'error': 'Out of range float values are not JSON compliant: nan',
#     'type': 'ValueError'}
```

The dataset id `1` here is the `test` dataset on a fresh chap
instance -- **production data**, not contrived input. CSV export of
the same dataset works fine (`/csv`); only `/df` (the DataFrame /
JSON shape) crashes.

**Why this matters:** real datasets routinely have `NaN` cells (a
covariate didn't have a measurement for some org-unit / period). The
JSON serialiser's "no NaN" rule is a general gotcha, but chap-core's
`/df` endpoint hands raw float values to FastAPI without first
substituting `None` (or omitting the row, or stringifying as
`"NaN"`). Every consumer hitting `/df` against any non-toy dataset
will 500.

**Likely fix:** before serialisation, walk the DataFrame and replace
`NaN` with `None` (which serialises as JSON `null`). Or document
that `/df` is "complete grids only" and surface a 422 with a list of
the (org_unit, period, covariate) cells that are missing -- the same
shape `ChapMissingValuesDetail` already uses elsewhere.

---

## 27. CSV / DF dataset endpoints return 500 (not 404) for unknown ids

**Endpoints:**
- `GET /v1/crud/datasets/{id}/csv`
- `GET /v1/crud/datasets/{id}/df`

**Reproduction (via chap_client):**

```python
client.get("/v1/crud/datasets/99999/csv")
# -> 500 {'detail': 'Internal server error',
#         'error': 'Dataset with id 99999 not found', 'type': 'ValueError'}
client.get("/v1/crud/datasets/99999/df")
# -> 500 (same shape)
```

**Why this matters:** the matching `/v1/crud/datasets/{id}` already
returns a clean `404 Dataset not found` for the same input. The
`/csv` and `/df` sub-endpoints raise `ValueError` instead of
`HTTPException(404)`, leaking the internal class name and producing
a 500 instead of a 404. Callers can't programmatically distinguish
"chap is broken" from "I asked for an id that doesn't exist".

**Likely fix:** raise `HTTPException(404, "Dataset not found")` --
mirror the parent `/{id}` endpoint's behaviour. Same pattern as
findings 10 and 21 (replace ad-hoc raises with typed HTTP errors).

---

# chap-frontend (modeling-app) findings

The findings above are all server-side (chap-core REST API). The
findings below are in the chap-frontend modeling app -- the React
single-page-app served at
`http://localhost:8080/apps/dhis2-chapmodeling-app/#/...`. Caught in
a Playwright walkthrough on 2026-05-08 against the same dev DHIS2
instance.

---

## 28. "Report a bug" link goes to `example@example.com`

**Surface:** the alpha-warning banner that renders on every page
(Evaluations, Predictions, Models, Jobs, ...).

**Reproduction:**

```html
<a href="mailto:example@example.com?subject=Modeling App | Issue%20Report:&body=...">
  chap@dhis2.org
</a>
```

The visible link text is `chap@dhis2.org`. The actual `href` is
`mailto:example@example.com`. So every bug report sent via the
"please report to:" link in the alpha-version banner goes to a
placeholder address that doesn't exist.

**Why this matters:** users seeing the alpha-version warning click
the link in good faith. The bug reports fall into the void. We're
discouraging exactly the feedback the app is asking for.

**Likely fix:** point `href` to `mailto:chap@dhis2.org` (or whatever
the real intake address is). Trivial one-character change in the
banner component. Pick a real address and verify it's monitored.

---

## 29. Empty-name evaluations: blank cell on the Evaluations list, "Unnamed" on the Jobs page

**Surface:** two list views render the same backing record with two
different fallbacks.

**Reproduction:** create an evaluation with `name=""` (chap accepts
this; see finding 14):

```bash
curl -X POST http://localhost:8000/v1/analytics/create-backtest \
  -H 'Content-Type: application/json' \
  -d '{"name":"","modelId":"chap_ewars_monthly","datasetId":1}'
# wait for it to finish
```

Now navigate the modeling app:

- `#/evaluate` -> the row's Name cell is **fully blank**, no
  fallback text. The clickable link wraps an empty string.
- `#/jobs` -> the same job's Name cell shows the placeholder
  `"Unnamed"`.

**Why this matters:** the Evaluations row is unfindable from the UI
-- you can't search it, sort it, or click it (the link target is
empty so screen-readers and keyboard nav skip it).

**Likely fix:** apply the same `"Unnamed"` (or "(no name)")
fallback the Jobs page already uses. One shared util function.
Long-term fix is server-side (finding 14: reject empty names at
submission), but the UI should be defensive.

---

## 30. Unresolved `modelId` renders inconsistently across pages

**Surface:** Evaluations list vs Predictions list. Same backing
record (`modelId` is a string that may or may not resolve to a
configured model on this instance).

**Reproduction:** any chap instance where some evaluations /
predictions reference a configured model that no longer exists
(e.g. a chapkit-registered model whose container was restarted; see
finding 5).

- `#/evaluate` -> the `Model` column for unresolved rows shows the
  **numeric configured-model id** ("12").
- `#/predictions` -> the `Model` column for unresolved rows shows
  the **raw name string** ("chapkit-ewars-model").

Resolved rows on both pages show the model template's `displayName`
("Monthly CHAP-EWARS model"). Only the fallback differs.

**Why this matters:** users move between Evaluations and Predictions
freely; the same row referenced from two pages should display the
same way. The numeric-id fallback is the more confusing of the two
(see finding 5); the Predictions page handling (raw name) is
strictly better and should be adopted everywhere.

**Likely fix:** consolidate the resolver into one helper and use it
on both pages. As a parallel, fix the underlying chap-core issue --
finding 5's "validate `modelId` at submission" -- so the fallback
case becomes rare.

---

## 31. Jobs UI labels chap's standard endpoint as `Create evaluation (Legacy)`

**Surface:** `#/jobs` "Type" column.

**Reproduction:** on the Jobs page, every evaluation-creation row
displays "Create evaluation (Legacy)" in the Type column. The
underlying job type from chap-core is `create_backtest` (the
endpoint `/v1/analytics/create-backtest`).

**Why this matters:** users see "(Legacy)" and assume there's a
non-legacy alternative they should be using. There isn't -- this is
the only `create-backtest` path chap-core exposes today, and it's
the path chap_client / chap-scheduler / the modeling app's own
"New evaluation" button all hit. Calling it "Legacy" implies an
upgrade story that doesn't exist.

**Likely fix:** drop the "(Legacy)" suffix in the UI's job-type
formatter. If chap-core has a v2 path planned, label only the
genuinely legacy path "(Legacy)". Note: the user feedback memory
from the prior session already flagged this -- "don't use 'Create
evaluation (Legacy)' use 'Create evaluation' it won't be recognized
in the interface otherwise". The string is still in the UI today.

---

## 32. Page heading "Active jobs" but the table lists every job state

**Surface:** `#/jobs` page heading vs table contents.

**Reproduction:** on the Jobs page, the H2 reads "Active jobs". The
description below it says "View and manage currently running jobs
and their status." But the table lists rows in every state:
`Success`, `Failed`, `Pending`, etc. -- nothing about it is
"active-only".

There's a Status filter (`Status` dropdown) but no default applied,
so the page lands on "all jobs", not "active jobs".

**Why this matters:** heading-content mismatch. Users looking for
their currently-running job see a page titled "Active jobs" stuffed
with finished jobs and have to scroll / filter. Conversely, users
looking at the audit log see "Active jobs" and assume the page is
filtered when it isn't.

**Likely fix:** either rename the heading to "Jobs" (matches the
nav item), or change the page to actually default to active-only
with a "show all" toggle. The current state is the worst of both.

---

## 33. Bookmarking `index.html#/...` URLs returns a DHIS2-level 404

**Surface:** any direct navigation to a chap-frontend route via the
`index.html` form.

**Reproduction:** navigate to either of these in a fresh tab:

- `http://localhost:8080/apps/dhis2-chapmodeling-app/#/jobs` -> works
- `http://localhost:8080/apps/dhis2-chapmodeling-app/index.html#/jobs`
  -> returns DHIS2's HTML 404 ("HTTP Status 404 - Not Found")

Both are valid SPA URL forms in DHIS2 conventions; only the bare
slug works against the chap-frontend. The bookmark/share story is
fragile because the `index.html` form is what most browsers
auto-complete to.

**Why this matters:** any user who copies the URL out of the
address bar at one moment may end up with the `index.html` form,
and pasting it back returns a 404 DHIS2 error page rather than the
modeling app. Looks like a chap-frontend outage to the user.

**Likely fix:** check the chap-frontend's manifest.webapp / build
output to see why DHIS2's app shell only resolves the bare slug.
Probably a one-line `"launchPath"` setting.

---

## Filing status (continued)

### Triage suggestion

If filing by impact:

1. **#17** -- security (reflective CORS + credentials).
2. **#20** -- two endpoints unusable; chap's own `response_model` is broken.
3. **#26** -- `/df` 500s on every real dataset with `NaN` cells.
4. **#28** -- bug-report mailto link is broken; users' feedback goes to nowhere.
5. **#7, #19, #21, #22** -- the "phantom job id" family. Same fix
   pattern (validate id, return 404) applied in 4 places.
6. **#10, #27** -- 500-instead-of-4xx family.
7. **#5, #14, #15, #16** -- chap-core mutating-endpoint validation
   gaps; the chap-frontend findings 29 / 30 are downstream of this.
8. Everything else can be filed as a sweep / cleanup.

### Probe-round provenance

- Findings 1-4 caught early in the chap_client extraction.
- Findings 5-6 caught while answering a UX question on PR #25.
- Findings 7-13 caught in a deliberate API debug-session probe.
- Findings 14-18 caught in a follow-on validation / config probe.
- Findings 19-24 caught while dogfooding the probe through `chap_client`
  itself.
- Findings 25-27 caught in the dataset-export / route-ordering pass.
- Findings 28-33 caught in a Playwright walkthrough of the
  chap-frontend modeling app (admin/district auth, default dev DHIS2,
  same chap-core 2.0.0.dev1 instance backing it). These are the only
  **chap-frontend** findings; everything 1-27 is chap-core.

---

# What we can do in this repo

Every finding above is an upstream issue. This section maps each one
to the most useful thing we can do in **chap-scheduler** /
**chap_client** without waiting for chap-core or chap-frontend to
ship a fix.

The bar is "shipping value to ourselves and the eventual external
chap_client consumer". Not every finding has a useful in-repo
action; for those we just track the link to upstream and move on.

## A. Defensive client-side validation (chap_client)

These are pydantic-level shrinks on `chap_client.schemas` request
models. They reject bad input synchronously with a clear error
instead of letting chap-core accept it and fail the job
asynchronously. Easy wins; don't change wire shape.

| Finding | What we ship | Effect |
|---|---|---|
| **#5** (`modelId` unvalidated) | Preflight in `create_evaluation`: `list_configured_models()`, raise `ValueError` if `request.model_id` doesn't resolve. Optional `validate=False` escape hatch. | Catches typos + stale references at submission, not 60-180s later via `wait_for_prediction`. |
| **#6** (`datasetId` unvalidated) | Preflight in `create_evaluation` against `get_dataset(request.dataset_id)`; remap chap's 404 to `ValueError`. | Same. |
| **#3** (500 on bad `modelTemplateId`) | Preflight in `create_configured_model` against `list_model_templates()`. The new `list_model_templates()` was added at the same time. | Catches the id-space-confusion case described in finding 3. |
| **#10** (500 on omitted `userOptionValues`) | Already mitigated: `ChapConfiguredModelCreate.user_option_values` defaults to `{}` and we always send the field. Docstring note added. | Already shipped. |
| **#14** (empty `name` accepted) | `ChapMakeEvaluationRequest.name`: `Field(min_length=1)`. Same on `ChapMakePredictionRequest.name` and `ChapConfiguredModelCreate.name`. | 422 at validation, never reaches chap. |
| **#15** (negative `nPeriods`) | `nPeriods` / `nSplits` / `stride` / `datasetId` / `modelTemplateId` / `configuredModelWithDataSourceId`: `Field(gt=0)`. | Same. |
| **#16** (extra fields silently ignored) | The three mutating request models now use `ConfigDict(extra="forbid")`; response models keep `extra="ignore"` for forward-compat. | Catches typos like `nPriods` for the field. |

**Status: shipped** in PR #32 (chap_client v0.0.1). Roadmap item #54 done; the seven entries above are now closed at the client layer.

Footprint: schema changes in `chap_client/src/chap_client/schemas.py`, a
new `list_model_templates()` method on `ModelsEndpoints`, preflight
helpers on `EvaluationsEndpoints` and `ModelsEndpoints`, and 14 new
tests in `chap_client/tests/test_client.py`. No dependency on chap-core
changes.

## B. Polling robustness (chap_client + chap-scheduler flow)

These cover the "phantom job id" family. chap-core returns the same
shape for "this job is pending" and "this job doesn't exist", so a
typo'd id makes our flow burn its 10-minute timeout for nothing.

| Finding | What we ship |
|---|---|
| **#7** (`GET /v1/jobs/<bogus>` -> 200 PENDING) | New `chap_client.wait_for_job(job_id, *, timeout, poll)` that combines `job_status` + a "does this id exist?" check. Implementation: on first poll, also call `list_jobs()` and confirm membership. If the id is unknown, raise `ValueError("unknown job id")` immediately. |
| **#22** (logs phantom 200) | Don't expose `client.job_logs` until #7 is fixed; if we do, gate with the same membership check. |
| **#19** (cancel phantom 200) | Don't expose `client.cancel_job` until validated; if we do, refuse on unknown ids client-side. |
| **#21** (TaskRevokedError leak on `*_result`) | Don't expose `evaluation_result` / `prediction_result` -- prefer the working sibling endpoints (`evaluation_entries`, `prediction_entries`). Already what we do. |

Footprint: one helper, one new method, ~30 lines + tests. The
chap-scheduler flow's `wait_for_prediction` calls the new helper
instead of looping `job_status` directly.

## C. Workarounds for chap-core's broken response shapes

| Finding | What we ship |
|---|---|
| **#20** (`evaluation_result`/`prediction_result` 500) | Don't model these in chap_client. They are unusable. Document in CHAP_SPEC_DRIFT.md (already done) and recommend the working analytics-entry endpoints. |
| **#26** (`/df` NaN crash) | Don't model `/df` until upstream fix lands. If we model `/csv` (text response), expose it as `dataset_csv()` returning a string. |
| **#27** (`/csv` and `/df` 500-not-404 on bogus id) | If we model `/csv`, our wrapper should remap the 500-with-"not found" body to `ChapHttpError(status=404, ...)`. Crude but works. |

## D. Documentation in chap_client

For findings that have no client-side mitigation, drop a one-line
note in the affected method's docstring pointing at the drift number.
Keeps the surprises discoverable without rebuilding the world.

| Finding | Where the note lives |
|---|---|
| **#2** (models == configured-models) | `list_models` and `list_configured_models` docstrings. **Done.** |
| **#4** (`name` rewritten to `template:name`) | `create_configured_model` docstring. **Done.** |
| **#8** (`/v1/crud/backtests/{id}` 405) | `get_evaluation` docstring (we use `/info`). **Done.** |
| **#11** (silent upsert) | `create_configured_model` docstring -- add a line. |
| **#12** (silent param ignoring) | `list_jobs` / `list_evaluations` docstrings -- add a line. |
| **#23** (info bigger than full) | `get_evaluation` docstring -- add a line about why we use `/info`. **Done.** |
| **#24** (path-position error) | If/when we model `backtest_overlap`, document the error shape. |
| **#25** (csvFile route shadowing) | If/when we model the CSV upload, document the path. |

## E. Out of repo's scope

| Finding | Why |
|---|---|
| **#9** (no DELETE on cmwds) | Need chap-core to add the handler. We can `archive=True` flag client-side but it's a non-standard hack. |
| **#13** (200 + error body on visualization) | Visualization endpoints aren't modelled; nothing to wrap. |
| **#17** (CORS reflective) | chap-core FastAPI app config. We can't fix from a client. The chap-scheduler default deployment routes via DHIS2's chap-route proxy, which is same-origin and unaffected -- so this is "document and warn" for direct deployments. |
| **#18** (35/65 endpoints missing description) | chap-core spec authoring; we just consume. |
| **#28** -- **#33** | All chap-frontend; chap-scheduler does not interact with the modeling-app. We can't fix from this repo. |

## F. Externalise chap_client (the bigger move)

Once A-D land, the natural next step is what the README already
flags: **move `chap_client/` to its own repo** so it can publish
independently and be consumed by anyone integrating with chap-core,
not just chap-scheduler.

Concrete blockers to address before extraction:

- [ ] **Coverage:** today 19/65 endpoints (29%). Specifically the
      visualisation, v2 services, and debug/metrics blocks are
      unmodelled. External consumers will want at least the
      visualization catalogue (the `/v1/visualization/{kind}-plots/`
      list endpoints, which return useful catalogues, see the
      finding-13 reproduction).
- [ ] **Path-dep -> PyPI:** today `chap-scheduler/pyproject.toml`
      points at `chap_client/` via uv path-dep. After extraction,
      chap-scheduler depends on the published version; chap_client
      gets its own CI / release pipeline.
- [ ] **Versioning + deprecation policy:** today the README says
      "treat the API as experimental". External release means
      semver, a CHANGELOG, and a story for breaking changes when
      chap-core itself ships breaking changes.
- [ ] **CORS / auth shapes:** the package currently assumes either
      direct chap or DHIS2-proxy. If externalised, we should
      document the four common deployment shapes (direct, DHIS2
      proxy, OIDC behind a gateway, k8s ingress with token auth)
      and verify each works with the existing `auth=` parameter.
- [ ] **CHAP_SPEC_DRIFT.md travels with the package** so external
      consumers see the same caveats we do. (It currently lives in
      `chap_client/`, so the move is a `git mv`.)

The "in this repo" actions in A-D should land *before* extraction,
because they make the public API safer / more discoverable, which
is exactly what an external consumer wants.

## G. Recommended ordering

1. **Ship A** (defensive validation): one PR, ~half a day. Makes
   chap_client immediately friendlier.
2. **Ship B** (polling robustness): one PR. Closes the worst of the
   phantom-job-id family on the consumer side.
3. **Ship C and D** (workarounds + docstring notes): one PR
   bundling them.
4. **Coverage sweep**: separate PR adding `list_model_templates()`,
   `delete_evaluation` already done, plus one or two of the
   visualization catalogue endpoints. Bumps coverage to ~35%.
5. **Externalise to its own repo** (per F).
