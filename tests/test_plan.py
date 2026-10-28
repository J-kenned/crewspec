"""Unit tests for the resolved-plan renderer behind ``crewspec validate``."""

from __future__ import annotations

from pathlib import Path

from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models.mcp import HttpMcpServer, StdioMcpServer
from crewspec.plan import render_json, render_tree


def _ir() -> ResolvedIR:
    """A two-agent IR: ``base`` is bare, ``reviewer`` lists both skills."""

    github = ResolvedMcp(
        name="github", server=StdioMcpServer(command="gh-mcp", args=["serve"])
    )
    fmt = ResolvedSkill(
        name="format",
        path=Path("skills/format"),
        skill_md=Path("skills/format/SKILL.md"),
    )
    lint = ResolvedSkill(
        name="lint", path=Path("skills/lint"), skill_md=Path("skills/lint/SKILL.md")
    )
    base = ResolvedAgent(name="base", persona_path=Path("agents/base.md"))
    reviewer = ResolvedAgent(
        name="reviewer",
        persona_path=Path("agents/reviewer.md"),
        tools=("Read", "Edit"),
        skills=(fmt, lint),
    )
    return ResolvedIR(
        name="review-pack", agents=(base, reviewer), skills=(fmt, lint), mcps=(github,)
    )


def test_render_tree_reflects_ir_order() -> None:
    out = render_tree(_ir())

    assert out.startswith("crewspec plan: review-pack — 2 agents, 2 skills, 1 mcp")
    # Agents are printed in the order the IR carries them (crewfile order).
    assert out.index("• base") < out.index("• reviewer")
    # The agent's declared skills, verbatim from the IR.
    assert "skills:   format, lint" in out
    # Every declared skill and MCP server, with the server's transport.
    assert "skills:\n  • format — skills/format\n  • lint — skills/lint" in out
    assert "mcp servers: 1" in out
    assert "github — stdio (gh-mcp serve)" in out


def test_render_tree_marks_inherit_all_tools() -> None:
    # ``base`` carries no tool allowlist, so it inherits every tool.
    assert "(inherit all)" in render_tree(_ir())


def test_render_tree_lists_warnings_or_none() -> None:
    assert "warnings:\n  (none)" in render_tree(_ir())
    assert "! something looked off" in render_tree(_ir(), ["something looked off"])


def test_render_tree_summarizes_remote_mcp() -> None:
    ir = ResolvedIR(
        name="remote",
        mcps=(
            ResolvedMcp(
                name="api", server=HttpMcpServer(url="https://example.test/mcp")
            ),
        ),
    )
    assert "api — http (https://example.test/mcp)" in render_tree(ir)


def test_render_tree_labels_project_scoped_mcps() -> None:
    ir = ResolvedIR(
        name="scoped",
        mcps=(
            ResolvedMcp(name="shared", server=StdioMcpServer(command="shared")),
            ResolvedMcp(
                name="local", server=StdioMcpServer(command="local", scope="project")
            ),
        ),
    )
    out = render_tree(ir)

    assert "mcp servers: 2 (1 user, 1 project)" in out
    assert "(local) [project]" in out
    assert out.count("[project]") == 1
    # No breakdown when every server is user scope.
    assert "mcp servers: 1\n" in render_tree(_ir())


def test_render_tree_handles_empty_ir() -> None:
    out = render_tree(ResolvedIR(name="empty"))
    assert "crewspec plan: empty — 0 agents, 0 skills, 0 mcp servers" in out
    assert "agents:\n  (none)" in out
    assert "skills:\n  (none)" in out


def test_render_json_round_trips_into_ir() -> None:
    ir = _ir()
    assert ResolvedIR.model_validate_json(render_json(ir)) == ir
