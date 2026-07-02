"""crewspec command-line interface.

A thin click layer over :mod:`crewspec.pipeline`: it resolves arguments, runs
the shared pipeline, and maps each pipeline failure onto a
non-zero exit with an actionable message. All real work lives in the pipeline and
emitters so future subcommands reuse
it untouched.
"""

from __future__ import annotations

from pathlib import Path

import click

from crewspec import __version__
from crewspec.emitters import EMITTERS, EmitError
from crewspec.parser import CrewfileError, resolve_crewfile
from crewspec.pipeline import InstallSummary, install
from crewspec.resolver import ResolveError


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="crewspec")
def cli() -> None:
    """crewspec declarative AI agent crews."""


@cli.command("install")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--target",
    type=click.Choice(sorted(EMITTERS)),
    default="claude",
    show_default=True,
    help="Emit target to render artifacts for.",
)
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Directory to write artifacts into.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Parse and resolve only; write no artifacts.",
)
def install_cmd(path: Path, target: str, out: Path, dry_run: bool) -> None:
    """Install a crewfile: parse, resolve, then emit for the chosen target.

    PATH is a crewfile (``.crewfile`` / ``crewspec.yaml``) or a directory
    containing one.
    """

    try:
        crewfile = resolve_crewfile(path)
        summary = install(crewfile, EMITTERS[target](), out, dry_run=dry_run)
    except (CrewfileError, ResolveError, EmitError) as exc:
        raise click.ClickException(str(exc)) from exc

    _echo_summary(summary, dry_run=dry_run)


@cli.command("list")
def list_cmd() -> None:
    """List installed packs."""
    raise click.ClickException("list is not yet implemented")


def _echo_summary(summary: InstallSummary, *, dry_run: bool) -> None:
    if dry_run:
        click.echo(f"Dry run OK — no files written (target: {summary.target})")
    else:
        click.echo(f"Installed crew → {summary.out} (target: {summary.target})")
    click.echo(
        f"  agents: {summary.agents}   "
        f"skills: {summary.skills}   "
        f"mcps: {summary.mcps}"
    )


def main() -> None:
    cli()
