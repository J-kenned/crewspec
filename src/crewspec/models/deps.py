"""Dependency declarations for skills and agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillDeps(BaseModel):
    """What a skill depends on: other skills and MCP servers."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)


class AgentDeps(BaseModel):
    """What an agent depends on: skills, MCP servers, and other agents."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
