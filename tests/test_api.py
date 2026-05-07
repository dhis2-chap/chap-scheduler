from fastapi.testclient import TestClient

from chap_scheduler.api.app import create_app
from chap_scheduler.config import Settings


def _settings() -> Settings:
    # Tests don't spin up the embedded Prefect server (avoids DB/migrations).
    return Settings(embed_prefect=False)


def test_health() -> None:
    app = create_app(_settings())
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_info_when_prefect_disabled() -> None:
    app = create_app(_settings())
    client = TestClient(app)
    response = client.get("/info")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "chap-scheduler"
    assert body["prefect_embedded"] is False
    assert body["prefect_ui_path"] is None
    assert body["prefect_api_path"] is None


def test_info_reports_embedded_paths() -> None:
    app = create_app(Settings(embed_prefect=True, prefect_mount_path="/prefect"))
    client = TestClient(app)
    response = client.get("/info")
    assert response.status_code == 200
    body = response.json()
    assert body["prefect_embedded"] is True
    assert body["prefect_ui_path"] == "/prefect/"
    assert body["prefect_api_path"] == "/prefect/api"


def test_root_redirects_to_prefect_ui() -> None:
    app = create_app(Settings(embed_prefect=True, prefect_mount_path="/prefect"))
    client = TestClient(app, follow_redirects=False)
    response = client.get("/")
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/prefect/"


def test_root_redirects_to_docs_when_prefect_disabled() -> None:
    app = create_app(_settings())
    client = TestClient(app, follow_redirects=False)
    response = client.get("/")
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/docs"
