"""The top-level crewspec document."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crewspec.models.agent import AgentDef
from crewspec.models.mcp import McpServer
from crewspec.models.skill import SkillDef

#: Composition keys reserved for ``crewspec.yaml``; rejected until it lands.
_UNSUPPORTED_KEYS = ("include", "crew")


class CrewDoc(BaseModel):
    """A parsed crewspec document.

    One schema serves both ``.crewfile`` and ``crewspec.yaml``. Agents, skills,
    and MCP servers are identified by their map key; there is no separate
    ``name`` field. The composition keys ``include`` and ``crew`` are rejected
    with a clear error until composition is supported.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    skills: dict[str, SkillDef] = Field(default_factory=dict)
    mcps: dict[str, McpServer] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_composition_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in _UNSUPPORTED_KEYS:
                if key in data:
                    raise ValueError(
                        f"`{key}` is not supported yet; see Not in V1 in the README"
                    )
        return data
