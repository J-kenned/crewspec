"""The resolved intermediate representation every emitter consumes.

The resolver collapses the parsed node graph into this shape: the agents in
crewfile order, each carrying its own transitive skill and MCP closure,
plus the global deduplicated MCP set. It is the *contract* between resolution and
emission serializable data with no behaviour and no IO.

MCP configs are carried unresolved. ``${...}`` references stay intact and
nothing here reads a secret or environment value. Emitters parse those
references with :func:`crewspec.models.interpolate.parse_refs` and write them
into the output for the host tool to expand. ``${VAR}`` is copied unchanged
and ``${secret:NAME}`` becomes ``${NAME}`` (Codex writes a lone header secret
as the bare name under ``env_http_headers``). ``${file:PATH}`` is copied
verbatim with a warning. No emitter reads or writes a secret value.
:mod:`crewspec.resolver.secrets` can resolve values locally, but the CLI
pipeline does not call it.
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


class ResolvedAgent(BaseModel):
    """An agent with its transitive skill and MCP closure resolved.

    ``tools`` (the agent's ``allowed_tools`` grant list) and ``mcps`` (the
    resolved server configs) are kept deliberately **separate**: each emitter
    maps MCPs to its own tool-grant syntax. ``skills`` and ``mcps`` are
    dependency-ordered and deduplicated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    persona_path: Path
    tools: tuple[str, ...] = ()
    skills: tuple[ResolvedSkill, ...] = ()
    mcps: tuple[ResolvedMcp, ...] = ()


class ResolvedIR(BaseModel):
    """The whole crew resolved: ordered agents plus the global MCP set.

    ``agents`` is in crewfile declaration order. ``mcps`` is
    the union of every agent's MCP closure, deduplicated, in first-seen order,
    the single shared set a share-model emitter installs once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agents: tuple[ResolvedAgent, ...] = ()
    mcps: tuple[ResolvedMcp, ...] = ()
