"""Endpoint mixins composed into `chap_client.ChapClient`.

Each module here defines a small mixin class with the typed methods
for one chap resource group. ``ChapClient`` inherits from each mixin
so the public surface stays flat:
``client.list_evaluations()``, not ``client.evaluations.list()``.

Splitting endpoints into mixins keeps each file focused on one
resource and one set of pydantic schemas; the alternative -- one
giant ``ChapClient`` -- starts to break down past ~5 resources.
"""

from chap_client.endpoints.datasets import DatasetsEndpoints
from chap_client.endpoints.evaluations import EvaluationsEndpoints
from chap_client.endpoints.models import ModelsEndpoints
from chap_client.endpoints.prediction_setups import PredictionSetupsEndpoints
from chap_client.endpoints.predictions import PredictionsEndpoints
from chap_client.endpoints.system import SystemEndpoints

__all__ = [
    "DatasetsEndpoints",
    "EvaluationsEndpoints",
    "ModelsEndpoints",
    "PredictionSetupsEndpoints",
    "PredictionsEndpoints",
    "SystemEndpoints",
]
