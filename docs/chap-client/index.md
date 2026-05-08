# chap-client

Python HTTP client for the chap REST API.

The package is a sibling of `chap-scheduler` in this repo, wired in
via a uv path-dep. It will eventually move to its own repo and
publish independently — until then, treat the API as **experimental**:
shapes can shift between merges.

## Install (today)

`chap-client` is not yet on PyPI. From a checkout of this repo:

```bash
uv sync  # installs chap-client editable from chap_client/
```

Or as a path-dep in your own project:

```toml
dependencies = ["chap-client"]

[tool.uv.sources]
chap-client = { path = "../chap-scheduler/chap_client", editable = true }
```

## Two construction shapes

`ChapClient` is constructed with primitives so it has no opinion about
where credentials come from:

```python
ChapClient(base_url, auth, *, route_prefix="", ...)
```

### Direct against chap

If you have chap reachable on its own port (e.g. `localhost:8000`):

```python
from chap_client import ChapClient

with ChapClient(base_url="http://localhost:8000") as client:
    info = client.system_info()
    print(info.chap_core_version)
```

Pass `auth=httpx.Auth(...)` or `auth=("user", "pw")` if your chap
deployment requires authentication.

### Via DHIS2's chap-route proxy

When chap is exposed through DHIS2 (the chap-scheduler default), use
`route_prefix="/api/routes/chap/run"` and DHIS2 basic auth:

```python
from chap_client import ChapClient

with ChapClient(
    base_url="https://dhis.example.org",
    auth=("admin", "district"),
    route_prefix="/api/routes/chap/run",
) as client:
    info = client.system_info()
```

Inside chap-scheduler, the `Dhis2Credentials.chap_client()` method
wires that shape automatically:

```python
from chap_scheduler.blocks.dhis2 import Dhis2Credentials

creds = Dhis2Credentials.load("local-dhis2")
with creds.chap_client() as client:
    info = client.system_info()
```

## Pages in this section

- **[Endpoints](endpoints.md)** — every chap REST endpoint we model,
  with curl + Python examples. Read this first if you're trying to
  figure out what chap can do or why it returned what it returned.
- **[API reference](api.md)** — auto-generated reference for the
  `ChapClient` class, request / response models, and exceptions.

## Behaviour you should know

- **Connection pooling.** A single `httpx.Client` is held for the
  lifetime of each `ChapClient` instance. Use it as a context manager
  so the pool is closed cleanly.
- **Retries.** GET / HEAD retry on transient transport errors and 5xx
  responses with exponential backoff + jitter, default 3 attempts.
  POST is **never** retried — chap's mutating endpoints aren't
  idempotent and a retry on a connection blip would risk a duplicate.
  Disable retries for tests with `max_attempts=1`.
- **Errors.** Every non-2xx response raises `ChapHttpError`, carrying
  `method`, `path`, `status`, and the parsed (or raw) response body
  as `detail`.

## chap-core spec drift

While integrating chap_client we've found a handful of cases where
chap-core's actual behaviour differs from its OpenAPI spec, or is
non-obvious. Running notes:
[`chap_client/CHAP_SPEC_DRIFT.md`](https://github.com/dhis2-chap/chap-scheduler/blob/main/chap_client/CHAP_SPEC_DRIFT.md)
in the repo. File a chap-core ticket if any of those is news to you.
