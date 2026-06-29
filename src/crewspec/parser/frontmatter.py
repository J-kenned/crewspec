"""Read the reserved ``x-crewspec`` dependency block from ``SKILL.md``.

A skill directory carries a ``SKILL.md`` whose YAML frontmatter (the leading
``---``-fenced block) may include an ``x-crewspec`` mapping declaring the skill's
intrinsic dependencies. Standard frontmatter keys (``name``, ``description``,
...) are ignored here; only ``x-crewspec`` is extracted, and it is validated
against :class:`~crewspec.models.skill.XCrewspecFrontmatter` so unknown keys are
rejected.

A skill with no ``x-crewspec`` block simply declares no frontmatter deps. A
``SKILL.md`` with no frontmatter fence at all, or with malformed YAML inside it,
is an error, skill authors are expected to ship valid frontmatter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from crewspec.models.skill import XCrewspecFrontmatter
from crewspec.parser.errors import FrontmatterError

_FENCE = "---"


def parse_skill_frontmatter(skill_md: Path) -> XCrewspecFrontmatter:
    """Return the ``x-crewspec`` deps declared in ``skill_md``'s frontmatter.

    Raises :class:`FrontmatterError` if the frontmatter fence is missing or the
    block is not valid YAML / not a mapping, or if the ``x-crewspec`` block has
    an unexpected shape.
    """

    text = skill_md.read_text()
    block = _extract_block(text, skill_md)

    try:
        data = yaml.safe_load(block)
    except yaml.MarkedYAMLError as exc:
        raise FrontmatterError(
            f"malformed YAML in SKILL.md frontmatter: {exc.problem}",
            path=skill_md,
            line=_mark_line(exc),
        ) from exc
    except yaml.YAMLError as exc:
        raise FrontmatterError(
            f"malformed YAML in SKILL.md frontmatter: {exc}", path=skill_md
        ) from exc

    if not isinstance(data, dict):
        raise FrontmatterError("SKILL.md frontmatter must be a mapping", path=skill_md)

    raw = data.get("x-crewspec")
    if raw is None:
        return XCrewspecFrontmatter()
    if not isinstance(raw, dict):
        raise FrontmatterError(
            "'x-crewspec' frontmatter block must be a mapping", path=skill_md
        )

    return _validate_block(raw, skill_md)


def _extract_block(text: str, skill_md: Path) -> str:
    """Return the YAML between the leading ``---`` fences."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise FrontmatterError(
            "SKILL.md is missing its '---' frontmatter fence", path=skill_md
        )

    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            return "\n".join(lines[1:index])

    raise FrontmatterError("SKILL.md frontmatter fence is never closed", path=skill_md)


def _validate_block(raw: dict[str, Any], skill_md: Path) -> XCrewspecFrontmatter:
    try:
        return XCrewspecFrontmatter.model_validate(raw)
    except ValueError as exc:
        raise FrontmatterError(
            f"invalid 'x-crewspec' frontmatter block: {exc}", path=skill_md
        ) from exc


def _mark_line(exc: yaml.MarkedYAMLError) -> int | None:
    """Translate a YAML problem mark (0-based) to a 1-based line number.

    The mark is relative to the frontmatter block; offset by one for the opening
    fence line so it points into the actual ``SKILL.md``.
    """

    if exc.problem_mark is None:
        return None
    return exc.problem_mark.line + 2
