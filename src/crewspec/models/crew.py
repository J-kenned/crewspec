"""The top-level crewspec document."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crewspec.models.agent import AgentDef
from crewspec.models.mcp import McpServer
from crewspec.models.skill import SkillDef


class CrewDoc(BaseModel):
    """A parsed crewspec document.

    One schema serves both ``.crewfile`` (a single unit) and ``crewspec.yaml``
    (the composition superset). ``include`` and ``crew`` are parsed.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    skills: dict[str, SkillDef] = Field(default_factory=dict)
    mcps: dict[str, McpServer] = Field(default_factory=dict)

    # Todo: Parsed but not yet acted upon
    include: list[str] | None = None
    crew: dict[str, Any] | None = None
