"""Skill definitions and the reserved ``x-crewspec`` frontmatter contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from crewspec.models.deps import SkillDeps


class SkillDef(BaseModel):
    """A skill: a pointer to a ``SKILL.md`` directory plus its dependencies."""

    model_config = ConfigDict(extra="forbid")

    path: str
    depends_on: SkillDeps = Field(default_factory=SkillDeps)


class XCrewspecFrontmatter(BaseModel):
    """The ``x-crewspec`` block embedded in ``SKILL.md`` frontmatter.

    The contract for skill-declared dependencies, the parser
    reads this block out of skill frontmatter.
    """

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
