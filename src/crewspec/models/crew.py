"""The top-level crewspec document."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from crewspec.models.agent import AgentDef
from crewspec.models.mcp import McpServer
from crewspec.models.skill import SkillDef


class CrewDoc(BaseModel):
    """A parsed crewspec document.

    One schema serves both ``.crewfile`` and ``crewspec.yaml``. Agents, skills,
    and MCP servers are identified by their map key; there is no separate
    ``name`` field. The composition keys ``include`` and ``crew`` are rejected
    by the loader with a one-line error before the document reaches this model;
    ``extra="forbid"`` still rejects them here as a fallback.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    skills: dict[str, SkillDef] = Field(default_factory=dict)
    mcps: dict[str, McpServer] = Field(default_factory=dict)
