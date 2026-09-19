"""Emitters render the crewspec IR into target-specific artifacts"""

from crewspec.emitters.base import Emitter
from crewspec.emitters.claude import ClaudeEmitter
from crewspec.emitters.codex import CodexEmitter
from crewspec.emitters.errors import EmitError, MissingPersonaDescription

EMITTERS: dict[str, type[Emitter]] = {
    ClaudeEmitter.name: ClaudeEmitter,
    CodexEmitter.name: CodexEmitter,
}

__all__ = [
    "EMITTERS",
    "ClaudeEmitter",
    "CodexEmitter",
    "Emitter",
    "EmitError",
    "MissingPersonaDescription",
]
