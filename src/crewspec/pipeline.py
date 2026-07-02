"""The pipeline, shared across CLI subcommands.

This is the single seam every install path funnels through: the local
``crewspec install`` here, the ``github:`` fetch that follows it, and the
``validate`` command ``--dry-run`` previews. Keeping the pipeline in one place
means those callers compose the same three stages: load, resolve, emit — rather
than each re-wiring them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from crewspec.emitters import Emitter
from crewspec.ir import ResolvedIR
from crewspec.parser import load_crewfile
from crewspec.resolver import resolve


@dataclass(frozen=True)
class InstallSummary:
    """The outcome of an install, in the shape the CLI prints.

    ``out`` is the concrete directory artifacts were written to, or ``None`` on
    a dry run where resolution succeeded but nothing was emitted.
    """

    target: str
    agents: int
    skills: int
    mcps: int
    out: Path | None


def build_ir(crewfile: Path) -> ResolvedIR:
    """Parse and resolve ``crewfile`` into the target-agnostic IR.

    Raises a :class:`~crewspec.parser.errors.CrewfileError` subclass on a parse
    failure and a :class:`~crewspec.resolver.errors.ResolveError` on a resolve
    failure — the caller maps each to an exit code.
    """

    loaded = load_crewfile(crewfile)
    return resolve(loaded.registry)


def install(
    crewfile: Path,
    emitter: Emitter,
    dest: Path,
    *,
    dry_run: bool = False,
) -> InstallSummary:
    """Run the full pipeline for ``crewfile`` and return a printable summary.

    Parses and resolves, then emits with ``emitter`` into ``dest`` unless
    ``dry_run`` is set, in which case emission is skipped and ``out`` is ``None``.
    """

    ir = build_ir(crewfile)
    if dry_run:
        return _summarize(ir, emitter.name, out=None)
    emitter.emit(ir, dest)
    return _summarize(ir, emitter.name, out=emitter.output_path(dest))


def _summarize(ir: ResolvedIR, target: str, *, out: Path | None) -> InstallSummary:
    skills = {skill.name for agent in ir.agents for skill in agent.skills}
    return InstallSummary(
        target=target,
        agents=len(ir.agents),
        skills=len(skills),
        mcps=len(ir.mcps),
        out=out,
    )
