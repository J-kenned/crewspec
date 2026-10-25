"""Shared fixtures."""

from __future__ import annotations

from typing import Callable

import pytest
from click.testing import CliRunner, Result

from crewspec.cli import cli

Runner = Callable[..., Result]


@pytest.fixture
def run_cli() -> Runner:
    """Invoke the crewspec CLI in-process and return the click ``Result``."""

    def _run(*args: str) -> Result:
        return CliRunner().invoke(cli, list(args), prog_name="crewspec")

    return _run
