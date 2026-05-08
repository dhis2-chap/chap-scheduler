# chap-client

HTTP client for the chap REST API. Currently a stub — future home of
the client code being extracted from
[`chap-scheduler`](https://github.com/dhis2-chap/chap-scheduler) one
stable layer at a time.

Today this package is a sibling to `chap-scheduler` in the same
repository, wired in via a uv path-dep. When the API surface
stabilises, it will move to its own repo and publish independently.
