"""Smoke tests asserting the package imports and the CLI runs."""

from click.testing import CliRunner

import crewspec
from crewspec.cli import cli


def test_package_imports() -> None:
    assert crewspec.__version__ == "0.1.0"


def test_cli_help_prints_usage() -> None:
    result = CliRunner().invoke(cli, ["--help"], prog_name="crewspec")
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "crewspec" in result.output
