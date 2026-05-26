"""Drive the dhis2-chap-prediction Prefect deployment end-to-end.

Codifies the recipe from `docs/local-stack-runbook.md` (steps 6 + 7) so
`make e2e` is a single command. Saves the `Dhis2Credentials` block,
finds the deployment, triggers a flow run, polls until terminal, and
prints the run-report markdown artifact.

Configuration via env vars (all have sensible defaults):

- ``DHIS2_BASE_URL``    -- DHIS2 origin reachable from the worker
                            container (typically ``host.docker.internal``).
                            Default: ``http://host.docker.internal:8080``.
- ``DHIS2_USERNAME``    -- DHIS2 user.       Default: ``admin``.
- ``DHIS2_PASSWORD``    -- DHIS2 password.   Default: ``district``.
- ``PREFECT_API_URL``   -- Embedded Prefect API.
                            Default: ``http://127.0.0.1:9090/prefect/api``.
- ``E2E_BLOCK_NAME``    -- Block document name. Default: ``local-dhis2``.
- ``E2E_DEPLOYMENT_NAME`` -- Deployment name. Default: ``dhis2-chap-prediction``.
- ``E2E_TIMEOUT``       -- Wall-clock seconds for the flow to finish.
                            Default: ``600`` (10 min).
- ``E2E_POLL_INTERVAL`` -- Seconds between flow-state polls.
                            Default: ``10``.
- ``E2E_END_DATE``      -- Optional ``YYYY-MM-DD`` cap; passed to the flow
                            as ``end_date``. Default: empty (let each
                            prediction setup use its probed end period).

Exits 0 if the flow completes successfully, 1 otherwise.

Prerequisites the target won't check (`make e2e`'s help text says so):

- chap-scheduler container running on :9090 (`docker compose up`).
- chap-core stack running on :8000.
- DHIS2 running on :8080 with the chap route patched + 30s timeout.
- DHIS2's container connected to chap-core's docker network so the
  route's ``host.docker.internal`` resolves.

See `docs/local-stack-runbook.md` for the one-shot setup recipe.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date

import httpx
from pydantic import SecretStr

from chap_scheduler.blocks.dhis2 import Dhis2Credentials


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _save_block(block_name: str, dhis2_url: str, user: str, password: str) -> str:
    """Save the Dhis2Credentials block; return the document UUID."""
    bid = Dhis2Credentials(
        base_url=dhis2_url,
        username=user,
        password=SecretStr(password),
    ).save(block_name, overwrite=True)
    return str(bid)


def _find_deployment_id(client: httpx.Client, deployment_name: str) -> str:
    """POST /deployments/filter and pick by name."""
    response = client.post("/deployments/filter", json={})
    response.raise_for_status()
    for d in response.json():
        if d["name"] == deployment_name:
            return str(d["id"])
    raise RuntimeError(f"deployment {deployment_name!r} not found")


def _trigger_flow_run(
    client: httpx.Client,
    deployment_id: str,
    block_id: str,
    end_date: str | None,
) -> str:
    """POST /deployments/{id}/create_flow_run; return the flow_run_id."""
    parameters: dict[str, object] = {
        "credentials": {"$ref": {"block_document_id": block_id}},
        "end_date": end_date,
    }
    response = client.post(
        f"/deployments/{deployment_id}/create_flow_run",
        json={"parameters": parameters},
    )
    response.raise_for_status()
    return str(response.json()["id"])


_TERMINAL_STATES: frozenset[str] = frozenset({"COMPLETED", "FAILED", "CRASHED", "CANCELLED"})


def _poll_flow_run(
    client: httpx.Client,
    flow_run_id: str,
    timeout: float,
    poll_interval: float,
) -> str:
    """Poll /flow_runs/{id} until terminal; return the terminal state name.

    Raises:
        TimeoutError: ``timeout`` elapsed before the flow reached a
            terminal state.
    """
    deadline = time.monotonic() + timeout
    last: str | None = None
    while True:
        response = client.get(f"/flow_runs/{flow_run_id}")
        response.raise_for_status()
        state = response.json()["state"]["type"]
        if state != last:
            print(f"[{time.strftime('%H:%M:%S')}] {state}", flush=True)
            last = state
        if state in _TERMINAL_STATES:
            return str(state)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"flow run {flow_run_id} did not finish within {timeout}s (last state: {state!r})")
        time.sleep(poll_interval)


def _dump_artifact(client: httpx.Client, flow_run_id: str) -> None:
    """Print every markdown artifact the flow emitted (typically just the run report)."""
    response = client.post(
        "/artifacts/filter",
        json={"artifacts": {"flow_run_id": {"any_": [flow_run_id]}}},
    )
    response.raise_for_status()
    artifacts = response.json()
    if not artifacts:
        print("(no artifacts emitted)", flush=True)
        return
    for artifact in artifacts:
        print(f"\n--- artifact: {artifact.get('key')} ---", flush=True)
        print(artifact.get("data", ""), flush=True)


def _parse_end_date(raw: str) -> str | None:
    if not raw:
        return None
    # Validate the shape; pass through as ISO date string.
    return date.fromisoformat(raw).isoformat()


def main() -> int:
    """Run the e2e flow; returns shell exit code (0 on COMPLETED, 1 otherwise)."""
    dhis2_url = _env("DHIS2_BASE_URL", "http://host.docker.internal:8080")
    dhis2_user = _env("DHIS2_USERNAME", "admin")
    dhis2_password = _env("DHIS2_PASSWORD", "district")
    prefect_api = _env("PREFECT_API_URL", "http://127.0.0.1:9090/prefect/api")
    block_name = _env("E2E_BLOCK_NAME", "local-dhis2")
    deployment_name = _env("E2E_DEPLOYMENT_NAME", "dhis2-chap-prediction")
    timeout = float(_env("E2E_TIMEOUT", "600"))
    poll_interval = float(_env("E2E_POLL_INTERVAL", "10"))
    end_date = _parse_end_date(_env("E2E_END_DATE", ""))

    # Required for the Block.save() call to talk to the embedded server.
    os.environ.setdefault("PREFECT_API_URL", prefect_api)

    print(f"saving block {block_name!r} -> {dhis2_url} (user={dhis2_user})", flush=True)
    block_id = _save_block(block_name, dhis2_url, dhis2_user, dhis2_password)
    print(f"  block_id = {block_id}", flush=True)

    with httpx.Client(base_url=prefect_api, timeout=30.0) as client:
        deployment_id = _find_deployment_id(client, deployment_name)
        print(f"deployment {deployment_name!r} -> {deployment_id}", flush=True)
        flow_run_id = _trigger_flow_run(client, deployment_id, block_id, end_date)
        print(f"flow run id = {flow_run_id}", flush=True)
        try:
            terminal = _poll_flow_run(client, flow_run_id, timeout, poll_interval)
        except TimeoutError as exc:
            print(f"\nTIMEOUT: {exc}", flush=True)
            _dump_artifact(client, flow_run_id)
            return 1
        print(f"\nflow finished in state: {terminal}", flush=True)
        _dump_artifact(client, flow_run_id)
        return 0 if terminal == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
