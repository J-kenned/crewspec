"""crewspec command-line interface.

A thin click layer over :mod:`crewspec.pipeline`: it resolves arguments, runs
the shared pipeline, and maps each pipeline failure onto a
non-zero exit with an actionable message. All real work lives in the pipeline and
emitters so future subcommands reuse
it untouched.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import click

from crewspec import __version__
from crewspec.emitters import EMITTERS, CodexEmitter, EmitError
from crewspec.installed import InstalledPack, list_installed
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
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Directory crewspec installed into.",
)
@click.option(
    "--target",
    type=click.Choice(sorted(EMITTERS)),
    default=None,
    help="Report only this target. Default: every target with a pack installed.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print the packs as a JSON array for scripted use.",
)
def list_cmd(out: Path, target: str | None, as_json: bool) -> None:
    """List the crewspec packs installed under --out.

    Reads the plugin trees ``install`` emitted and prints each target's plugin
    path, agents, skills, and MCP servers. Nothing is resolved or written. An
    empty listing is a valid answer and exits 0.
    """

    packs = list_installed(out, [target] if target else ())

    # Warnings go to stderr in both modes so stdout stays a clean listing.
    for pack in packs:
        for message in pack.warnings:
            click.echo(f"warning: {message}", err=True)

    if as_json:
        rows = [
            {
                "target": pack.target,
                "path": pack.path.as_posix(),
                "agents": list(pack.agents),
                "skills": list(pack.skills),
                "mcps": list(pack.mcps),
            }
            for pack in packs
        ]
        click.echo(json.dumps(rows, indent=2))
    elif packs:
        _echo_packs(packs)
    elif target:
        click.echo(f"No crewspec pack installed for {target} under {out}")
    else:
        click.echo(f"No crewspec packs installed under {out}")


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


def _echo_packs(packs: list[InstalledPack]) -> None:
    width = max(len(pack.target) for pack in packs)
    for pack in packs:
        click.echo(f"{pack.target:<{width}}  {pack.path.as_posix()}")
        skills = ", ".join(pack.skills) or "(none)"
        if pack.target == CodexEmitter.name:
            # Codex has no agents directory; the emitter folds each agent into
            # a skill, so the agent-derived skills are listed with the rest.
            click.echo(f"  skills: {skills}   (Codex agents are emitted as skills)")
        else:
            click.echo(f"  agents: {', '.join(pack.agents) or '(none)'}")
            click.echo(f"  skills: {skills}")
        click.echo(f"  mcps:   {', '.join(pack.mcps) or '(none)'}")


def main() -> None:
    cli()
