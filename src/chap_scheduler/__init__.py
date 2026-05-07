"""chap-scheduler: FastAPI service embedding Prefect for CHAP workflow orchestration."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("chap-scheduler")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
