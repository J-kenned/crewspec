"""Abstract base class for crewspec emitters, plus the per-pack state file."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from crewspec.emitters.errors import EmitError
from crewspec.ir import ResolvedIR

#: Name of the state file each emitter writes inside its plugin metadata dir.
STATE_FILE = "crewspec.json"


class PackState(BaseModel):
    """What crewspec wrote for one pack outside its own plugin directory.

    ``mcps`` are the ``mcpServers`` keys crewspec owns in the host's config and
    ``instructions`` records whether a managed instructions block was written.
    The next install or ``uninstall`` reads it to remove exactly those.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    mcps: tuple[str, ...] = ()
    instructions: bool = False

    @classmethod
    def read(cls, path: Path) -> PackState | None:
        """The state at ``path``, or ``None`` when no file exists.

        Raises :class:`EmitError` when the file exists but cannot be read or
        does not hold a state object.
        """

        if not path.exists():
            return None
        try:
            return cls.model_validate_json(path.read_text())
        except OSError as exc:
            raise EmitError(f"'{path}' could not be read: {exc.strerror}") from exc
        except (ValueError, ValidationError) as exc:
            raise EmitError(f"'{path}' is not a valid crewspec state file") from exc

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n")


class Emitter(ABC):
    """Render a resolved crewspec into target-specific artifacts.

    Each target keeps one plugin directory per pack under ``plugins_dir``, named
    after the pack, with a state file in its ``meta_dir``. That file is how
    ``list`` finds packs and how ``uninstall`` knows what to remove.
    """

    #: Short, stable identifier for the emitter target (e.g. ``"claude"``).
    name: str
    #: Directory under ``dest`` that holds one plugin directory per pack.
    plugins_dir: tuple[str, ...]
    #: Metadata directory inside a plugin that holds its manifest and state.
    meta_dir: str

    @abstractmethod
    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        """Write artifacts for ``ir`` into ``dest``."""
        raise NotImplementedError

    @abstractmethod
    def uninstall(self, pack: str, dest: Path) -> None:
        """Remove pack ``pack`` from ``dest``, including what it wrote outside
        its plugin directory. Raises :class:`EmitError` when it is not installed.
        """
        raise NotImplementedError

    def output_path(self, dest: Path, pack: str) -> Path:
        """The plugin directory for ``pack`` under ``dest``; crewspec owns it."""
        return dest.joinpath(*self.plugins_dir, pack)

    def state_path(self, dest: Path, pack: str) -> Path:
        return self.output_path(dest, pack) / self.meta_dir / STATE_FILE

    def installed(self, dest: Path) -> list[Path]:
        """Plugin directories under ``dest`` that carry a state file, sorted."""
        parent = dest.joinpath(*self.plugins_dir)
        return sorted(
            p.parent.parent for p in parent.glob(f"*/{self.meta_dir}/{STATE_FILE}")
        )

    def read_state(self, dest: Path, pack: str) -> PackState:
        """The state for an installed ``pack``, raising when it is not installed.

        A plugin directory without a state file (an install from before state
        files existed) counts as installed and owning nothing outside itself.
        """

        if not self.output_path(dest, pack).is_dir():
            raise EmitError(
                f"no pack '{pack}' is installed for {self.name} under '{dest}'"
            )
        return PackState.read(self.state_path(dest, pack)) or PackState(name=pack)
