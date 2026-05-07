from unittest.mock import patch

from typer.testing import CliRunner

from chap_scheduler import __version__
from chap_scheduler.cli.main import app


def test_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_info() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "chap-scheduler" in result.stdout
    assert "prefect_mount_path" in result.stdout


def test_register_blocks_default_target_is_the_local_embedded_server() -> None:
    runner = CliRunner()
    with patch("chap_scheduler.blocks.dhis2.Dhis2Credentials.register_type_and_schema") as mock_register:
        result = runner.invoke(app, ["register-blocks"])
    assert result.exit_code == 0
    assert "/prefect/api" in result.stdout
    assert "Dhis2Credentials" in result.stdout
    mock_register.assert_called_once()


def test_register_blocks_honours_explicit_api_url() -> None:
    runner = CliRunner()
    with patch("chap_scheduler.blocks.dhis2.Dhis2Credentials.register_type_and_schema") as mock_register:
        result = runner.invoke(app, ["register-blocks", "--api-url", "http://other:1234/prefect/api"])
    assert result.exit_code == 0
    assert "http://other:1234/prefect/api" in result.stdout
    mock_register.assert_called_once()
