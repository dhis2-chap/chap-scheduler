# Operations

Day-to-day tasks: triggering a run, scheduling, reading the run-report,
rotating credentials, and common troubleshooting. For installation and
quick-start, see the
[README](https://github.com/dhis2-chap/chap-scheduler#quick-start).

## Before triggering a run

The flow needs:

1. A DHIS2 instance reachable from the worker container, with the chap
   bundle installed and at least one **configured-model-with-data-source**
   row registered (chap UI → "Configured models").
2. A `Dhis2Credentials` block instance for that DHIS2 server. Create one
   in the Prefect UI:

    Open <http://127.0.0.1:9090/prefect/blocks/catalog> →
    *DHIS2 Credentials (chap-scheduler)* → **+ Add** → fill in
    `base_url`, `username`, `password` → save with a memorable name like
    `prod-dhis2`.

## Trigger a one-off run

In the Prefect UI:

1. **Deployments** → **dhis2-chap-prediction** → **Run** → **Custom run**.
2. Pick the `Dhis2Credentials` block from the dropdown.
3. (Optional) Set `end_date` if you want to pin the prediction's last
   period to a specific date instead of using the freshness probe.
4. **Submit**.

The run lands in the run list. Click it to see logs and, once it
finishes, the **run-report artifact**.

## Schedules

The flow itself ships **without** a baked-in schedule (see
[Architecture](architecture.md#schedules-are-intentionally-not-baked-in)
for why). Add a cron trigger via the Prefect UI:

1. **Deployments** → **dhis2-chap-prediction** → **Schedules** tab →
   **+ Add Schedule**.
2. Pick **Cron** (or Interval if you prefer), set the cron expression and
   timezone.
3. Click **Edit parameters** on the schedule and pin the
   `Dhis2Credentials` block instance you want this schedule to use. You
   can add multiple schedules to the same deployment, each with its own
   block — e.g. nightly against staging, weekly against production.

The schedule will start firing immediately. Disable it from the same UI.

## Reading the run-report

Every run emits a markdown artifact named `dhis2-chap-prediction-report`.
Open the run in the Prefect UI → **Artifacts** tab. The report contains:

- **DHIS2 system info** (version, server time, instance URL) and **chap
  system info** (chap-core version, Python version, server timezone) —
  pinpoints what the run actually talked to.
- **Per-model section.** For each configured-model-with-data-source the
  flow tried:
    - Status (`succeeded` / `failed`).
    - On failure: which step (`fetch_dhis2`, `submit_prediction`,
      `wait_for_prediction`, …) and the error message.
    - For chap rejections (HTTP 400 with structured detail): the
      per-`(orgUnit, featureName)` "missing values" breakdown grouped by
      reason and time period.
    - On success: prediction id, analytics-row count, org-units covered,
      periods covered, predicted-period list.

The artifact is always written, including when DHIS2 or chap was
unreachable end-to-end (you'll see `dhis2_error` / `chap_error` set
instead of system info).

## Rotating DHIS2 credentials

In the Prefect UI: **Blocks** → click the block → **Edit** → update
`password` → save. The next flow run that uses this block picks up the
new value. No service restart, no env-var rewrite.

## Common troubleshooting

### "All regions rejected due to missing values" on every model

The prediction fired before DHIS2 had data for one or more required
covariates in the most recent period. Check the run-report's rejection
detail — the listed `featureName` and `timePeriods` tell you which
covariate is lagging.

If this is chronic for a covariate (typical for climate data lagging the
disease-cases pipeline), expect the freshness probe to step the end
period back a month or two automatically; the prediction will simply
target an earlier window than "today minus one period".

### `start period after end period`

The configured model's `startPeriod` is later than the end period the
flow resolved (either via probe or `end_date`). This is a configuration
issue on the chap side — the configured model needs a `startPeriod`
that's actually before any plausible end period.

### Prediction stays in `PENDING` / `RUNNING` past the timeout

The flow polls chap's job-status endpoint and gives up after
`CHAP_SCHEDULER_PREDICTION_TIMEOUT_SECONDS` (default in
[`config.py`](https://github.com/dhis2-chap/chap-scheduler/blob/main/src/chap_scheduler/config.py)).
Long-training models may need this raised. Set it in `.env` or as a
container env var.

### Worker registered but no deployment shows up in the UI

Check the worker container's logs. `flow.serve()` registers the
deployment only after the Prefect API is reachable; if the chap-scheduler
service is still starting, the worker retries in a loop. Once
`/prefect/api/health` returns 200 the deployment will appear.

### "Spawning a second Prefect server"

If you see Prefect logging "starting ephemeral server" from the API
container's logs, something in the API process is constructing a Prefect
client without `PREFECT_API_URL` set. This shouldn't happen in the
shipped code — block-type registration is intentionally on the worker
side for exactly this reason. File a bug if you hit it.

## CLI reference

```bash
chap-scheduler --version             # version
chap-scheduler info                  # resolved config (env-driven)
chap-scheduler serve                 # run the FastAPI server
chap-scheduler register-blocks       # register block types against a running API
                                     # (worker container does this automatically;
                                     #  use this command if you serve standalone)
```

All settings come from environment / `.env`, prefixed with
`CHAP_SCHEDULER_`. See
[`.env.example`](https://github.com/dhis2-chap/chap-scheduler/blob/main/.env.example).
