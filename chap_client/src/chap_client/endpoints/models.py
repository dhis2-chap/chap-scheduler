"""Model registry + configured-model CRUD endpoints (``/v1/crud/models`` etc.)."""

from chap_client.base import ChapClientBase
from chap_client.schemas import ChapConfiguredModelCreate, ChapConfiguredModelDB, ChapModelSpec, ChapModelTemplate


class ModelsEndpoints(ChapClientBase):
    """Methods for ``/v1/crud/models``, ``/v1/crud/configured-models``, and ``/v1/crud/model-templates``.

    Note: chap returns the same ``ModelSpecRead`` shape from both
    ``/v1/crud/models`` and ``/v1/crud/configured-models`` and the data
    is identical on a fresh chap instance. See
    ``chap_client/CHAP_SPEC_DRIFT.md`` finding 2.
    ``/v1/crud/model-templates`` is a third, **distinct** endpoint --
    its ids are the ones accepted as ``modelTemplateId`` in
    `create_configured_model` (finding 3).
    """

    def list_models(self) -> list[ChapModelSpec]:
        """List the model registry (``GET /v1/crud/models``)."""
        raw = self.get("/v1/crud/models")
        return [ChapModelSpec.model_validate(item) for item in raw]

    def list_configured_models(self) -> list[ChapModelSpec]:
        """List configured models (``GET /v1/crud/configured-models``).

        Note: chap returns the same ``ModelSpecRead`` shape here as for
        `list_models()` -- the API doesn't expose a tighter type
        for configured-models specifically.
        """
        raw = self.get("/v1/crud/configured-models")
        return [ChapModelSpec.model_validate(item) for item in raw]

    def list_model_templates(self) -> list[ChapModelTemplate]:
        """List model templates (``GET /v1/crud/model-templates``).

        These are the rows whose ``id`` is the valid id space for
        `ChapConfiguredModelCreate.model_template_id`. Used by
        `create_configured_model`'s preflight to catch the id-space
        confusion documented as `CHAP_SPEC_DRIFT.md` finding 3.
        """
        raw = self.get("/v1/crud/model-templates")
        return [ChapModelTemplate.model_validate(item) for item in raw]

    def create_configured_model(
        self,
        spec: ChapConfiguredModelCreate,
        *,
        validate: bool = True,
    ) -> ChapConfiguredModelDB:
        """Create a configured model (``POST /v1/crud/configured-models``).

        With ``validate=True`` (the default), this method first calls
        `list_model_templates()` and refuses synchronously with a
        ``ValueError`` if ``spec.model_template_id`` doesn't resolve
        -- that's the `CHAP_SPEC_DRIFT.md` finding 3 mitigation.
        Pass ``validate=False`` to skip the round-trip when the caller
        already has the live id list in hand.

        Args:
            spec: The configured-model definition. ``model_template_id``
                must come from ``/v1/crud/model-templates``, **not**
                ``/v1/crud/models`` -- they're different id spaces and
                chap returns a 500 if you mix them.
            validate: When True (default), preflight ``model_template_id``
                against the current model-templates list.

        Returns:
            Chap's stored row (``ConfiguredModelDB`` upstream), which
            exposes ``model_template_id`` and the chosen option values.
            Note: chap silently rewrites the supplied ``name`` to
            ``"{template_name}:{your_name}"``; see
            ``CHAP_SPEC_DRIFT.md`` finding 4.

        Raises:
            ValueError: ``validate=True`` and the supplied
                ``model_template_id`` does not match any template id
                returned by `list_model_templates()`.
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        if validate:
            templates = self.list_model_templates()
            template_ids = sorted(t.id for t in templates if t.id is not None)
            if spec.model_template_id not in template_ids:
                raise ValueError(
                    f"modelTemplateId={spec.model_template_id} does not match any model template "
                    f"on this chap (known ids: {template_ids}). "
                    f"Note: this is NOT the same id space as /v1/crud/models. "
                    f"Pass validate=False to skip this check."
                )
        body = spec.model_dump(by_alias=True, mode="json")
        return ChapConfiguredModelDB.model_validate(self.post("/v1/crud/configured-models", json=body))
