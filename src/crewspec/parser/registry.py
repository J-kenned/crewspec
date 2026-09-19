"""The in-memory node graph produced by the loader.

The registry keys agents, skills, and MCP servers by name so the resolver
can walk dependency edges without re-reading the disk. Each node pairs
its source definition with the artifacts the emitter will copy and, for skills,
the dependency set merged from the crewfile and ``SKILL.md`` frontmatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from crewspec.models.agent import AgentDef
from crewspec.models.crew import CrewDoc
from crewspec.models.deps import SkillDeps
from crewspec.models.mcp import McpServer
from crewspec.models.skill import SkillDef


@dataclass(frozen=True)
class AgentNode:
    """An agent definition with its persona pointer resolved on disk."""

    definition: AgentDef
    persona: Path


@dataclass(frozen=True)
class SkillNode:
    """A skill definition with its artifacts resolved and its deps merged.

    ``deps`` is the merged set (frontmatter floor ∪ crewfile). ``root`` is the
    skill directory the emitter copies whole; ``skill_md`` is its ``SKILL.md``
    entry point.
    """

    definition: SkillDef
    root: Path
    skill_md: Path
    deps: SkillDeps


@dataclass(frozen=True)
class NodeRegistry:
    """All nodes in a loaded crew, keyed by name within each kind."""

    agents: dict[str, AgentNode] = field(default_factory=dict)
    skills: dict[str, SkillNode] = field(default_factory=dict)
    mcps: dict[str, McpServer] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedCrew:
    """The result of loading a crewfile: the document, graph, and its root."""

    doc: CrewDoc
    registry: NodeRegistry
    #: Directory the crewfile lives in, all artifact paths resolve against it.
    root: Path
