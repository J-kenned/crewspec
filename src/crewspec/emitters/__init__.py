"""Emitters render the crewspec IR into target-specific artifacts"""

from crewspec.emitters.actions import ActionsEmitter
from crewspec.emitters.base import Emitter
from crewspec.emitters.claude import ClaudeEmitter
from crewspec.emitters.codex import CodexEmitter
from crewspec.emitters.errors import EmitError, MissingPersonaDescription
from crewspec.emitters.kiro import KiroEmitter

EMITTERS: dict[str, type[Emitter]] = {
    ClaudeEmitter.name: ClaudeEmitter,
    CodexEmitter.name: CodexEmitter,
    KiroEmitter.name: KiroEmitter,
    ActionsEmitter.name: ActionsEmitter,
}


def dispositions(ir_blocks: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """Per-target disposition of each block in ``ir_blocks`` (an agent's
    ``withheld`` list): ``delivered``, ``translated`` or ``withheld``."""

    return {
        name: {block: cls.dispositions.get(block, "withheld") for block in ir_blocks}
        for name, cls in EMITTERS.items()
    }


__all__ = [
    "EMITTERS",
    "ActionsEmitter",
    "ClaudeEmitter",
    "CodexEmitter",
    "KiroEmitter",
    "dispositions",
    "Emitter",
    "EmitError",
    "MissingPersonaDescription",
]
