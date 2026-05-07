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
