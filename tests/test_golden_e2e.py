"""Golden end-to-end test: install the example pack and lock everything it writes.

The snapshot covers the whole ``--out`` directory: the plugin under
``.claude/skills/<name>/``, the user-scope ``.claude.json``, and ``.claude/CLAUDE.md``.
"""

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
PLUGIN = (".claude", "skills", "code-review-crew")
UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"


def _snapshot(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` to its bytes, keyed by POSIX relpath.

    Keying by path makes the comparison order-independent while the bytes keep it
    content-exact; ``rglob`` includes dotfiles like ``.claude.json``.
    """

    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _emit(out: Path) -> Path:
    """Install the example pack into ``out`` and return ``out``."""

    result = CliRunner().invoke(cli, ["install", str(EXAMPLE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    return out


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
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Emit the example pack once for the behaviour assertions below."""

    return _emit(tmp_path_factory.mktemp("emit"))


@pytest.fixture(scope="module")
def plugin_root(out: Path) -> Path:
    return out.joinpath(*PLUGIN)


def test_example_matches_golden(tmp_path: Path) -> None:
    emitted = _snapshot(_emit(tmp_path))

    if UPDATE_GOLDEN:
        _write_golden(emitted)

    assert GOLDEN.is_dir(), (
        "golden tree is missing; regenerate with "
        "`UPDATE_GOLDEN=1 uv run pytest tests/test_golden_e2e.py`"
    )
    assert emitted == _snapshot(GOLDEN)


def test_every_declared_skill_is_emitted(plugin_root: Path) -> None:
    # No agent lists test-analysis; it ships because the crewfile declares it.
    assert (plugin_root / "skills" / "test-analysis" / "SKILL.md").is_file()
    # The agent file lists only the skills the agent declares.
    meta = _agent_frontmatter(plugin_root / "agents" / "reviewer.md")
    assert meta["skills"] == "code-review"


def test_mcps_are_user_scoped(out: Path, plugin_root: Path) -> None:
    assert not (plugin_root / ".mcp.json").exists()
    servers = yaml.safe_load((out / ".claude.json").read_text())["mcpServers"]
    assert list(servers) == ["github"]
    meta = _agent_frontmatter(plugin_root / "agents" / "reviewer.md")
    assert str(meta["tools"]).split(", ").count("mcp__github") == 1


def test_instructions_are_a_managed_block(out: Path) -> None:
    text = (out / ".claude" / "CLAUDE.md").read_text()
    assert text.startswith("<!-- crewspec:code-review-crew:begin -->\n")
    assert text.endswith("<!-- crewspec:code-review-crew:end -->\n")
    assert (EXAMPLE / "instructions.md").read_text().rstrip("\n") in text


def test_references_dir_copied(plugin_root: Path) -> None:
    emitted = plugin_root / "skills" / "code-review" / "references" / "checklist.md"
    source = EXAMPLE / "skills" / "code-review" / "references" / "checklist.md"
    assert emitted.is_file()
    assert emitted.read_text() == source.read_text()
