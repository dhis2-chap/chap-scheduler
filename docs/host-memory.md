# Host memory pressure when running the full local stack

If macOS panics or hard-reboots while running the chap-scheduler +
chap-core + DHIS2 stacks together (3 crashes in one session was the
trigger for this note), the cause is almost always **memory pressure
from chap-core's INLA-based predictors**, not a runaway loop in the
chap-scheduler / chap_client code. This file is the diagnosis +
mitigations checklist.

## Confirming it's memory pressure (not something else)

Run while the stacks are up and a prediction is mid-flight (or right
after macOS recovers from a panic):

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}"
```

Idle baseline (all stacks up, nothing running):

```text
NAME                                     MEM USAGE / LIMIT
dhis2-docker-postgresql-1                4.2 GiB / 15.6 GiB
dhis2                                    2.7 GiB / 5 GiB    (55%)
chap-core-worker-1                       1.5 GiB / 15.6 GiB (NO LIMIT)
chap                                     408 MiB / 15.6 GiB
chap-core-ewars-1                        170 MiB / 15.6 GiB
chap-scheduler-chap-scheduler-1          192 MiB / 15.6 GiB
... (small)
total: ~10 GiB
```

The headline numbers to watch:

- **`chap-core-worker-1`** runs the actual model code (INLA / R for
  EWARS, torch for the deep models). Its compose entry has no
  `mem_limit:`, so Docker reports the full 15.6 GiB allocation as the
  ceiling. During an INLA EWARS run on the seeded Lao PDR dataset
  (18 provinces × 24 months, 4 covariates), it routinely climbs to
  **5-8 GiB**. With a smaller dataset 2-3 GiB.
- **`chap-core-ewars-1`** sits at ~170 MiB idle but during the actual
  R model invocation can spike to **4-6 GiB**. Same memory band as
  the worker -- they're often hot at the same time.
- **`dhis2-docker-postgresql-1`** at 4 GiB and **`dhis2`** at 2.7 GiB
  are stable -- not the spikers. But they consume real RAM that's no
  longer available to a hot chap run.

When two of those three spike together (worker + ewars during a
chapkit-EWARS evaluation), the docker side alone is **12-16 GiB**.
Add Docker Desktop's own overhead (~1 GiB), DHIS2 + Postgres steady
~7 GiB, and your Mac's real apps (browser, IDE, ...), and 32 GiB hosts
swap-thrash, 16 GiB hosts hard-panic.

If `vm_stat` shows `Pages free` near zero and `Pages purged` climbing
fast, the kernel is paging hard -- the panic is incoming.

## Mitigations, in order of cost

### 1. Cap chap-core-worker (one-line compose change)

In `chap-core/compose.yml`, find the `worker` service and add a
`mem_limit:`. Pick a value that fits comfortably alongside DHIS2 +
chap-scheduler + your real apps -- on a 16 GiB host, **6g** is the
ceiling; on a 32 GiB host, **8g** is generous.

```yaml
services:
  worker:
    # ... existing config ...
    mem_limit: 6g
    memswap_limit: 6g  # disable swap-into-disk; OOM-kill the worker instead of panicking the host
```

The same goes for `ewars` if you've added the chapkit overlay
(`compose.ewars.yml`):

```yaml
services:
  ewars:
    mem_limit: 5g
    memswap_limit: 5g
```

The trade-off: a real prediction job that genuinely needs >6 GiB will
get OOM-killed by Docker, surfacing in chap-core as a job in
`FAILURE` state. That's a clean failure -- a panicked Mac is not.

### 2. Don't run chap-core when you're not predicting

The single biggest win. chap-core's worker + redis + ewars containers
are heavy and most of the time you're working on
chap-scheduler's flow code, not the actual ML run. Bring chap-core up
only when you're about to e2e:

```bash
# Stop chap-core when you switch to scheduler / chap_client work.
( cd chap-core && docker compose down )

# Bring it back up only when about to run an e2e.
( cd chap-core && docker compose up -d )
# ~30 seconds to ready (the postgres healthcheck dominates).
```

DHIS2 + chap-scheduler stay up because they're cheap (steady
~3-3.5 GiB combined and don't spike).

### 3. Cap Docker Desktop's total RAM

Docker Desktop -> Settings -> Resources -> Memory. Default on Macs
is *half of host RAM* (32 GiB on a 64 GiB Mac), which is too much
when DHIS2 alone wants 5 GiB and chap-core another 8.

**Recommended:** set Docker Desktop to **12-16 GiB on a 16 GiB host**,
**18-24 GiB on a 32 GiB host**, **28-36 GiB on a 64 GiB host**. Leaves
your real apps headroom. Docker won't grow past this even if chap
goes wild, so the worst case is "chap job dies" rather than "Mac
reboots".

### 4. Stop pgadmin4 / unused sidecars

`dhis2-docker-pgadmin4-1` (260 MiB), `chap-core-redis-1` (17 MiB),
`chap-scheduler-postgres-1` (70 MiB) are all small but they sum.
Stop the ones you're not using:

```bash
docker stop dhis2-docker-pgadmin4-1
```

Worth at most ~300 MiB total -- only a fix if you're already at 99%.

## What it isn't (and how I checked)

- **Not an unbounded loop in this repo.** The only `while True:` in
  the codebase is `chap_client.ChapClient.wait_for_job`, and it has a
  three-pronged guard: membership check before entering, deadline
  with `TimeoutError`, and the right transient-status set checked
  case-insensitively. See [`loop-safety.md`](loop-safety.md) for the
  full rule.
- **Not chap-scheduler's flow.** The flow's tasks are all bounded:
  `_enumerate_periods` has a 120-period cap; `wait_for_prediction`
  delegates to `wait_for_job`; analytics / org-unit fetches are
  one-shot HTTP gets.
- **Not Prefect.** The embedded Prefect server is steady at ~190 MiB
  in this stack and does not spike during model runs.

The smoking gun is always in `docker stats` -- if `chap-core-worker-1`
or `chap-core-ewars-1` is climbing past 6 GiB while you watch,
that's the run that's about to take your Mac down. Mitigation #1
caps that.
