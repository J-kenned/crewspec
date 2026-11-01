"""The top-level crewspec document."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crewspec.models.agent import AgentDef
from crewspec.models.mcp import McpServer
from crewspec.models.skill import SkillDef

#: Pack names double as directory and plugin names on every target.
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class ModDef(BaseModel):
    """A mod: a pointer to a Claude Code plugin directory with a hooks module."""

    model_config = ConfigDict(extra="forbid")

    path: str


class CrewDoc(BaseModel):
    """A parsed crewspec document.

    One schema serves both ``.crewfile`` and ``crewspec.yaml``. ``name``
    identifies the pack: it becomes the plugin directory and manifest name on
    each target. Agents, skills, mods, and MCP servers are identified by their
    map key. ``instructions`` points at a markdown file the Claude target writes
    into ``CLAUDE.md`` as a managed block. The composition keys ``include`` and
    ``crew`` are rejected by the loader with a one-line error before the
    document reaches this model; ``extra="forbid"`` still rejects them here as
    a fallback.

    ``version`` gates the schema: the per-agent runtime blocks and ``x-*``
    escape hatch are 0.2 only, so a 0.1 document that uses one fails naming
    the agent and the block rather than silently carrying it.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal["0.1", "0.2"]
    name: str = Field(pattern=NAME_PATTERN)
    instructions: str | None = None
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    skills: dict[str, SkillDef] = Field(default_factory=dict)
    mcps: dict[str, McpServer] = Field(default_factory=dict)
    mods: dict[str, ModDef] = Field(default_factory=dict)
    #: Opaque: each value is written verbatim to the Claude manifest's ``lspServers``.
    lsp: dict[str, dict[str, object]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _mod_keys_are_plugin_names(self) -> CrewDoc:
        """A mod key becomes ``.claude/skills/<key>/``, a sibling of the pack's
        own plugin, so it must be a plain name and must not be the pack's."""

        for key in self.mods:
            if not re.fullmatch(NAME_PATTERN, key):
                raise ValueError(
                    f"mods.{key} is not a valid mod name "
                    "(lowercase letters, digits, and hyphens)"
                )
            if key == self.name:
                raise ValueError(
                    f"mods.{key} has the pack's name and would overwrite its plugin "
                    "directory; rename the mod"
                )
        return self

    @model_validator(mode="after")
    def _runtime_blocks_need_0_2(self) -> CrewDoc:
        if self.version != "0.1":
            return self
        for name, agent in self.agents.items():
            blocks = agent.runtime_blocks()
            if blocks:
                raise ValueError(
                    f'agents.{name}.{blocks[0]} needs version "0.2" '
                    f"(0.1 has no per-agent runtime blocks)"
                )
        return self
