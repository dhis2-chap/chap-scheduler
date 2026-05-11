# CHAP issues — source-mapped fix locations

Cross-references each numbered item in [`CHAP_SPEC_DRIFT.md`](./CHAP_SPEC_DRIFT.md) to the source location where the fix would land. Paths prefixed `chap-core/` are relative to `../chap-core/` (sibling of this repo); paths prefixed `chap-frontend/` are relative to `../chap-frontend/`.

> NOTE: file:line numbers were captured against chap-core HEAD on 2026-05-11; line numbers will drift as chap-core / chap-frontend evolve. Re-grep the symbol name if a line moves.

Tag legend:
- `[trivial]` — change a type annotation, add a single validator, swap a status code, single-string edit
- `[medium]` — refactor a handler, change response shape, add an exception handler, shared util / multi-site UI fix
- `[hard]` — schema migration, breaking API change, security-CORS rewrite, hosting / routing rework
- `[upstream]` — needs a fix to a chap-core dependency (e.g. celery result model)

---

## 1. `ModelSpecRead.target` / `.covariates` typed `string` but wire is object `[trivial]`

- **Pydantic model**: `chap_core/database/model_spec_tables.py:34` — `class ModelSpecRead(ModelSpecBase)` with `target: FeatureType` (line 37) and `covariates: list[FeatureType]` (line 36).
- **Wire-shape construction**: `chap_core/database/database.py:249-275` in `SessionWrapper.get_configured_models()` (the `model["target"] = {...}` and `model["covariates"] = [...]` substitution).
- **Fix**: the runtime payload is fine; the OpenAPI spec emitter is wrong because `FeatureType` is a SQLModel `table=True` that pydantic doesn't always serialise as an object schema. Either (a) introduce a plain pydantic `FeatureRef` (use `chap_core/database/feature_tables.py:13` `FeatureTypeRead`) and re-type `ModelSpecRead.target: FeatureTypeRead`, `.covariates: list[FeatureTypeRead]`, or (b) add an explicit `model_config = ConfigDict(json_schema_extra=...)` so the emitted schema matches the wire.

## 2. `/v1/crud/models` and `/v1/crud/configured-models` return the same data `[trivial]`

- **Routes**: `chap_core/rest_api/v1/routers/crud.py:741 list_models()` and `:577 list_configured_models()`. `list_models` is literally `return list_configured_models(session)`.
- **Fix**: either deprecate one (mark the decorator `deprecated=True`), add a `description=` clarifying that they are aliases, or actually split them. Same applies to the POST aliases at `crud.py:747 add_model`.

## 3. `POST /v1/crud/configured-models` 500s on bogus `modelTemplateId` `[trivial]`

- **Route handler**: `chap_core/rest_api/v1/routers/crud.py:607-625` — `add_configured_model()`.
- **The bare assert that leaks**: `chap_core/database/database.py:163` — `assert model_template is not None, f"Model template with id {model_template_id} not found"` inside `SessionWrapper.add_configured_model()`.
- **Fix**: in the handler at `crud.py:617` already does `session.exec(select(ModelTemplateDB)...).first()` — short-circuit with `raise HTTPException(404, "Model template not found")` when `template is None`, before calling into `session_wrapper.add_configured_model`. Also swap the `assert` in database.py:163 for a typed exception so other call sites get the same treatment.

## 4. `POST /v1/crud/configured-models` mutates the supplied `name` `[trivial]`

- **Name-rewrite site**: `chap_core/database/database.py:166-172` in `SessionWrapper.add_configured_model()` — `name = f"{template_name}:{configuration_name}"`.
- **Fix**: either document the prefix behaviour with a `description=` on `crud.py:607 add_configured_model` and on the `ModelConfigurationCreate.name` field at `chap_core/rest_api/data_models.py:154`, or split namespace from supplied name (introduce explicit `namespace: str` on `ModelConfigurationCreate`).

## 5. Evaluation `modelId` is unvalidated free-form string `[medium]`

- **Request model**: `chap_core/rest_api/data_models.py:97-100` — `class MakeBacktestRequest(BacktestParams)` (`model_id: str`).
- **Response model**: `chap_core/rest_api/data_models.py:65-72` — `class BacktestCreate(BacktestBase)` and the underlying column `model_id: str` at `chap_core/database/tables.py:19`.
- **Route**: `chap_core/rest_api/v1/routers/analytics.py:328-340` — `create_backtest()`. Also the CRUD path `chap_core/rest_api/v1/routers/crud.py:320-336`.
- **Fix**: in `analytics.py:329` add a session dependency and resolve `request.model_id` against `ConfiguredModelDB.name` (or accept `int` via `SessionWrapper.get_configured_model_by_id_or_name`); raise 404 synchronously. Or rename to `configured_model_name` on `MakeBacktestRequest`.

## 6. `datasetId` on `create-backtest` is also unvalidated `[trivial]`

- **Request model**: `chap_core/rest_api/data_models.py:97-100` — `MakeBacktestRequest.dataset_id: int`.
- **Route**: `chap_core/rest_api/v1/routers/analytics.py:328-340` — `create_backtest()`.
- **Fix**: in the handler, `session.get(DataSet, request.dataset_id)` (mirrors `crud.py:466 get_dataset`) and raise 404 before queuing. Same treatment in the CRUD POST `crud.py:321 create_backtest`.

## 7. `GET /v1/jobs/{id}` returns 200 `"PENDING"` for unknown ids `[medium]`

- **Route**: `chap_core/rest_api/v1/jobs.py:59-63` — `get_job_status()`.
- **Underlying bug**: `chap_core/rest_api/celery_tasks.py:264-265` — `CeleryJob.status` returns `AsyncResult.state` which is `"PENDING"` for any unknown task id (Celery design). The handler does not consult Redis (`r.hkeys(f"job_meta:{job_id}")`) first.
- **Fix**: in `jobs.py:60`, look the id up in `job_meta:*` via the existing `get_job_meta(job_id)` at `celery_tasks.py:382`; raise `HTTPException(404)` when no metadata exists.

## 8. `GET /v1/crud/backtests/{id}` returns 405 `[trivial]`

- **Routes**: only `chap_core/rest_api/v1/routers/crud.py:267 /backtests/{backtestId}/full` and `:275 /backtests/{backtestId}/info` exist; the bare `GET /backtests/{backtestId}` is not registered. The PATCH at `:351` and DELETE at `:339` match the method-not-allowed semantics.
- **Fix**: add `@router_get("/backtests/{backtestId}", ...)` near `crud.py:267` aliasing `get_backtest_info`, or add a stub returning 404 with a hint pointing at `/info` and `/full`.

## 9. `DELETE /v1/crud/configured-models-with-data-source/{id}` returns 405 `[medium]`

- **Routes present**: `chap_core/rest_api/v1/routers/crud.py:646 list_configured_models_with_data_source` (GET list), `:662 get_configured_model_with_data_source` (GET one), `:691 create_configured_model_with_data_source_from_backtest` (POST). No DELETE handler.
- **Fix**: add a `@router.delete("/configured-models-with-data-source/{configuredModelWithDataSourceId}")` near `crud.py:691` mirroring `:443 delete_prediction` (soft delete if Predictions reference cmwds rows).

## 10. `POST /v1/crud/configured-models` 500s when `userOptionValues` is omitted `[trivial]`

- **Request model**: `chap_core/rest_api/data_models.py:153-157` — `class ModelConfigurationCreate(DBModel)` with `user_option_values: dict | None = None`.
- **Crash site**: `chap_core/database/model_templates_and_config_tables.py:92-110` — `ConfiguredModelDB._validate_model_configuration` calls `jsonschema.validate(instance=None, ...)` which raises `ValueError("Invalid user options: None is not of type 'object'")`.
- **Fix**: change `data_models.py:156` to `user_option_values: dict = Field(default_factory=dict)` so the default `{}` reaches the validator. Alternative: coerce `None -> {}` at `ConfiguredModelDB._validate_model_configuration` (model_templates_and_config_tables.py:92).

## 11. `POST /v1/crud/configured-models` is silently idempotent on `(name, modelTemplateId)` `[medium]`

- **Upsert site**: `chap_core/database/database.py:174-178` — `SessionWrapper.add_configured_model()` checks `existing_configured` and returns its id.
- **Route**: `chap_core/rest_api/v1/routers/crud.py:607-625` — `add_configured_model()`.
- **Fix**: in `database.py:176-178`, raise instead of returning the existing id (caller in `crud.py:619` then turns it into a `409`). Or, in the route, query for an existing `name + model_template_id` first and `raise HTTPException(409)`.

## 12. List endpoints silently ignore unknown query params `[medium]`

- **Routes**: `chap_core/rest_api/v1/jobs.py:22-45 list_jobs` already takes `ids`, `status`, `type` query params (filters defined here), but no `limit` / `offset`. CRUD list endpoints `chap_core/rest_api/v1/routers/crud.py:253 get_backtests`, `:420 get_predictions`, `:459 get_datasets` take no query params at all.
- **Fix**: add `limit: int = Query(None, gt=0)` / `offset: int = Query(0, ge=0)` to each list endpoint (`crud.py:253, 420, 459`). FastAPI rejects unknown params by default only with a custom dependency, so additionally consider adding a Pydantic-`extra="forbid"` query model.

## 13. Visualization endpoints return 200 with `{"error": ...}` body on missing ids `[medium]`

- **Routes**: `chap_core/rest_api/v1/routers/visualization.py`:
  - `:79-98 generate_visualization` (returns `{"error": ...}` at lines 85, 88, 91)
  - `:115-127 generate_data_plots` (lines 120, 124)
  - `:144-156 generate_backtest_plots` (lines 149, 153)
- **Fix**: replace every `return {"error": "..."}` with `raise HTTPException(status_code=404, detail="...")` (or 400 for the metric-not-found cases).

## 14. `POST /v1/analytics/create-backtest` accepts empty `name` `[trivial]`

- **Request model**: `chap_core/rest_api/data_models.py:97-100` — `MakeBacktestRequest.name: str`.
- **Same family**: `:103-105 MakeBacktestWithDataRequest.name`, `:93-94 MakePredictionRequest.name` (inherits via `DatasetMakeRequest`), `:37-40 DatasetMakeRequest.name`.
- **Fix**: change `name: str` to `name: str = Field(min_length=1)` on each of those request models. (For `MakeBacktestWithDataRequest` and `MakePredictionRequest`, the underlying `DataSetCreateInfo.name` at `chap_core/database/dataset_tables.py` is the actual field.)

## 15. `POST /v1/analytics/create-backtest` accepts negative `nPeriods` `[trivial]`

- **Field defaults**: `chap_core/api_types.py:83-86` — `class BacktestParams(DBModel)` with `n_periods: int = 3`, `n_splits: int = 7`, `stride: int = 1`.
- **Fix**: add `Field(gt=0)` to each of the three fields. `MakeBacktestRequest` and `MakeBacktestWithDataRequest` inherit from `BacktestParams` so the fix propagates. Also `PredictionParams.n_periods` at `chap_core/rest_api/data_models.py:47-49`.

## 16. POST endpoints silently ignore unknown top-level fields `[trivial]`

- **Base model**: `chap_core/database/base_tables.py` — `DBModel` (all request models inherit from this or `BaseModel`). Confirm `model_config` is not already set to `extra="forbid"`.
- **Fix**: set `model_config = ConfigDict(extra="forbid")` on the mutating request models specifically (read models stay permissive): `MakeBacktestRequest`, `MakeBacktestWithDataRequest`, `MakePredictionRequest`, `MakePredictionWithDataSourceRequest`, `ModelConfigurationCreate`, `PredictionCreate`, `DatasetCreate`, `DatasetMakeRequest`, `BacktestUpdate` — all in `chap_core/rest_api/data_models.py` and `chap_core/rest_api/v1/routers/analytics.py:374` (`MakePredictionWithDataSourceRequest`).

## 17. CORS reflects any Origin with `allow-credentials: true` `[hard]`

- **CORS config**: `chap_core/rest_api/app.py:33-45` — `origins = ["*", "http://localhost:3000", "localhost:3000"]` combined with `allow_origins=origins` (which Starlette treats as wildcard reflection when `*` is present) and `allow_credentials=True`.
- **Fix**: replace `origins` with an explicit allowlist sourced from env (e.g. `CHAP_CORS_ORIGINS` split by comma); drop the `"*"` entry. Or set `allow_credentials=False` and require explicit bearer auth. Starlette's CORS docs explicitly flag reflective `*` + credentials as unsafe.

## 18. 35 of 65 endpoints have no `description` in OpenAPI `[medium]`

- **Surface**: every `@router.get/post/...` decorator across `chap_core/rest_api/v1/routers/{crud,analytics,visualization}.py`, `chap_core/rest_api/v1/jobs.py`, and `chap_core/rest_api/common_routes.py`. Many use only `tags=[...]` with no `description=`.
- **Fix**: add `description="..."` to each decorator. Bulk sweep, no logic changes. Mechanical.

## 19. `POST /v1/jobs/<bogus>/cancel` returns 200 `[medium]`

- **Route**: `chap_core/rest_api/v1/jobs.py:84-103` — `cancel_job()`. The `if job is None` at line 90 never fires because `worker.get_job` at `chap_core/rest_api/celery_tasks.py:334` always returns a `CeleryJob` (it wraps `AsyncResult(task_id)` which never returns None for unknown ids).
- **Fix**: same pattern as #7 — consult `get_job_meta(job_id)` at `celery_tasks.py:382` and raise 404 when the id has no Redis metadata. Then `worker.get_job(job_id)` is only called for known ids.

## 20. `/v1/jobs/{id}/evaluation_result` and `/prediction_result` return 500 on real success jobs `[upstream]`

- **Routes**: `chap_core/rest_api/v1/jobs.py:115-122` — `get_prediction_result()` (line 117) and `get_evaluation_result()` (line 121). Both `return cast("...", _get_successful_job(job_id).result)`.
- **Response models**: `chap_core/rest_api/data_models.py:27-29 FullPredictionResponse` and `chap_core/api_types.py:99-101 EvaluationResponse`.
- **Working sibling**: `jobs.py:129-132 get_database_result` wraps `result` in `DataBaseResponse(id=result)` (line 125 defines the wrapper). The broken pair returns the bare int directly.
- **Fix**: either change the handlers to wrap the result the way `get_database_result` does (and update the `response_model`), or remove the `-> FullPredictionResponse` / `-> EvaluationResponse` annotations (FastAPI then skips response validation). The current return is a bare prediction-id integer; the declared `response_model` is dict-shaped. Tagged `[upstream]` because the worker tasks (`chap_core/rest_api/worker_functions.py`) determine what `_get_successful_job().result` actually is.

## 21. `/v1/jobs/<bogus>/{database,evaluation,prediction}_result` returns 500 with `TaskRevokedError` leaked `[medium]`

- **Routes**: same three at `chap_core/rest_api/v1/jobs.py:115, 120, 129`. The shared helper is `jobs.py:48-56 _get_successful_job()`.
- **Source of the leak**: `_get_successful_job` at `jobs.py:49` does `worker.get_job(job_id)` (`celery_tasks.py:334`), which returns a `CeleryJob` whose `.result` (at `celery_tasks.py:267-269`) is `AsyncResult.result` — and for an unknown task that is a `TaskRevokedError` instance. The handler then tries to validate that object against `DataBaseResponse`.
- **Fix**: gate `_get_successful_job` on `get_job_meta(job_id)` (same as #7/#19); raise 404 before touching Celery. Bonus: catch `TaskRevokedError` in `_get_successful_job` and re-raise as `HTTPException(404)`.

## 22. `/v1/jobs/<bogus>/logs` returns 200 with empty string `[trivial]`

- **Route**: `chap_core/rest_api/v1/jobs.py:106-112 get_logs()`. Calls `job.get_logs()` at `celery_tasks.py:299-316` which falls through to returning `self.exception_info` (empty string for unknown ids).
- **Fix**: same membership check as #7 / #19 / #21 — raise 404 when `get_job_meta(job_id)` is empty.

## 23. `/info` and `/full` on backtests have unrelated, partially-overlapping shapes `[medium]`

- **Routes**: `chap_core/rest_api/v1/routers/crud.py:267-272 get_backtest` (`/full`, `response_model=Backtest`) and `:275-287 get_backtest_info` (`/info`, `response_model=BacktestRead`).
- **Models**: `Backtest` at `chap_core/database/tables.py:39` (has `model_db_id` at line 45 but does NOT eager-load `configured_model` / `dataset` relationships); `BacktestRead` at `chap_core/database/tables.py:85-88` (adds `dataset: DataSetMeta`, `configured_model: ConfiguredModelRead | None`).
- **Fix**: either (a) make `/full` actually a superset by switching its `response_model` to a new `BacktestFull` (already defined at `chap_core/rest_api/data_models.py:75-77` as `BacktestRead + metrics + forecasts`) — and update the handler at `crud.py:267` to eager-load relations, or (b) rename `/full` to `/raw` to reflect that it returns the raw DB row.

## 24. `/v1/analytics/backtest-overlap/{a}/{b}` error message uses path position, not id `[trivial]`

- **Route**: `chap_core/rest_api/v1/routers/analytics.py:212-227 get_backtest_overlap`. The offending lines: `:222` (`"Backtest 1 not found"`) and `:224` (`"Backtest 2 not found"`).
- **Fix**: change to `f"Backtest {backtest_id1} not found"` / `f"Backtest {backtest_id2} not found"`. Two-line edit.

## 25. Route ordering: `/v1/crud/datasets/csvFile` shadowed by `/v1/crud/datasets/{datasetId}` `[trivial]`

- **Routes (registration order matters)**: `chap_core/rest_api/v1/routers/crud.py`:
  - `:465 get_dataset` (`@router.get("/datasets/{datasetId}")`) — registered first
  - `:493 create_dataset_csv` (`@router.post("/datasets/csvFile")`) — registered after
- **Fix**: move the `csvFile` decorator above `get_dataset` (FastAPI matches in registration order). One-block reorder.

## 26. `GET /v1/crud/datasets/{id}/df` 500s on any dataset containing `NaN` `[trivial]`

- **Route**: `chap_core/rest_api/v1/routers/crud.py:511-521 get_dataset_df`. The crash is at line 521 (`return df.to_dict(orient="records")`) — pandas `NaN` floats are not JSON-serialisable.
- **Fix**: between lines 520 and 521, replace NaN with None: `df = df.where(df.notna(), None)` (or `df.replace({np.nan: None})`). Note: the sibling `get_dataset` route at `crud.py:466-474` already scrubs NaN at line 473 (`obs.value if obs.value is None or np.isfinite(obs.value) else None`); the same fix-pattern applies.

## 27. CSV / DF dataset endpoints return 500 (not 404) for unknown ids `[trivial]`

- **Routes**: `chap_core/rest_api/v1/routers/crud.py:511 get_dataset_df` and `:524 get_dataset_csv`. Both call `SessionWrapper.get_dataset(dataset_id)` at `:517` and `:527`, which raises `ValueError("Dataset with id X not found")` for unknown ids — leaked to the global handler at `chap_core/rest_api/app.py:50-61` as 500.
- **Fix**: add `dataset = session.get(DataSet, dataset_id); if dataset is None: raise HTTPException(404, "Dataset not found")` at the top of both handlers (mirror the pattern from `crud.py:466-470`). Alternative: change `SessionWrapper.get_dataset` to raise `HTTPException(404)` directly.

---

# chap-frontend findings

Items 28-33 live in the chap-frontend (modeling-app) React SPA, not chap-core. Paths below are relative to `../chap-frontend/`.

## 28. "Report a bug" link goes to `example@example.com` `[trivial]`

- **Render site**: `chap-frontend/apps/modeling-app/src/features/common-features/InfoAboutReportingBugs/InfoAboutReportingBugs.tsx:38` — `<a href="mailto:example@example.com?subject=Modeling App | Issue%20Report:...">chap@dhis2.org</a>` inside the alpha-warning banner. The href and the visible link text disagree; the banner copy itself (lines 34-36) is hardcoded English with no i18n catalogue entry.
- **Fix**: replace `example@example.com` in the `href` with the real chap-team intake address (the link text already says `chap@dhis2.org`, so the simplest patch is `mailto:chap@dhis2.org`). Single-character file edit.

## 29. Empty-name evaluations: blank cell on Evaluations list vs "Unnamed" on Jobs page `[medium]`

- **Evaluations cell (blank)**: `chap-frontend/apps/modeling-app/src/components/BacktestsTable/BacktestsTable.tsx:63-75` — `columnHelper.accessor('name', ...)` renders `info.getValue()` inside a `<Link>` with no fallback, so an empty name renders an empty clickable string.
- **Jobs cell ("Unnamed")**: `chap-frontend/apps/modeling-app/src/components/JobsTable/JobsTable.tsx:52-55` — `columnHelper.accessor('name', ...)` also renders the raw value, but chap-core's celery layer substitutes `"Unnamed"` server-side at `chap-core/chap_core/rest_api/celery_tasks.py:152` and `:372`, so the Jobs page gets a placeholder while the Evaluations page does not.
- **Fix**: in `BacktestsTable.tsx:66-74`, fall back to `i18n.t('Untitled')` (the key already exists — see `chap-frontend/apps/modeling-app/i18n/en.pot:47`) or to `"Unnamed"` for consistency with the celery placeholder. Extract a shared `displayName(name)` helper if it grows. Server-side root cause is #14 (`name: str = Field(min_length=1)`).

## 30. Unresolved `modelId` renders inconsistently across pages `[medium]`

- **Evaluations cell (numeric id fallback)**: `chap-frontend/apps/modeling-app/src/components/BacktestsTable/BacktestsTable.tsx:80-94` — accessor uses `configuredModel.id`, looks up via `models?.find(m => m.id === configuredModelId)`, returns `model?.displayName || configuredModelId`. Unresolved rows show the numeric configured-model id.
- **Predictions cell (raw name string fallback)**: `chap-frontend/apps/modeling-app/src/components/PredictionsTable/PredictionsTable.tsx:54-63` — accessor uses `modelId`, looks up via `models?.find(m => m.name === modelId)`, returns `model?.displayName || modelId`. Unresolved rows show the raw chap-core model name.
- **Fix**: consolidate the resolver — both tables should use the same key (the Predictions-style `name` is the more useful fallback) and the same helper. Server-side root cause is #5 (validate `modelId` at submission so unresolved rows become rare).

## 31. Jobs UI labels chap's standard endpoint as `Create evaluation (Legacy)` `[trivial]`

- **Label site**: `chap-frontend/apps/modeling-app/src/components/JobsTable/TableCells/JobTypeCell.tsx:5` — `[JOB_TYPES.BACKTEST]: i18n.t('Create evaluation (Legacy)')`. `JOB_TYPES.BACKTEST` is the string `'create_backtest'` (the chap-core standard endpoint), defined at `chap-frontend/apps/modeling-app/src/hooks/useJobs.ts:14`.
- **i18n string**: `chap-frontend/apps/modeling-app/i18n/en.pot:269` — `msgid "Create evaluation (Legacy)"`. The non-suffixed `"Create evaluation"` already exists at `:257`.
- **Fix**: change `JobTypeCell.tsx:5` to `i18n.t('Create evaluation')` (drop the `(Legacy)` suffix). Remove the orphaned `msgid` from `en.pot` on the next Transifex sync. There is no v2 endpoint that makes `create_backtest` legacy; the suffix is misleading.

## 32. Page heading "Active jobs" but the table lists every job state `[trivial]`

- **Heading site**: `chap-frontend/apps/modeling-app/src/pages/JobsPage/JobsPage.tsx:9-10` — `pageTitle={i18n.t('Active jobs')}` plus description `'View and manage currently running jobs and their status.'`. The underlying `<JobsTable>` (`chap-frontend/apps/modeling-app/src/components/JobsTable/JobsTable.tsx:125`) renders all jobs unfiltered; there's a Status filter (`JobsTableFilters`) but no default applied.
- **i18n string**: `chap-frontend/apps/modeling-app/i18n/en.pot:1489` — `msgid "Active jobs"`.
- **Fix**: either rename the heading to `i18n.t('Jobs')` (matches the nav item and the actual content), or default the Status filter in `useJobsTableFilters` to `PENDING|STARTED` with a "show all" toggle. The single-string rename is the simpler fix.

## 33. Bookmarking `index.html#/...` URLs returns a DHIS2-level 404 `[hard]`

- **Routing setup**: `chap-frontend/apps/modeling-app/src/App.tsx:54` — the app uses `createHashRouter` (imported at `:2`), so all in-app routes are hash-based (`#/...`). React Router itself handles `#/jobs` correctly.
- **DHIS2 app shell config**: `chap-frontend/apps/modeling-app/d2.config.js:1-22` — no `launchPath` is set; DHIS2 derives the entry from `entryPoints.app: './src/App.tsx'`. The DHIS2 app shell's URL-rewrite layer accepts the bare slug (`/apps/dhis2-chapmodeling-app/#/jobs`) but not the `index.html` form (`/apps/dhis2-chapmodeling-app/index.html#/jobs`).
- **Container nginx**: `chap-frontend/apps/modeling-app/nginx.conf:8-11` — has the standard SPA fallback (`try_files $uri /index.html`) so this is not where the 404 originates; the 404 page is rendered by the DHIS2 server in front of the app, not by the app's own nginx.
- **Fix**: no single source-code line owns this. Either (a) add an explicit `launchPath` to `d2.config.js` that the DHIS2 app shell will accept in both forms, (b) document the bare-slug form as the canonical bookmark URL in the modeling-app readme, or (c) raise a DHIS2-platform ticket — the app-shell URL rewrite is upstream of chap. Tagged `[hard]` because the actual fix may require coordination with the DHIS2 app-platform team rather than a one-file edit.
