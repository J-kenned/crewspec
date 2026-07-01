"""Emitters render the crewspec IR into target-specific artifacts"""

from crewspec.emitters.base import Emitter
from crewspec.emitters.claude import ClaudeEmitter
from crewspec.emitters.errors import EmitError, MissingPersonaDescription

__all__ = [
    "ClaudeEmitter",
    "Emitter",
    "EmitError",
    "MissingPersonaDescription",
]
