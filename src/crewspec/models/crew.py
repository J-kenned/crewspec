"""The top-level crewspec document."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from crewspec.models.agent import AgentDef
from crewspec.models.mcp import McpServer
from crewspec.models.skill import SkillDef

#: Pack names double as directory and plugin names on every target.
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class CrewDoc(BaseModel):
    """A parsed crewspec document.

    One schema serves both ``.crewfile`` and ``crewspec.yaml``. ``name``
    identifies the pack: it becomes the plugin directory and manifest name on
    each target. Agents, skills, and MCP servers are identified by their map
    key. ``instructions`` points at a markdown file the Claude target writes
    into ``CLAUDE.md`` as a managed block. The composition keys ``include`` and
    ``crew`` are rejected by the loader with a one-line error before the
    document reaches this model; ``extra="forbid"`` still rejects them here as
    a fallback.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    name: str = Field(pattern=NAME_PATTERN)
    instructions: str | None = None
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    skills: dict[str, SkillDef] = Field(default_factory=dict)
    mcps: dict[str, McpServer] = Field(default_factory=dict)
