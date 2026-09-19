"""The resolved intermediate representation every emitter consumes.

The resolver collapses the parsed node graph into this shape: a topologically
ordered list of agents, each carrying its own transitive skill and MCP closure,
plus the global deduplicated MCP set. It is the *contract* between resolution and
emission serializable data with no behaviour and no IO.

MCP configs are carried *unresolved* ``${...}`` references stay intact. Secret
and environment substitution is a separate, IO-bearing concern handled by
:mod:`crewspec.resolver.secrets` at emit time, never here.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from crewspec.models.mcp import McpServer


class ResolvedMcp(BaseModel):
    """A named MCP server config, carried from the registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    server: McpServer


class ResolvedSkill(BaseModel):
    """A skill flattened to the artifacts an emitter copies.

    ``path`` is the skill root — a skill *is* its directory, so an emitter copies
    the whole tree (minus VCS/OS junk). ``skill_md`` locates the ``SKILL.md``
    entry point within it. Paths are carried from the loader (resolved against
    the crewfile root); normalising them for portable output is the emitter's
    concern.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: Path
    skill_md: Path
    description: str | None = None


class ResolvedAgent(BaseModel):
    """An agent with its transitive skill and MCP closure resolved.

    ``tools`` (the agent's ``allowed_tools`` grant list) and ``mcps`` (the
    resolved server configs) are kept deliberately **separate**: each emitter
    maps MCPs to its own tool-grant syntax. ``skills`` and ``mcps`` are
    dependency-ordered and deduplicated. ``requires_agents`` records agent→agent
    edges as a relationship only, they order emission but do not contribute to
    this agent's skill or MCP closure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    persona_path: Path
    tools: tuple[str, ...] = ()
    skills: tuple[ResolvedSkill, ...] = ()
    mcps: tuple[ResolvedMcp, ...] = ()
    requires_agents: tuple[str, ...] = ()


class ResolvedIR(BaseModel):
    """The whole crew resolved: ordered agents plus the global MCP set.

    ``agents`` is topologically ordered (depended-on agents first). ``mcps`` is
    the union of every agent's MCP closure, deduplicated, in first-seen order,
    the single shared set a share-model emitter installs once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agents: tuple[ResolvedAgent, ...] = ()
    mcps: tuple[ResolvedMcp, ...] = ()
