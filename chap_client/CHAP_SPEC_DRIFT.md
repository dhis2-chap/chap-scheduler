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

### Triage suggestion

If filing by impact:

1. **#17** -- security (reflective CORS + credentials).
2. **#20** -- two endpoints unusable; chap's own `response_model` is broken.
3. **#7, #19, #21, #22** -- the "phantom job id" family. Same fix
   pattern (validate id, return 404) applied in 4 places.
4. **#10** -- 500 instead of 400 on a missing-but-optional field.
5. Everything else can be filed as a sweep / cleanup.
