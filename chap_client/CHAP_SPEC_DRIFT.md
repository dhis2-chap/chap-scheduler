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

## Filing status (2026-05-08)

None of these findings have been filed against
[chap-core](https://github.com/dhis2-chap/chap-core) or chap-frontend
yet. This file is the working set; once a finding is filed upstream,
add the issue / PR link next to its number so we can prune fixes as
they land. Findings 1-4 caught early in the chap_client extraction;
findings 5-6 caught while answering a UX question on PR #25.
