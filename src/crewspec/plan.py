"""Render a resolved plan for ``crewspec validate``.

Presentation over the :class:`~crewspec.ir.resolved.ResolvedIR`, kept out of the
CLI so the click layer stays thin and the rendering is unit-testable on its own.
Two views share one IR: a human-readable tree (:func:`render_tree`) and the raw
IR serialization (:func:`render_json`) for scripted use.

The IR arrives already ordered (agents, skills, and MCPs in crewfile order) so
rendering is a straight walk that never re-derives order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp
from crewspec.models.mcp import StdioMcpServer


def render_json(ir: ResolvedIR) -> str:
    """Serialize the resolved IR as indented JSON.

    Reuses pydantic's serialization directly so scripted callers consume the same
    shape emitters do; ``Path`` fields render as strings.
    """

    return ir.model_dump_json(indent=2)


def render_tree(ir: ResolvedIR, warnings: Sequence[str] = ()) -> str:
    """Render the resolved plan as a human-readable tree.

    Lists the pack name, agents in crewfile order with their tools and skills,
    every declared skill, every MCP server, then any ``warnings`` captured
    during resolution.
    """

    lines: list[str] = [_header(ir), "", "agents:"]
    if ir.agents:
        for agent in ir.agents:
            lines.extend(_agent_lines(agent))
    else:
        lines.append("  (none)")

    lines += ["", "skills:"]
    if ir.skills:
        lines += [f"  • {s.name} — {_display_path(s.path)}" for s in ir.skills]
    else:
        lines.append("  (none)")

    lines += ["", f"mcp servers: {len(ir.mcps)}"]
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
    return [
        f"  • {agent.name}",
        f"      persona:  {_display_path(agent.persona_path)}",
        f"      tools:    {', '.join(agent.tools) or '(inherit all)'}",
        f"      skills:   {', '.join(s.name for s in agent.skills) or '(none)'}",
    ]


def _mcp_summary(mcp: ResolvedMcp) -> str:
    """A one-line transport summary: ``stdio (cmd args)`` or ``http (url)``."""

    server = mcp.server
    if isinstance(server, StdioMcpServer):
        return f"stdio ({' '.join([server.command, *server.args])})"
    return f"{server.type} ({server.url})"


def _display_path(path: Path) -> str:
    """Show ``path`` relative to the working directory when it lives under it."""

    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
