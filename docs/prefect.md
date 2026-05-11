# Prefect primer

The rest of these docs assume a working vocabulary for [Prefect](https://www.prefect.io/).
If you're new to it, this page is the five-minute orientation. It only covers
what chap-scheduler actually uses — Prefect has a much larger surface area.

## Why Prefect at all

We need to run a Python function (the prediction flow) on a schedule, with:

- **Retries and timeouts** without writing them by hand.
- **Per-run history** with parameters, logs, status, and artifacts.
- **A UI** an operator can use to trigger an ad-hoc run, change a schedule,
  or rotate a DHIS2 password — without touching code.
- **Saved credentials** that flows reference by name, instead of being
  templated into env vars or YAML.

Prefect gives all of this out of the box. The alternative — cron + a small
admin UI + a secrets store — is more code than it's worth for a service
whose main job is "call chap once in a while".

## The four concepts you need

### Flows

A **flow** is a Python function decorated with `@flow`. Each call is a *flow
run*: Prefect records start time, parameters, status, logs, and any
artifacts you emit. In this repo the flow lives at
[`flows/dhis2_chap_prediction.py`](https://github.com/dhis2-chap/chap-scheduler/blob/main/src/chap_scheduler/flows/dhis2_chap_prediction.py).

Flows can call **tasks** (`@task`-decorated functions). Tasks get their own
retry policy and logs, and the per-task status shows up in the UI. We
mostly use them as a way to label per-step failures (which step failed?
fetch DHIS2? submit prediction?).

### Blocks

A **block** is a saved, named bag of configuration. The *block type* is a
pydantic model (think: schema); a *block instance* is a saved value the
operator creates in the UI. Flows take a block instance as a parameter.

We ship one block type — `Dhis2Credentials` — holding base URL, username,
and a `SecretStr` password. Operators create one instance per DHIS2 server
and pick it from a dropdown when triggering a run. Rotating credentials is
"edit the block, save"; the next flow run picks up the new value.

### Deployments

A **deployment** is a flow that has been registered with the Prefect server
along with metadata (entrypoint, parameter defaults, tags, schedules, work
pool). It's what makes a flow runnable from the UI or by a schedule, as
opposed to "a Python function on someone's laptop".

In chap-scheduler the worker container creates the deployment by calling
`flow.serve()` — see the [Architecture page](architecture.md) for how that
fits together.

### Work pools and workers

A **work pool** is a queue of flow runs waiting to be executed. A
**worker** subscribes to a pool, picks up runs, and executes them.

`flow.serve()` is the simplified path: it bundles "create the deployment"
and "be the worker" into one process. We use that. The worker container
in `compose.yml` runs `flow.serve()` against the embedded Prefect API; it
both registers the deployment on startup and polls for new runs.

## What about the UI

The Prefect UI is mounted at `/prefect/` on the chap-scheduler service.
With the default `compose.yml`:

- UI: <http://127.0.0.1:9090/prefect/>
- API: <http://127.0.0.1:9090/prefect/api>

In the UI you can:

- Trigger a one-off flow run (pick a `Dhis2Credentials` block from the
  dropdown; optionally pick an `end` mode -- `calculated` / `fixed` /
  `offset` -- and/or set `configured_model_id`).
- Browse past runs, read their logs, and view the **run-report
  artifact** each run emits.
- Add or change a cron schedule on the deployment.
- Edit a `Dhis2Credentials` block (rotate password, change URL).

!!! warning "The UI is unauthenticated"

    The embedded Prefect UI has no auth. `compose.yml` binds the host port
    to `127.0.0.1` only. If you need to expose it, put a reverse proxy
    with auth in front. See the README's "Network exposure" note.

## What we deliberately don't use (yet)

- **Async tasks.** Our tasks are sync. Prefect runs them in a worker thread
  under the sync runner, which means `time.sleep` blocks the worker thread
  but not the engine event loop. Good enough for a slow polling loop
  against chap.
- **Concurrency limits, work-queue priorities, custom result storage.**
  All defaults. The flow runs once per schedule tick; there's no fan-out
  to limit, and result storage isn't a feature we surface today.
- **Baked-in schedules.** The flow itself ships without a `cron=...`.
  Operators add cron triggers via the UI per deployment so the same
  flow can run on different cadences against different `Dhis2Credentials`
  blocks. See [Operations](operations.md#schedules).

## Further reading

- Prefect docs: <https://docs.prefect.io/>
- Concepts (flows, tasks, deployments, work pools):
  <https://docs.prefect.io/v3/concepts/>
- Blocks: <https://docs.prefect.io/v3/concepts/blocks/>
