"""Scan an install directory for the plugin trees ``crewspec install`` emits.

Backs ``crewspec list``. Each emitter owns one plugin subtree under ``--out``
(:meth:`~crewspec.emitters.base.Emitter.output_path`), so a target counts as
installed exactly when that subtree exists. The scan reads only what the
emitters write (agent files, skill directories, the MCP server config), so no
separate manifest or re-resolution is needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from crewspec.emitters import EMITTERS


@dataclass(frozen=True)
class InstalledPack:
    """One emitted plugin tree found under an install directory.

    ``path`` is the plugin root relative to the scanned directory. Every name
    list is sorted. ``agents`` is empty for a target without an agents directory
    (Codex emits agents as skills). ``warnings`` records anything unreadable so a
    damaged tree lists with gaps instead of failing.
    """

    target: str
    path: Path
    agents: tuple[str, ...]
    skills: tuple[str, ...]
    mcps: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def list_installed(out: Path, targets: Sequence[str] = ()) -> list[InstalledPack]:
    """Return the packs installed under ``out``, ordered by target name.

    ``targets`` restricts the scan to those emitter names; empty means every
    registered target. A target whose plugin root is absent is omitted.
    """

    packs: list[InstalledPack] = []
    for name in targets or sorted(EMITTERS):
        emitter = EMITTERS[name]()
        root = emitter.output_path(out)
        if not root.is_dir():
            continue
        mcps, warnings = _mcp_names(root / emitter.mcp_file)
        packs.append(
            InstalledPack(
                target=name,
                path=root.relative_to(out),
                agents=tuple(sorted(p.stem for p in (root / "agents").glob("*.md"))),
                skills=tuple(
                    sorted(p.parent.name for p in (root / "skills").glob("*/SKILL.md"))
                ),
                mcps=mcps,
                warnings=warnings,
            )
        )
    return packs


def _mcp_names(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Sorted server names from an emitted MCP config, plus any warning.

    The emitters write no MCP file for a crew without MCPs, so a missing file
    means "no servers". A file that cannot be read, is not JSON, or lacks a
    ``mcpServers`` object also yields no servers, with a warning, rather than
    failing the listing.
    """

    if not path.is_file():
        return (), ()
    try:
        data = json.loads(path.read_text())
    except OSError:
        return (), (f"{path} could not be read; MCP servers not listed",)
    except ValueError:
        return (), (f"{path} is not valid JSON; MCP servers not listed",)
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return (), (f"{path} has no 'mcpServers' object; MCP servers not listed",)
    return tuple(sorted(servers)), ()
