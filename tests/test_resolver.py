"""Tests for the resolver and the resolved IR.

Registries are built in memory with fake paths: ``resolve`` is pure and does no
IO, so the paths it carries through need not exist on disk.
"""

from pathlib import Path
from typing import Iterable

import pytest

from crewspec.ir import ResolvedIR
from crewspec.models import AgentDef, AgentDeps, SkillDef, SkillDeps, StdioMcpServer
from crewspec.models.mcp import McpServer
from crewspec.parser.registry import AgentNode, NodeRegistry, SkillNode
from crewspec.resolver import (
    ConflictError,
    CycleError,
    merge_mcps,
    merge_skills,
    resolve,
)


def _agent(
    name: str,
    *,
    skills: Iterable[str] = (),
    mcps: Iterable[str] = (),
    agents: Iterable[str] = (),
    tools: Iterable[str] = (),
) -> AgentNode:
    return AgentNode(
        definition=AgentDef(
            persona=f"agents/{name}.md",
            allowed_tools=list(tools),
            depends_on=AgentDeps(
                skills=list(skills), mcps=list(mcps), agents=list(agents)
            ),
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
) -> NodeRegistry:
    # The node's identity is its map key; the helpers encode it in the path.
    return NodeRegistry(
        agents={a.persona.stem: a for a in agents},
        skills={s.root.name: s for s in skills},
        mcps=mcps or {},
    )


def _names(items: Iterable[object]) -> list[str]:
    return [item.name for item in items]  # type: ignore[attr-defined]


def test_diamond_deps_order_deps_first_and_dedup() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s1"])],
        skills=[
            _skill("s1", skills=["s2", "s3"]),
            _skill("s2", skills=["s4"]),
            _skill("s3", skills=["s4"]),
            _skill("s4"),
        ],
    )

    agent = resolve(registry).agents[0]

    # s4 appears once, before its dependents, which precede the root s1.
    assert _names(agent.skills) == ["s4", "s2", "s3", "s1"]


def test_shared_mcp_deduped_per_agent_and_globally() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s1", "s2"])],
        skills=[_skill("s1", mcps=["m"]), _skill("s2", mcps=["m"])],
        mcps={"m": StdioMcpServer(command="serve")},
    )

    ir = resolve(registry)

    assert _names(ir.agents[0].mcps) == ["m"]
    assert _names(ir.mcps) == ["m"]


def test_skill_cycle_raises_with_path() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s1"])],
        skills=[_skill("s1", skills=["s2"]), _skill("s2", skills=["s1"])],
    )

    with pytest.raises(CycleError, match="skill cycle detected") as exc:
        resolve(registry)
    assert exc.value.kind == "skill"
    assert exc.value.cycle == ["s1", "s2", "s1"]


def test_agent_cycle_raises_with_path() -> None:
    registry = _registry(
        agents=[_agent("a1", agents=["a2"]), _agent("a2", agents=["a1"])],
    )

    with pytest.raises(CycleError, match="agent cycle detected") as exc:
        resolve(registry)
    assert exc.value.kind == "agent"
    assert exc.value.cycle == ["a1", "a2", "a1"]


def test_merge_mcps_dedups_identical_and_rejects_conflict() -> None:
    server = StdioMcpServer(command="serve")
    assert _names(
        merge_mcps([("m", server), ("m", StdioMcpServer(command="serve"))])
    ) == ["m"]

    with pytest.raises(ConflictError, match="conflicting mcp 'm'") as exc:
        merge_mcps([("m", server), ("m", StdioMcpServer(command="other"))])
    assert exc.value.kind == "mcp"
    assert exc.value.name == "m"


def test_merge_skills_dedups_identical_and_rejects_conflict() -> None:
    node = _skill("x", mcps=["a"])
    assert _names(merge_skills([("x", node), ("x", _skill("x", mcps=["a"]))])) == ["x"]

    with pytest.raises(ConflictError, match="conflicting skill 'x'") as exc:
        merge_skills([("x", node), ("x", _skill("x", mcps=["b"]))])
    assert exc.value.kind == "skill"
    assert exc.value.name == "x"


def test_deep_skill_chain_orders_leaf_first() -> None:
    chain = [_skill(f"s{i}", skills=[f"s{i + 1}"]) for i in range(1, 5)]
    chain.append(_skill("s5"))
    registry = _registry(agents=[_agent("a", skills=["s1"])], skills=chain)

    agent = resolve(registry).agents[0]

    assert _names(agent.skills) == ["s5", "s4", "s3", "s2", "s1"]


def test_agent_edges_order_emission_without_polluting_closure() -> None:
    registry = _registry(
        agents=[
            _agent("a1", skills=["s_a"], agents=["a2"]),
            _agent("a2", skills=["s_b"]),
        ],
        skills=[_skill("s_a"), _skill("s_b")],
    )

    ir = resolve(registry)

    # Depended-on agent is emitted first.
    assert _names(ir.agents) == ["a2", "a1"]
    a1 = next(a for a in ir.agents if a.name == "a1")
    assert a1.requires_agents == ("a2",)
    # a1's closure is its own skills only — a2's skill does not leak in.
    assert _names(a1.skills) == ["s_a"]


def test_global_mcp_set_unions_across_agents_in_order() -> None:
    registry = _registry(
        agents=[
            _agent("a1", mcps=["m1", "shared"]),
            _agent("a2", mcps=["shared", "m2"]),
        ],
        mcps={
            "m1": StdioMcpServer(command="one"),
            "m2": StdioMcpServer(command="two"),
            "shared": StdioMcpServer(command="shared"),
        },
    )

    ir = resolve(registry)

    assert _names(ir.mcps) == ["m1", "shared", "m2"]


def test_serialization_round_trips() -> None:
    registry = _registry(
        agents=[_agent("reviewer", skills=["lint"], mcps=["github"], tools=["Read"])],
        skills=[
            _skill("lint", skills=["format"], mcps=["github"]),
            _skill("format"),
        ],
        mcps={"github": StdioMcpServer(command="gh-mcp", args=["serve"])},
    )

    ir = resolve(registry)
    restored = ResolvedIR.model_validate_json(ir.model_dump_json())

    assert restored == ir


def test_passthrough_and_empty_deps() -> None:
    registry = _registry(agents=[_agent("solo", tools=["Read", "Edit"])])

    agent = resolve(registry).agents[0]

    assert agent.tools == ("Read", "Edit")
    assert agent.persona_path == Path("agents/solo.md")
    assert agent.skills == ()
    assert agent.mcps == ()
    assert agent.requires_agents == ()


def test_empty_registry_resolves_to_empty_ir() -> None:
    ir = resolve(_registry())
    assert ir == ResolvedIR()


def test_resolved_skill_carries_artifacts() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["lint"])],
        skills=[_skill("lint")],
    )

    skill = resolve(registry).agents[0].skills[0]

    assert skill.path == Path("skills/lint")
    assert skill.skill_md == Path("skills/lint/SKILL.md")


def test_cycle_path_starts_at_entry_not_dfs_root() -> None:
    # s0 is a clean prefix into a 3-node cycle; the reported path must begin at
    # the cycle entry s1, not the DFS root s0.
    registry = _registry(
        agents=[_agent("a", skills=["s0"])],
        skills=[
            _skill("s0", skills=["s1"]),
            _skill("s1", skills=["s2"]),
            _skill("s2", skills=["s3"]),
            _skill("s3", skills=["s1"]),
        ],
    )

    with pytest.raises(CycleError) as exc:
        resolve(registry)
    assert exc.value.cycle == ["s1", "s2", "s3", "s1"]


def test_skill_self_loop_raises() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s1"])],
        skills=[_skill("s1", skills=["s1"])],
    )

    with pytest.raises(CycleError) as exc:
        resolve(registry)
    assert exc.value.kind == "skill"
    assert exc.value.cycle == ["s1", "s1"]


def test_agent_self_loop_raises() -> None:
    registry = _registry(agents=[_agent("a1", agents=["a1"])])

    with pytest.raises(CycleError) as exc:
        resolve(registry)
    assert exc.value.kind == "agent"
    assert exc.value.cycle == ["a1", "a1"]


def test_agent_diamond_orders_shared_dep_once_without_leakage() -> None:
    registry = _registry(
        agents=[
            _agent("root", skills=["s_root"], agents=["a2", "a3"]),
            _agent("a2", skills=["s2"], agents=["a1"]),
            _agent("a3", skills=["s3"], agents=["a1"]),
            _agent("a1", skills=["s1"]),
        ],
        skills=[_skill("s_root"), _skill("s1"), _skill("s2"), _skill("s3")],
    )

    order = _names(resolve(registry).agents)

    # Shared dependency a1 is emitted exactly once, before both dependents,
    # which both precede the root.
    assert order.count("a1") == 1
    assert order.index("a1") < order.index("a2") < order.index("root")
    assert order.index("a1") < order.index("a3") < order.index("root")
    root = next(a for a in resolve(registry).agents if a.name == "root")
    # Agent edges never widen a closure: root keeps only its own skill.
    assert _names(root.skills) == ["s_root"]


def test_per_agent_mcp_order_own_block_first_then_skills() -> None:
    registry = _registry(
        agents=[_agent("a", skills=["s1"], mcps=["shared", "own"])],
        skills=[_skill("s1", mcps=["shared", "skillonly"])],
        mcps={
            "own": StdioMcpServer(command="own"),
            "shared": StdioMcpServer(command="shared"),
            "skillonly": StdioMcpServer(command="skillonly"),
        },
    )

    agent = resolve(registry).agents[0]

    # Own MCPs first (declared order), then skill MCPs; 'shared' dedups to its
    # first-seen position in the own block.
    assert _names(agent.mcps) == ["shared", "own", "skillonly"]


def test_deep_skill_chain_does_not_overflow_the_stack() -> None:
    # A chain far deeper than CPython's recursion limit resolves cleanly via the
    # iterative walk rather than raising RecursionError.
    depth = 5000
    chain = [_skill(f"s{i}", skills=[f"s{i + 1}"]) for i in range(depth)]
    chain.append(_skill(f"s{depth}"))
    registry = _registry(agents=[_agent("a", skills=["s0"])], skills=chain)

    agent = resolve(registry).agents[0]

    assert _names(agent.skills) == [f"s{i}" for i in range(depth, -1, -1)]
