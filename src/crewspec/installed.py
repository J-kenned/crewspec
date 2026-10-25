"""Scan an install directory for the packs ``crewspec install`` emitted.

Backs ``crewspec list``. Each emitter keeps one plugin directory per pack under
``--out`` and writes a state file inside it
(:data:`~crewspec.emitters.base.STATE_FILE`), so a pack is installed exactly
when that file exists. The scan reads only what the emitters write (the state
file, agent files, skill directories), so no re-resolution is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from crewspec.emitters import EMITTERS, EmitError
from crewspec.emitters.base import PackState


@dataclass(frozen=True)
class InstalledPack:
    """One emitted pack found under an install directory.

    ``path`` is the plugin root relative to the scanned directory. Every name
    list is sorted. ``agents`` is empty for a target without an agents directory
    (Codex emits agents as skills). ``warnings`` records anything unreadable so a
    damaged tree lists with gaps instead of failing.
    """

    name: str
    target: str
    path: Path
    agents: tuple[str, ...]
    skills: tuple[str, ...]
    mcps: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def list_installed(out: Path, targets: Sequence[str] = ()) -> list[InstalledPack]:
    """Return the packs installed under ``out``, ordered by target then name.

    ``targets`` restricts the scan to those emitter names; empty means every
    registered target.
    """

    packs: list[InstalledPack] = []
    for target in targets or sorted(EMITTERS):
        emitter = EMITTERS[target]()
        for root in emitter.installed(out):
            mcps: tuple[str, ...] = ()
            flat_agents: tuple[str, ...] = ()
            warnings: tuple[str, ...] = ()
            try:
                state = PackState.read(emitter.state_path(out, root.name))
            except EmitError as exc:
                warnings = (f"{exc}; MCP servers not listed",)
            else:
                mcps = tuple(sorted(state.mcps)) if state else ()
                flat_agents = tuple(sorted(state.agents)) if state else ()
            packs.append(
                InstalledPack(
                    name=root.name,
                    target=target,
                    path=root.relative_to(out),
                    agents=flat_agents
                    or tuple(sorted(p.stem for p in (root / "agents").glob("*.md"))),
                    skills=tuple(
                        sorted(
                            p.parent.name for p in (root / "skills").glob("*/SKILL.md")
                        )
                    ),
                    mcps=mcps,
                    warnings=warnings,
                )
            )
    return packs
