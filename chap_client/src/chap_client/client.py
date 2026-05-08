"""HTTP client for the chap REST API.

Constructed with primitives (``base_url``, ``auth``, optional
``route_prefix``) so this package stays free of any opinions about
where the credentials come from. Two common shapes:

- **Via the DHIS2 chap-route proxy** (used by chap-scheduler):
  pass ``route_prefix="/api/routes/chap/run"`` and DHIS2 basic auth.
- **Direct against chap**: pass ``route_prefix=""`` (the default) and
  whatever auth chap's deployment expects.

Connection pooling: a single :class:`httpx.Client` is held for the
lifetime of the :class:`ChapClient` instance, so polling loops reuse
the underlying TCP connection. Use as a context manager so the pool
is closed cleanly.

Retries: idempotent methods (GET / HEAD) retry on transient transport
errors and 5xx responses, with exponential backoff + jitter, capped
at ``max_attempts`` (default 3). POST is never retried -- chap's
``submit_prediction`` and ``create_evaluation`` aren't idempotent and
a retry on a connection blip would risk a duplicate. Disable retries
for tests by passing ``max_attempts=1``.

The endpoint methods themselves live on the mixins under
:mod:`chap_client.endpoints`. Each mixin covers one chap resource
group; ``ChapClient`` inherits from all of them so the public surface
stays flat.
"""

from chap_client._client_base import ChapAuth, ChapClientBase
from chap_client.endpoints import (
    ConfiguredModelsWithDataSourceEndpoints,
    DatasetsEndpoints,
    EvaluationsEndpoints,
    ModelsEndpoints,
    PredictionsEndpoints,
    SystemEndpoints,
)


class ChapClient(
    SystemEndpoints,
    DatasetsEndpoints,
    ModelsEndpoints,
    ConfiguredModelsWithDataSourceEndpoints,
    EvaluationsEndpoints,
    PredictionsEndpoints,
):
    """Calls chap REST endpoints.

    Composed from the mixins under :mod:`chap_client.endpoints` so
    each resource cluster lives in its own file. The class itself is
    intentionally empty -- the methods come from the mixins, the HTTP
    plumbing comes from
    :class:`~chap_client._client_base.ChapClientBase`.

    Use as a context manager so the underlying connection pool is
    closed:

    .. code-block:: python

        with ChapClient(base_url=url, auth=(user, password), route_prefix="/api/routes/chap/run") as client:
            client.system_info()
            client.list_evaluations()
    """


__all__ = ["ChapAuth", "ChapClient", "ChapClientBase"]
