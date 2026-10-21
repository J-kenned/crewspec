"""Unit tests for the resolved-plan renderer behind ``crewspec validate``."""

from __future__ import annotations

from pathlib import Path

from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models.mcp import HttpMcpServer, StdioMcpServer
from crewspec.plan import render_json, render_tree


def _ir() -> ResolvedIR:
    """A two-agent IR: ``base`` is bare, ``reviewer`` owns the whole closure."""

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
        mcps=(github,),
    )
    return ResolvedIR(agents=(base, reviewer), mcps=(github,))


def test_render_tree_reflects_ir_order_and_dedup() -> None:
    out = render_tree(_ir())

    # Agents are printed in the order the IR carries them (crewfile order).
    assert out.index("• base") < out.index("• reviewer")
    # The skill closure is printed dependency-first, verbatim from the IR.
    assert "skills:   format, lint" in out
    # The global set is labelled as the deduplicated union with its transport.
    assert "mcp servers (deduplicated): 1" in out
    assert "github — stdio (gh-mcp serve)" in out


def test_render_tree_marks_inherit_all_tools() -> None:
    # ``base`` carries no tool allowlist, so it inherits every tool.
    assert "(inherit all)" in render_tree(_ir())


def test_render_tree_lists_warnings_or_none() -> None:
    assert "warnings:\n  (none)" in render_tree(_ir())
    assert "! something looked off" in render_tree(_ir(), ["something looked off"])


def test_render_tree_summarizes_remote_mcp() -> None:
    ir = ResolvedIR(
        mcps=(
            ResolvedMcp(
                name="api", server=HttpMcpServer(url="https://example.test/mcp")
            ),
        )
    )
    assert "api — http (https://example.test/mcp)" in render_tree(ir)


def test_render_tree_handles_empty_ir() -> None:
    out = render_tree(ResolvedIR())
    assert "crewspec plan: 0 agents, 0 skills, 0 mcp servers" in out
    assert "agents:\n  (none)" in out


def test_render_json_round_trips_into_ir() -> None:
    ir = _ir()
    assert ResolvedIR.model_validate_json(render_json(ir)) == ir
