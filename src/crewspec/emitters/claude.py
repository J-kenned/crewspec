"""Emitter that renders a crewspec into Claude-compatible artifacts."""

from pathlib import Path

from crewspec.emitters.base import Emitter


class ClaudeEmitter(Emitter):
    """Render the crewspec IR into artifacts for the Claude runtime."""

    name = "claude"

    def emit(self, ir: object, dest: Path) -> None:
        raise NotImplementedError("ClaudeEmitter.emit is not yet implemented")
