"""Render a resolved plan for ``crewspec validate``.

Presentation over the :class:`~crewspec.ir.resolved.ResolvedIR`, kept out of the
CLI so the click layer stays thin and the rendering is unit-testable on its own.
Two views share one IR: a human-readable tree (:func:`render_tree`) and the raw
IR serialization (:func:`render_json`) for scripted use.

The IR arrives already ordered (agents, skills, and MCPs in crewfile order) so
rendering is a straight walk that never re-derives order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping, Sequence

from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp
from crewspec.models.mcp import StdioMcpServer

#: Given an agent's withheld block names, the per-target disposition of each.
Dispositions = Callable[[tuple[str, ...]], Mapping[str, Mapping[str, str]]]


def render_json(ir: ResolvedIR, dispositions: Dispositions | None = None) -> str:
    """Serialize the resolved IR as indented JSON.

    Reuses pydantic's serialization directly so scripted callers consume the same
    shape emitters do; ``Path`` fields render as strings. With ``dispositions``
    each agent's ``dispositions`` slot is filled:
    ``{target: {block: delivered|translated|withheld}}`` for its 0.2 blocks, so
    the output still round-trips into a :class:`ResolvedIR`.
    """

    if dispositions is None:
        return ir.model_dump_json(indent=2)
    data = json.loads(ir.model_dump_json())
    for agent, row in zip(ir.agents, data["agents"], strict=True):
        if agent.withheld:
            row["dispositions"] = {
                target: dict(ruling)
                for target, ruling in dispositions(agent.withheld).items()
            }
    return json.dumps(data, indent=2)


def render_tree(
    ir: ResolvedIR,
    warnings: Sequence[str] = (),
    dispositions: Dispositions | None = None,
) -> str:
    """Render the resolved plan as a human-readable tree.

    Lists the pack name, agents in crewfile order with their tools and skills,
    every declared skill, every MCP server, then any ``warnings`` captured
    during resolution. With ``dispositions``, each agent that sets 0.2 blocks
    also lists what every target does with them.
    """

    lines: list[str] = [_header(ir), "", "agents:"]
    if ir.agents:
        for agent in ir.agents:
            lines.extend(_agent_lines(agent))
            if dispositions is not None and agent.withheld:
                for target, ruling in dispositions(agent.withheld).items():
                    summary = ", ".join(f"{b}: {d}" for b, d in ruling.items())
                    lines.append(f"      {target:<8}{summary}")
    else:
        lines.append("  (none)")

    lines += ["", "skills:"]
    if ir.skills:
        lines += [f"  • {s.name} — {_display_path(s.path)}" for s in ir.skills]
    else:
        lines.append("  (none)")

    project = sum(mcp.server.scope == "project" for mcp in ir.mcps)
    count = f"mcp servers: {len(ir.mcps)}"
    if project:
        count += f" ({len(ir.mcps) - project} user, {project} project)"
    lines += ["", count]
    if ir.mcps:
        lines += [f"  • {mcp.name} — {_mcp_summary(mcp)}" for mcp in ir.mcps]
    else:
        lines.append("  (none)")

    lines += ["", "warnings:"]
    if warnings:
        lines += [f"  ! {message}" for message in warnings]
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def _header(ir: ResolvedIR) -> str:
    return (
        f"crewspec plan: {ir.name} — "
        f"{_count(len(ir.agents), 'agent')}, "
        f"{_count(len(ir.skills), 'skill')}, "
        f"{_count(len(ir.mcps), 'mcp server')}"
    )


def _count(n: int, noun: str) -> str:
    """``"1 agent"`` / ``"2 agents"`` — pluralize ``noun`` by appending ``s``."""

    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _agent_lines(agent: ResolvedAgent) -> list[str]:
    lines = [
        f"  • {agent.name}",
        f"      persona:  {_display_path(agent.persona_path)}",
        f"      tools:    {', '.join(agent.tools) or '(inherit all)'}",
        f"      skills:   {', '.join(s.name for s in agent.skills) or '(none)'}",
    ]
    if agent.withheld:
        lines.append(f"      withheld: {', '.join(agent.withheld)} (not emitted)")
    return lines


def _mcp_summary(mcp: ResolvedMcp) -> str:
    """``stdio (cmd args)`` or ``http (url)``, tagged when not user scope."""

    server = mcp.server
    if isinstance(server, StdioMcpServer):
        summary = f"stdio ({' '.join([server.command, *server.args])})"
    else:
        summary = f"{server.type} ({server.url})"
    return summary if server.scope == "user" else f"{summary} [{server.scope}]"


def _display_path(path: Path) -> str:
    """Show ``path`` relative to the working directory when it lives under it."""

    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
