"""Dataset CRUD endpoints (``/v1/crud/datasets/*``)."""

from chap_client.base import ChapClientBase
from chap_client.schemas import ChapDataset


class DatasetsEndpoints(ChapClientBase):
    """Methods for ``/v1/crud/datasets``."""

    def list_datasets(self) -> list[ChapDataset]:
        """List datasets registered with chap (``GET /v1/crud/datasets``).

        Datasets carry a ``type`` field (``"evaluation"`` or
        ``"prediction"``) flagging which workflow they were built for;
        evaluation creation typically picks an evaluation dataset.

        Note: chap-core ignores unknown / pagination query params
        (``?limit``, ``?type``) and always returns the full table --
        see ``CHAP_CORE_ISSUES.md`` finding 12. Filter client-side
        until upstream adds real pagination.
        """
        raw = self.get("/v1/crud/datasets")
        return [ChapDataset.model_validate(item) for item in raw]

    def get_dataset(self, id: int) -> ChapDataset:
        """Fetch a single dataset by id (``GET /v1/crud/datasets/{id}``).

        Args:
            id: Numeric dataset id from `list_datasets()`.

        Returns:
            The dataset metadata. The actual rows live behind separate
            ``/csv`` and ``/df`` endpoints we don't model yet.

        Raises:
            ChapHttpError: chap returned a non-2xx response (e.g.
                ``404`` for an unknown id).
        """
        return ChapDataset.model_validate(self.get(f"/v1/crud/datasets/{id}"))
