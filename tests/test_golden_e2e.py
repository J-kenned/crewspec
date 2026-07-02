"""Golden end-to-end test: install the example pack and lock the emitted tree."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from crewspec.cli import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "code-review-crew"
GOLDEN = REPO_ROOT / "tests" / "golden" / "code-review-crew"
PLUGIN = (".claude", "skills", "crewspec")
UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"


def _snapshot(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` to its bytes, keyed by POSIX relpath.

    Keying by path makes the comparison order-independent while the bytes keep it
    content-exact; ``rglob`` includes dotfiles like ``.mcp.json``.
    """

    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _emit(out: Path) -> Path:
    """Install the example pack into ``out`` and return the emitted plugin root."""

    result = CliRunner().invoke(cli, ["install", str(EXAMPLE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    return out.joinpath(*PLUGIN)


def _write_golden(snapshot: dict[str, bytes]) -> None:
    if GOLDEN.exists():
        shutil.rmtree(GOLDEN)
    for rel, data in snapshot.items():
        dest = GOLDEN / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _agent_frontmatter(agent_md: Path) -> dict[str, object]:
    lines = agent_md.read_text().split("\n")
    end = lines.index("---", 1)
    meta = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(meta, dict)
    return meta


@pytest.fixture(scope="module")
def plugin_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Emit the example pack once for the behaviour assertions below."""

    return _emit(tmp_path_factory.mktemp("emit"))


def test_example_matches_golden(tmp_path: Path) -> None:
    emitted = _snapshot(_emit(tmp_path))

    if UPDATE_GOLDEN:
        _write_golden(emitted)

    assert GOLDEN.is_dir(), (
        "golden tree is missing; regenerate with "
        "`UPDATE_GOLDEN=1 uv run pytest tests/test_golden_e2e.py`"
    )
    assert emitted == _snapshot(GOLDEN)


def test_transitive_skill_closure(plugin_root: Path) -> None:
    # test-analysis is reachable only through code-review's SKILL.md frontmatter.
    assert (plugin_root / "skills" / "test-analysis" / "SKILL.md").is_file()
    # Dependency-first order: the transitive dep precedes the skill that pulls it.
    meta = _agent_frontmatter(plugin_root / "agents" / "reviewer.md")
    assert meta["skills"] == "test-analysis, code-review"


def test_shared_mcp_is_deduped(plugin_root: Path) -> None:
    servers = yaml.safe_load((plugin_root / ".mcp.json").read_text())["mcpServers"]
    # Both skills depend on github; the resolver collapses it to one entry.
    assert list(servers) == ["github"]
    meta = _agent_frontmatter(plugin_root / "agents" / "reviewer.md")
    assert str(meta["tools"]).split(", ").count("mcp__github") == 1


def test_references_dir_copied(plugin_root: Path) -> None:
    emitted = plugin_root / "skills" / "code-review" / "references" / "checklist.md"
    source = EXAMPLE / "skills" / "code-review" / "references" / "checklist.md"
    assert emitted.is_file()
    assert emitted.read_text() == source.read_text()
