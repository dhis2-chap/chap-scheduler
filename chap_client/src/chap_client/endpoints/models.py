"""Model registry + configured-model CRUD endpoints (``/v1/crud/models`` etc.)."""

from chap_client._client_base import ChapClientBase
from chap_client.schemas import ChapConfiguredModelCreate, ChapConfiguredModelDB, ChapModelSpec


class ModelsEndpoints(ChapClientBase):
    """Methods for ``/v1/crud/models`` and ``/v1/crud/configured-models``.

    Note: chap returns the same ``ModelSpecRead`` shape from both
    endpoints, and the data is identical on a fresh chap instance.
    See ``chap_client/CHAP_SPEC_DRIFT.md`` finding 2.
    """

    def list_models(self) -> list[ChapModelSpec]:
        """List the model registry (``GET /v1/crud/models``)."""
        raw = self.get("/v1/crud/models")
        return [ChapModelSpec.model_validate(item) for item in raw]

    def list_configured_models(self) -> list[ChapModelSpec]:
        """List configured models (``GET /v1/crud/configured-models``).

        Note: chap returns the same ``ModelSpecRead`` shape here as for
        :meth:`list_models` -- the API doesn't expose a tighter type
        for configured-models specifically.
        """
        raw = self.get("/v1/crud/configured-models")
        return [ChapModelSpec.model_validate(item) for item in raw]

    def create_configured_model(self, spec: ChapConfiguredModelCreate) -> ChapConfiguredModelDB:
        """Create a configured model (``POST /v1/crud/configured-models``).

        Args:
            spec: The configured-model definition. ``model_template_id``
                must come from ``/v1/crud/model-templates``, **not**
                ``/v1/crud/models`` -- they're different id spaces and
                chap returns a 500 if you mix them. See
                ``CHAP_SPEC_DRIFT.md`` finding 3.

        Returns:
            Chap's stored row (``ConfiguredModelDB`` upstream), which
            exposes ``model_template_id`` and the chosen option values.
            Note: chap silently rewrites the supplied ``name`` to
            ``"{template_name}:{your_name}"``; see
            ``CHAP_SPEC_DRIFT.md`` finding 4.

        Raises:
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        body = spec.model_dump(by_alias=True, mode="json")
        return ChapConfiguredModelDB.model_validate(self.post("/v1/crud/configured-models", json=body))
