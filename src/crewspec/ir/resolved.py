"""The resolved intermediate representation every emitter consumes.

The resolver flattens the parsed node graph into this shape: the pack name, the
optional instructions file, the agents in crewfile order, and every declared
skill and MCP server. It is the *contract* between resolution and emission:
serializable data with no behaviour and no IO.

MCP configs are carried unresolved. ``${...}`` references stay intact and
nothing here reads a secret or environment value. Emitters parse those
references with :func:`crewspec.models.interpolate.parse_refs` and write them
into the output for the host tool to expand. ``${VAR}`` is copied unchanged
and ``${secret:NAME}`` becomes ``${NAME}`` (Codex writes a lone header secret
as the bare name under ``env_http_headers``). No emitter reads or writes a
secret value. :mod:`crewspec.resolver.secrets` can resolve values locally, but
the CLI pipeline does not call it.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from crewspec.models.mcp import McpServer
from crewspec.models.runtime import (
    Approvals,
    Channel,
    Limits,
    Sandbox,
    Secret,
    Trigger,
)


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
    #: MCP server names the skill's ``x-crewspec.mcps`` (∪ crewfile) declares.
    mcps: tuple[str, ...] = ()


class ResolvedAgent(BaseModel):
    """An agent with the skills its emitted file lists.

    ``tools`` is the agent's ``allowed_tools`` grant list, passed through for
    each emitter to map onto its own syntax. ``skills`` are the agent's declared
    ``depends_on.skills`` in declared order; MCPs are global and live on the IR.
    ``withheld`` names the 0.2 runtime blocks (and ``x-*`` keys) the agent
    sets. The blocks themselves ride alongside, typed by the crewfile models,
    so an emitter with a home for one (Actions renders ``triggers``, Kiro
    reads ``x-operative.model``) can translate it; every other emitter
    reports it as withheld.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    persona_path: Path
    tools: tuple[str, ...] = ()
    skills: tuple[ResolvedSkill, ...] = ()
    withheld: tuple[str, ...] = ()
    triggers: tuple[Trigger, ...] = ()
    channels: tuple[Channel, ...] = ()
    sandbox: Sandbox | None = None
    secrets: dict[str, Secret] = {}
    limits: Limits | None = None
    approvals: Approvals | None = None
    extensions: dict[str, dict[str, object]] = {}
    #: Filled by ``crewspec validate``, not the resolver: per emitter target,
    #: what becomes of each block in ``withheld`` (delivered/translated/withheld).
    dispositions: dict[str, dict[str, str]] = {}


class ResolvedIR(BaseModel):
    """The whole pack resolved: identity, agents, and every skill and MCP.

    ``agents``, ``skills``, and ``mcps`` are in crewfile declaration order.
    ``instructions`` is the resolved path of the optional global instructions
    file, or ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    instructions: Path | None = None
    agents: tuple[ResolvedAgent, ...] = ()
    skills: tuple[ResolvedSkill, ...] = ()
    mcps: tuple[ResolvedMcp, ...] = ()
