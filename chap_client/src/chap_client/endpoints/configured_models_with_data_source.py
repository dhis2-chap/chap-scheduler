"""Endpoints for ``/v1/crud/configured-models-with-data-source/*``.

A configured-model-with-data-source is the "deployable" form chap
uses for predictions: a configured model bundled with the data
sources it pulls covariates from. This is the resource the
chap-scheduler Prefect flow consumes.
"""

from chap_client.base import ChapClientBase
from chap_client.schemas import ChapConfiguredModelWithDataSource


class ConfiguredModelsWithDataSourceEndpoints(ChapClientBase):
    """Methods for ``/v1/crud/configured-models-with-data-source``."""

    def list_configured_models_with_data_source(self) -> list[ChapConfiguredModelWithDataSource]:
        """List all configured-models-with-data-source rows (``GET …``)."""
        raw = self.get("/v1/crud/configured-models-with-data-source")
        return [ChapConfiguredModelWithDataSource.model_validate(item) for item in raw]

    def get_configured_model_with_data_source(self, id: int) -> ChapConfiguredModelWithDataSource:
        """Fetch a single configured-model-with-data-source by id.

        Calls ``GET /v1/crud/configured-models-with-data-source/{id}``.

        Note: chap returns a richer ``…ReadWithPredictions`` shape on
        this endpoint (it embeds the prediction list); we only model
        the common fields and silently ignore the rest via
        ``extra="ignore"``. Add ``predictions`` to
        `ChapConfiguredModelWithDataSource`
        if/when callers need it.

        Args:
            id: Numeric id from
                `list_configured_models_with_data_source()`.

        Raises:
            ChapHttpError: chap returned a non-2xx response.
        """
        return ChapConfiguredModelWithDataSource.model_validate(
            self.get(f"/v1/crud/configured-models-with-data-source/{id}")
        )

    def create_configured_model_with_data_source_from_backtest(
        self,
        evaluation_id: int,
    ) -> ChapConfiguredModelWithDataSource:
        """Create a configured-model-with-data-source row from an evaluation.

        Calls ``POST /v1/crud/configured-models-with-data-source/from-backtest/{id}``.

        chap derives the configured-model body from the referenced
        evaluation (chap-core URLs still call it "backtest"); there is
        no request body. Marked **Experimental** upstream; the
        response shape may change without notice.

        Args:
            evaluation_id: Numeric evaluation id from
                `list_evaluations()`.

        Raises:
            ChapHttpError: chap returned a non-2xx response. POST is
                non-idempotent and is **not** retried.
        """
        return ChapConfiguredModelWithDataSource.model_validate(
            self.post(f"/v1/crud/configured-models-with-data-source/from-backtest/{evaluation_id}")
        )
