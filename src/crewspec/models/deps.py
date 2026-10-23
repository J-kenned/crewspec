"""Dependency declarations for skills and agents.

Dependencies are reference checks: every name must be declared in the crewfile.
They do not select what is emitted; every declared skill and MCP is installed.
MCPs are global, so an agent declares none (the loader rejects a legacy
``depends_on.mcps`` with an explanatory message).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillDeps(BaseModel):
    """What a skill depends on: other skills and MCP servers."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)


class AgentDeps(BaseModel):
    """What an agent depends on: the skills listed in its emitted agent file."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
