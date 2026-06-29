"""Path-aware errors raised while loading a crewfile.

Every failure carries the offending file path and, where the underlying source
makes it feasible (YAML syntax), the line it occurred on. The string form is
``path:line: message`` so errors read like compiler diagnostics.
"""

from __future__ import annotations

from pathlib import Path


class CrewfileError(Exception):
    """Base class for crewfile loading failures."""

    def __init__(self, message: str, *, path: Path, line: int | None = None) -> None:
        self.message = message
        self.path = path
        self.line = line
        super().__init__(self._format())

    def _format(self) -> str:
        location = str(self.path)
        if self.line is not None:
            location = f"{location}:{self.line}"
        return f"{location}: {self.message}"


class CrewfileSyntaxError(CrewfileError):
    """The crewfile is not valid YAML or does not match the schema."""


class MissingArtifactError(CrewfileError):
    """A referenced artifact file or directory does not exist."""


class FrontmatterError(CrewfileError):
    """A skill's ``SKILL.md`` frontmatter is missing or malformed."""


class UnknownReferenceError(CrewfileError):
    """A ``depends_on`` entry names a node not defined in the crewfile."""
