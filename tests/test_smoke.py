"""Smoke tests asserting the package imports and the CLI runs."""

import pytest

import crewspec
from crewspec.cli import main


def test_package_imports() -> None:
    assert crewspec.__version__ == "0.1.0"


def test_cli_help_prints_usage(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["crewspec", "--help"])
    main()
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "crewspec" in out
