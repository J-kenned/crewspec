"""Errors raised while resolving the node graph into the IR.

Unlike :mod:`crewspec.parser.errors`, these are *graph* failures, not file
locations, a cycle or a conflict points at a set of named nodes, not a line in
a crewfile. They form their own hierarchy rather than subclassing the
path-anchored ``CrewfileError``.
"""

from __future__ import annotations

from typing import Literal

#: The kinds of node a resolution error can implicate.
NodeKind = Literal["skill", "agent", "mcp"]


class ResolveError(Exception):
    """Base class for resolution failures."""


class CycleError(ResolveError):
    """A dependency cycle was found in the skill or agent graph.

    ``cycle`` is the offending path with the repeated node at both ends, e.g
    ``["lint", "format", "lint"]``, rendered into the message so it reads like a
    diagnostic.
    """

    def __init__(self, kind: NodeKind, cycle: list[str]) -> None:
        self.kind = kind
        self.cycle = cycle
        super().__init__(f"{kind} cycle detected: {' → '.join(cycle)}")


class ConflictError(ResolveError):
    """One name maps to two structurally different definitions.

    Deduplication is by name, a repeated name whose definition differs is never
    silently resolved to one side. This guards future ``include`` composition,
    where the same name may arrive from multiple sources.
    """

    def __init__(self, kind: NodeKind, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(
            f"conflicting {kind} '{name}': same name with differing definitions"
        )
