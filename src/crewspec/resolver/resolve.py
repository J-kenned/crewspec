"""Resolve the parsed node graph into the :class:`ResolvedIR`.

A pure, no-IO transform from the loader's
:class:`~crewspec.parser.registry.NodeRegistry` into the target-agnostic IR every
emitter consumes. Every declared skill and MCP server is emitted; dependency
edges were already checked by the loader and select nothing here. Agents,
skills, and MCPs keep crewfile declaration order so golden output is stable.
"""

from __future__ import annotations

from crewspec.ir.resolved import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.parser.registry import NodeRegistry


def resolve(registry: NodeRegistry) -> ResolvedIR:
    """Resolve ``registry`` into the IR.

    The registry is assumed reference-validated: every ``depends_on`` entry
    resolves to a defined node, which the loader guarantees. An unvalidated
    registry with a dangling reference surfaces as a ``KeyError``.
    """

    skills = {
        name: ResolvedSkill(name=name, path=node.root, skill_md=node.skill_md)
        for name, node in registry.skills.items()
    }
    agents = [
        ResolvedAgent(
            name=name,
            persona_path=node.persona,
            tools=tuple(node.definition.allowed_tools),
            skills=tuple(skills[s] for s in node.definition.depends_on.skills),
        )
        for name, node in registry.agents.items()
    ]
    return ResolvedIR(
        name=registry.name,
        instructions=registry.instructions,
        agents=tuple(agents),
        skills=tuple(skills.values()),
        mcps=tuple(
            ResolvedMcp(name=name, server=server)
            for name, server in registry.mcps.items()
        ),
    )
