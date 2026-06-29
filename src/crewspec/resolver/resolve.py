"""Resolve the parsed node graph into the :class:`ResolvedIR`.

This is the heart of crewspec: a pure, no-IO transform from the loader's
:class:`~crewspec.parser.registry.NodeRegistry` into the target-agnostic IR every
emitter consumes. It performs four passes, all deterministic so golden output is
stable:

1. a transitive skill closure per agent (``agent → skills → (skills)``),
   dependency-first, with skill→skill cycle detection;
2. the MCP closure for each agent (its own MCPs plus every closure skill's),
   deduplicated;
3. a topological order over the agent graph with agent→agent cycle detection —
   agent edges order emission only, they do not widen a closure;
4. the global deduplicated MCP set, the union of every agent's MCPs.

"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator

from crewspec.ir.resolved import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models.mcp import McpServer
from crewspec.parser.registry import NodeRegistry, SkillNode
from crewspec.resolver.errors import ConflictError, CycleError, NodeKind


def resolve(registry: NodeRegistry) -> ResolvedIR:
    """Resolve ``registry`` into the IR.

    The registry is assumed reference-validated: every ``depends_on`` entry
    resolves to a defined node, which the loader guarantees. An unvalidated
    registry with a dangling reference surfaces as a ``KeyError``.

    Raises :class:`~crewspec.resolver.errors.CycleError` on a skill or agent
    dependency cycle and :class:`~crewspec.resolver.errors.ConflictError` when one
    name carries differing definitions.
    """

    resolved_agents: list[ResolvedAgent] = []
    for name in _agent_order(registry):
        node = registry.agents[name]
        deps = node.definition.depends_on

        skill_names = _skill_closure(registry, deps.skills)
        skills = merge_skills((sn, registry.skills[sn]) for sn in skill_names)
        mcps = merge_mcps(_agent_mcps(registry, deps.mcps, skill_names))

        resolved_agents.append(
            ResolvedAgent(
                name=name,
                persona_path=node.persona,
                tools=tuple(node.definition.allowed_tools),
                skills=tuple(skills),
                mcps=tuple(mcps),
                requires_agents=tuple(deps.agents),
            )
        )

    global_mcps = merge_mcps(
        (mcp.name, mcp.server) for agent in resolved_agents for mcp in agent.mcps
    )
    return ResolvedIR(agents=tuple(resolved_agents), mcps=tuple(global_mcps))


def merge_mcps(items: Iterable[tuple[str, McpServer]]) -> list[ResolvedMcp]:
    """Deduplicate ``(name, server)`` pairs by name, in first-seen order.

    A name seen twice with structurally identical configs collapses to one, a
    name seen with differing configs raises :class:`ConflictError`. The conflict
    path is dormant within a single registry (names are unique there) but is the
    guard ``include`` composition will rely on.
    """

    merged: dict[str, McpServer] = {}
    for name, server in items:
        existing = merged.get(name)
        if existing is None:
            merged[name] = server
        elif existing != server:
            raise ConflictError("mcp", name)
    return [ResolvedMcp(name=name, server=server) for name, server in merged.items()]


def merge_skills(items: Iterable[tuple[str, SkillNode]]) -> list[ResolvedSkill]:
    """Deduplicate ``(name, node)`` pairs by name, in first-seen order.

    Like :func:`merge_mcps`: identical repeats collapse, differing definitions
    for one name raise :class:`ConflictError`.
    """

    merged: dict[str, SkillNode] = {}
    for name, node in items:
        existing = merged.get(name)
        if existing is None:
            merged[name] = node
        elif existing != node:
            raise ConflictError("skill", name)
    return [_to_resolved_skill(name, node) for name, node in merged.items()]


def _agent_mcps(
    registry: NodeRegistry,
    direct: list[str],
    skill_names: list[str],
) -> list[tuple[str, McpServer]]:
    """The agent's MCP references: its own first, then its skills', in order."""

    items: list[tuple[str, McpServer]] = [(m, registry.mcps[m]) for m in direct]
    for skill in skill_names:
        for m in registry.skills[skill].deps.mcps:
            items.append((m, registry.mcps[m]))
    return items


def _skill_closure(registry: NodeRegistry, roots: list[str]) -> list[str]:
    """Transitive skills reachable from ``roots``, dependency-first, deduped.

    A skill is emitted only after its dependencies, so the result is a valid
    topological order. A back edge onto the current DFS path is a cycle and
    raises :class:`CycleError` carrying the offending path.
    """

    return _dfs_order(roots, lambda name: registry.skills[name].deps.skills, "skill")


def _agent_order(registry: NodeRegistry) -> list[str]:
    """Topological order over the agent graph, dependents last, deduped.

    Agent→agent edges order emission only; they are not followed when building a
    skill or MCP closure. A back edge raises :class:`CycleError`.
    """

    return _dfs_order(
        list(registry.agents),
        lambda name: registry.agents[name].definition.depends_on.agents,
        "agent",
    )


def _dfs_order(
    roots: list[str],
    neighbors: Callable[[str], list[str]],
    kind: NodeKind,
) -> list[str]:
    """Iterative depth-first topological order over a node graph.

    Emits each node after all its dependencies, deduplicated, neighbours visited
    in declared order. A back edge onto the active path raises :class:`CycleError`
    with the cycle sliced from its entry point. The walk is iterative (an explicit
    frame stack) rather than recursive so a pathologically deep dependency chain
    cannot exhaust the interpreter's stack, it would otherwise surface as a bare
    ``RecursionError`` outside this module's error contract.
    """

    order: list[str] = []
    done: set[str] = set()
    on_path: set[str] = set()

    for root in roots:
        if root in done:
            continue
        path: list[str] = [root]
        on_path.add(root)
        frames: list[tuple[str, Iterator[str]]] = [(root, iter(neighbors(root)))]
        while frames:
            name, deps = frames[-1]
            descended = False
            for dep in deps:
                if dep in done:
                    continue
                if dep in on_path:
                    raise CycleError(kind, path[path.index(dep) :] + [dep])
                on_path.add(dep)
                path.append(dep)
                frames.append((dep, iter(neighbors(dep))))
                descended = True
                break
            if descended:
                continue
            frames.pop()
            on_path.discard(name)
            path.pop()
            done.add(name)
            order.append(name)
    return order


def _to_resolved_skill(name: str, node: SkillNode) -> ResolvedSkill:
    return ResolvedSkill(
        name=name,
        path=node.root,
        skill_md=node.skill_md,
        extra_dirs=node.extra_dirs,
        description=node.definition.description,
    )
