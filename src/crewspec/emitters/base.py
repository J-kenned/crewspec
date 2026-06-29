"""Abstract base class for crewspec emitters."""

from abc import ABC, abstractmethod
from pathlib import Path


class Emitter(ABC):
    """Render a resolved crewspec into target-specific artifacts.

    Concrete emitters (e.g Claude emitter) consume the intermediate
    representation and write the files an agent runtime expects.
    """

    #: Short, stable identifier for the emitter target (e.g. ``"claude"``).
    name: str

    @abstractmethod
    def emit(self, ir: object, dest: Path) -> None:
        """Write artifacts for ``ir`` into ``dest``.

        Args:
            ir: The intermediate representation to render. Typed as ``object``
                until the IR model lands in a later issue.
            dest: Directory the emitter should write its artifacts into.
        """
        raise NotImplementedError
