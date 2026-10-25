"""Agent definitions."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crewspec.models.deps import AgentDeps
from crewspec.models.runtime import (
    RUNTIME_BLOCKS,
    Approvals,
    Channel,
    Limits,
    Sandbox,
    Secret,
    Trigger,
)

#: Agent-level escape hatch: ``x-<provider>`` blocks are kept opaque for the
#: runtime that owns them. Every other unknown key is still rejected.
EXTENSION_KEY = re.compile(r"^x-[a-z0-9-]+$")


class AgentDef(BaseModel):
    """An agent: a persona pointer, allowed tools, dependencies, and (0.2) its
    runtime blocks. Each 0.2 block is optional and lives on the agent only."""

    model_config = ConfigDict(extra="forbid")

    persona: str
    # Light passthrough: tool names (Read, Edit, Bash, mcp patterns) evolve per
    # target, so they are kept as free-form strings rather than constrained.
    allowed_tools: list[str] = Field(default_factory=list)
    depends_on: AgentDeps = Field(default_factory=AgentDeps)

    triggers: list[Trigger] | None = None
    channels: list[Channel] | None = None
    sandbox: Sandbox | None = None
    secrets: dict[str, Secret] | None = None
    limits: Limits | None = None
    approvals: Approvals | None = None
    #: ``x-*`` blocks keyed by their full key, stored as authored.
    extensions: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_extensions(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "extensions" in data:
            raise ValueError("`extensions` is reserved; write x-<name> keys instead")
        found = {
            k: v
            for k, v in data.items()
            if isinstance(k, str) and EXTENSION_KEY.match(k)
        }
        if not found:
            return data
        for key, value in found.items():
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be a mapping")
        rest = {k: v for k, v in data.items() if k not in found}
        rest["extensions"] = found
        return rest

    @model_validator(mode="after")
    def _one_approvals_inbox(self) -> AgentDef:
        inboxes = sum(c.approvals_inbox for c in self.channels or [])
        if inboxes > 1:
            raise ValueError("at most one channel may set approvals_inbox: true")
        if self.approvals is not None and inboxes != 1:
            raise ValueError(
                "approvals needs exactly one channel with approvals_inbox: true"
            )
        return self

    def runtime_blocks(self) -> tuple[str, ...]:
        """Names of the 0.2 blocks this agent sets, ``x-*`` keys included."""

        present = [b for b in RUNTIME_BLOCKS if getattr(self, b) is not None]
        return (*present, *self.extensions)
