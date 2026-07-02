"""Abstract base class for crewspec emitters."""

from abc import ABC, abstractmethod
from pathlib import Path

from crewspec.ir import ResolvedIR


class Emitter(ABC):
    """Render a resolved crewspec into target-specific artifacts.

    Concrete emitters (e.g Claude emitter) consume the intermediate
    representation and write the files an agent runtime expects.
    """

    #: Short, stable identifier for the emitter target (e.g. ``"claude"``).
    name: str

    @abstractmethod
    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        """Write artifacts for ``ir`` into ``dest``.

        Args:
            ir: The resolved intermediate representation to render.
            dest: Directory the emitter should write its artifacts into.
        """
        raise NotImplementedError

    def output_path(self, dest: Path) -> Path:
        """The concrete directory :meth:`emit` writes into, given ``dest``.

        Defaults to ``dest`` itself. An emitter that owns a subtree under
        ``dest`` (e.g. a plugin directory) overrides this so callers can report
        the real artifact location without knowing the target's layout.
        """
        return dest
