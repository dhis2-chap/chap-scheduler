# chap-client

Python HTTP client and pydantic schemas for the
[chap-core](https://github.com/dhis2-chap/chap-core) REST API.

The package exposes a typed `ChapClient` (composed from per-resource
endpoint mixins), a `chap-client` Typer CLI mirroring the same methods,
and pydantic schemas for chap's request and response shapes.

Today this package lives as a sibling of
[`chap-scheduler`](https://github.com/dhis2-chap/chap-scheduler) in the
same repository, wired in via a uv path-dep. When the API surface
stabilises it will move to its own repo and publish independently.

See the rendered docs at
[`docs/chap-client/`](../docs/chap-client/index.md) for the mental
model, full endpoint reference, CLI walkthrough, and API reference.
