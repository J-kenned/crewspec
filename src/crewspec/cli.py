"""crewspec command-line interface.

A thin click layer over :mod:`crewspec.pipeline`: it resolves arguments, runs
the shared pipeline, and maps each pipeline failure onto a
non-zero exit with an actionable message. All real work lives in the pipeline and
emitters so future subcommands reuse
it untouched.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import click

from crewspec import __version__
from crewspec.emitters import EMITTERS, EmitError
from crewspec.parser import CrewfileError, resolve_crewfile
from crewspec.pipeline import InstallSummary, build_ir, install
from crewspec.plan import render_json, render_tree
from crewspec.resolver import ResolveError
from crewspec.sources import (
    SourceError,
    SourceSpecError,
    is_github_spec,
    open_source,
)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="crewspec")
def cli() -> None:
    """crewspec declarative AI agent crews."""


@cli.command("install")
@click.argument("source")
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
def install_cmd(source: str, target: str, out: Path, dry_run: bool) -> None:
    """Install a crew: parse, resolve, then emit for the chosen target.

    SOURCE is a local crewfile (``.crewfile`` / ``crewspec.yaml``) or a directory
    containing one, or a ``github:owner/repo[@ref][/subdir]`` shorthand fetched
    over HTTPS.
    """

    if not is_github_spec(source) and not Path(source).exists():
        raise click.BadParameter("path does not exist", param_hint="'SOURCE'")

    try:
        with open_source(source) as local:
            crewfile = resolve_crewfile(local)
            summary = install(crewfile, EMITTERS[target](), out, dry_run=dry_run)
    except SourceSpecError as exc:
        raise click.BadParameter(str(exc), param_hint="'SOURCE'") from exc
    except (SourceError, CrewfileError, ResolveError, EmitError) as exc:
        raise click.ClickException(str(exc)) from exc

    _echo_summary(summary, dry_run=dry_run)


@cli.command("validate")
@click.argument("source")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the resolved IR as JSON for scripted use.",
)
def validate_cmd(source: str, as_json: bool) -> None:
    """Resolve SOURCE and print the resolved plan, writes no files.

    Runs the same parse + resolve as ``install`` but stops before emission and
    prints the resolved per-agent capabilities, the deduplicated MCP set, and any
    warnings. Exits non-zero on any parse or resolve error.

    SOURCE is a local crewfile (``.crewfile`` / ``crewspec.yaml``) or a directory
    containing one, or a ``github:owner/repo[@ref][/subdir]`` shorthand.
    """

    if not is_github_spec(source) and not Path(source).exists():
        raise click.BadParameter("path does not exist", param_hint="'SOURCE'")

    try:
        with open_source(source) as local:
            crewfile = resolve_crewfile(local)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ir = build_ir(crewfile)
    except SourceSpecError as exc:
        raise click.BadParameter(str(exc), param_hint="'SOURCE'") from exc
    except (SourceError, CrewfileError, ResolveError) as exc:
        raise click.ClickException(str(exc)) from exc

    messages = [str(entry.message) for entry in caught]
    if as_json:
        # stdout stays pure IR JSON for scripting; warnings go to stderr so they
        # never corrupt the parseable payload.
        for message in messages:
            click.echo(f"warning: {message}", err=True)
        click.echo(render_json(ir))
    else:
        click.echo(render_tree(ir, messages))


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
