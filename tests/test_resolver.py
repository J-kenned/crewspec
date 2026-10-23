"""Tests for the resolver and the resolved IR.

Registries are built in memory with fake paths: ``resolve`` is pure and does no
IO, so the paths it carries through need not exist on disk.
"""

from pathlib import Path
from typing import Iterable

from crewspec.ir import ResolvedIR
from crewspec.models import AgentDef, AgentDeps, SkillDef, SkillDeps, StdioMcpServer
from crewspec.models.mcp import McpServer
from crewspec.parser.registry import AgentNode, NodeRegistry, SkillNode
from crewspec.resolver import resolve


def _agent(
    name: str,
    *,
    skills: Iterable[str] = (),
    tools: Iterable[str] = (),
) -> AgentNode:
    return AgentNode(
        definition=AgentDef(
            persona=f"agents/{name}.md",
            allowed_tools=list(tools),
            depends_on=AgentDeps(skills=list(skills)),
        ),
        persona=Path(f"agents/{name}.md"),
    )


def _skill(
    name: str,
    *,
    skills: Iterable[str] = (),
    mcps: Iterable[str] = (),
) -> SkillNode:
    root = Path(f"skills/{name}")
    return SkillNode(
        definition=SkillDef(path=f"skills/{name}"),
        root=root,
        skill_md=root / "SKILL.md",
        deps=SkillDeps(skills=list(skills), mcps=list(mcps)),
    )


def _registry(
    *,
    agents: Iterable[AgentNode] = (),
    skills: Iterable[SkillNode] = (),
    mcps: dict[str, McpServer] | None = None,
    instructions: Path | None = None,
) -> NodeRegistry:
    # The node's identity is its map key; the helpers encode it in the path.
    return NodeRegistry(
        name="crew",
        instructions=instructions,
        agents={a.persona.stem: a for a in agents},
        skills={s.root.name: s for s in skills},
        mcps=mcps or {},
    )


def _names(items: Iterable[object]) -> list[str]:
    return [item.name for item in items]  # type: ignore[attr-defined]


def test_every_declared_skill_and_mcp_is_emitted_in_crewfile_order() -> None:
    # Nothing references s2 or m2; both are still part of the IR.
    registry = _registry(
        agents=[_agent("a", skills=["s1"])],
        skills=[_skill("s1", mcps=["m1"]), _skill("s2")],
        mcps={"m1": StdioMcpServer(command="one"), "m2": StdioMcpServer(command="two")},
    )

    ir = resolve(registry)

    assert ir.name == "crew"
    assert _names(ir.skills) == ["s1", "s2"]
    assert _names(ir.mcps) == ["m1", "m2"]


def test_agent_skills_are_its_declared_list_not_a_closure() -> None:
    # s1 depends on s2, but the agent names only s1, so its file lists only s1.
    registry = _registry(
        agents=[_agent("a", skills=["s1"])],
        skills=[_skill("s1", skills=["s2"]), _skill("s2")],
    )

    agent = resolve(registry).agents[0]

    assert _names(agent.skills) == ["s1"]


def test_agent_skills_keep_declared_order() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s2", "s1"])],
        skills=[_skill("s1"), _skill("s2")],
    )

    assert _names(resolve(registry).agents[0].skills) == ["s2", "s1"]


def test_skill_cycle_is_not_an_error() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s1"])],
        skills=[_skill("s1", skills=["s2"]), _skill("s2", skills=["s1"])],
    )

    assert _names(resolve(registry).skills) == ["s1", "s2"]


def test_agents_in_crewfile_order_with_passthrough_fields() -> None:
    registry = _registry(
        agents=[_agent("solo", tools=["Read", "Edit"]), _agent("other")],
    )

    ir = resolve(registry)

    assert _names(ir.agents) == ["solo", "other"]
    assert ir.agents[0].tools == ("Read", "Edit")
    assert ir.agents[0].persona_path == Path("agents/solo.md")
    assert ir.agents[0].skills == ()


def test_instructions_pass_through() -> None:
    ir = resolve(_registry(instructions=Path("instructions.md")))
    assert ir.instructions == Path("instructions.md")


def test_empty_registry_resolves_to_named_empty_ir() -> None:
    assert resolve(_registry()) == ResolvedIR(name="crew")


def test_resolved_skill_carries_artifacts() -> None:
    skill = resolve(_registry(skills=[_skill("lint")])).skills[0]

    assert skill.path == Path("skills/lint")
    assert skill.skill_md == Path("skills/lint/SKILL.md")


def test_serialization_round_trips() -> None:
    registry = _registry(
        agents=[_agent("reviewer", skills=["lint"], tools=["Read"])],
        skills=[_skill("lint", skills=["format"], mcps=["github"]), _skill("format")],
        mcps={"github": StdioMcpServer(command="gh-mcp", args=["serve"])},
        instructions=Path("instructions.md"),
    )

    ir = resolve(registry)
    restored = ResolvedIR.model_validate_json(ir.model_dump_json())

    assert restored == ir
