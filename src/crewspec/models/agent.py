"""Agent definitions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from crewspec.models.deps import AgentDeps


class AgentDef(BaseModel):
    """An agent: a persona pointer, allowed tools, and dependencies."""

    model_config = ConfigDict(extra="forbid")

    name: str
    persona: str
    # Light passthrough: tool names (Read, Edit, Bash, mcp patterns) evolve per
    # target, so they are kept as free-form strings rather than constrained.
    allowed_tools: list[str] = Field(default_factory=list)
    depends_on: AgentDeps = Field(default_factory=AgentDeps)
