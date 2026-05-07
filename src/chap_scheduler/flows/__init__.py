"""Prefect flows orchestrated by chap-scheduler.

Flows live here as plain Python modules using ``@flow`` / ``@task`` decorators.
Add modules under this package and register them in ``deployments/`` for
container-based execution. The package ships empty on purpose — the project
is plumbing-only at this stage.
"""
