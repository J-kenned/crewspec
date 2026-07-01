"""Errors raised while emitting target artifacts.

Emission is the IO boundary: it reads persona files and writes the target
tree, so its failures point at a *source file* the way the parser's do, not at
a graph node. They subclass a shared :class:`EmitError` rather than the
parser's ``CrewfileError`` because an emit failure is not a crewfile-parse
failure, even when it implicates a file the crewfile referenced.
"""

from __future__ import annotations

from pathlib import Path


class EmitError(Exception):
    """Base class for emitter failures."""


class MissingPersonaDescription(EmitError):
    """An agent's persona file declares no ``description`` frontmatter.

    Claude requires a subagent ``description`` to decide when to delegate, and
    the IR carries none, so the persona file is the only source. A persona with
    no frontmatter ``description`` cannot produce a valid agent file.
    """

    def __init__(self, agent: str, persona: Path) -> None:
        self.agent = agent
        self.persona = persona
        super().__init__(
            f"agent '{agent}' persona '{persona}' has no 'description' frontmatter; "
            f"Claude requires one to delegate to the subagent"
        )
