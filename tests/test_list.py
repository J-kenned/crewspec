"""End-to-end tests for ``crewspec list``: reading the trees ``install`` emits.

Each test installs ``examples/code-review-crew`` into ``tmp_path`` through the
CLI, then lists it. ``--json`` output is asserted exactly; the human output is
checked by substring.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from crewspec.cli import cli

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "code-review-crew"
CLAUDE_PLUGIN = Path(".claude", "skills", "crewspec")
CODEX_PLUGIN = Path("plugins", "crewspec")


def _run(*args: str) -> Result:
    return CliRunner().invoke(cli, list(args), prog_name="crewspec")


def _install(out: Path, target: str) -> None:
    result = _run("install", str(EXAMPLE), "--target", target, "--out", str(out))
    assert result.exit_code == 0, result.output


def _list_json(*args: str) -> list[dict[str, object]]:
    result = _run("list", *args, "--json")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert isinstance(rows, list)
    return rows


def test_list_claude_pack(tmp_path: Path) -> None:
    _install(tmp_path, "claude")

    assert _list_json("--out", str(tmp_path)) == [
        {
            "target": "claude",
            "path": ".claude/skills/crewspec",
            "agents": ["reviewer"],
            "skills": ["code-review", "test-analysis"],
            "mcps": ["github"],
        }
    ]

    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "claude  .claude/skills/crewspec" in result.output
    assert "agents: reviewer" in result.output
    assert "skills: code-review, test-analysis" in result.output
    assert "mcps:   github" in result.output


def test_list_codex_pack_folds_agents_into_skills(tmp_path: Path) -> None:
    _install(tmp_path, "codex")

    assert _list_json("--out", str(tmp_path)) == [
        {
            "target": "codex",
            "path": "plugins/crewspec",
            "agents": [],
            "skills": ["code-review", "reviewer", "test-analysis"],
            "mcps": ["github"],
        }
    ]

    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "codex  plugins/crewspec" in result.output
    assert "agents:" not in result.output
    assert "skills: code-review, reviewer, test-analysis" in result.output
    assert "Codex agents are emitted as skills" in result.output


def test_list_reports_every_installed_target_claude_first(tmp_path: Path) -> None:
    _install(tmp_path, "codex")
    _install(tmp_path, "claude")

    rows = _list_json("--out", str(tmp_path))
    assert [row["target"] for row in rows] == ["claude", "codex"]

    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert result.output.index("claude") < result.output.index("codex")


def test_list_empty_directory_is_not_an_error(tmp_path: Path) -> None:
    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"No crewspec packs installed under {tmp_path}"

    assert _list_json("--out", str(tmp_path)) == []


def test_list_target_without_a_pack_says_so(tmp_path: Path) -> None:
    _install(tmp_path, "claude")

    result = _run("list", "--out", str(tmp_path), "--target", "codex")
    assert result.exit_code == 0, result.output
    assert f"No crewspec pack installed for codex under {tmp_path}" in result.output


def test_list_target_filters_to_that_pack(tmp_path: Path) -> None:
    _install(tmp_path, "claude")
    _install(tmp_path, "codex")

    rows = _list_json("--out", str(tmp_path), "--target", "codex")
    assert [row["target"] for row in rows] == ["codex"]


def test_list_without_mcp_file_reports_no_mcps(tmp_path: Path) -> None:
    # install writes no .mcp.json for a crew without MCPs, so a missing file is
    # an ordinary "no servers", not a damaged tree.
    _install(tmp_path, "claude")
    (tmp_path / CLAUDE_PLUGIN / ".mcp.json").unlink()

    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "mcps:   (none)" in result.stdout
    assert result.stderr == ""
    assert _list_json("--out", str(tmp_path))[0]["mcps"] == []


def test_list_malformed_mcp_file_warns_and_lists_the_rest(tmp_path: Path) -> None:
    _install(tmp_path, "claude")
    (tmp_path / CLAUDE_PLUGIN / ".mcp.json").write_text("{not json")

    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "warning:" in result.stderr
    assert "not valid JSON" in result.stderr
    assert "agents: reviewer" in result.stdout
    assert "mcps:   (none)" in result.stdout

    result = _run("list", "--out", str(tmp_path), "--json")
    assert result.exit_code == 0, result.output
    assert "warning:" in result.stderr
    assert json.loads(result.stdout)[0]["mcps"] == []


def test_list_mcp_file_without_servers_object_warns(tmp_path: Path) -> None:
    _install(tmp_path, "claude")
    (tmp_path / CLAUDE_PLUGIN / ".mcp.json").write_text('{"servers": {}}')

    result = _run("list", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "no 'mcpServers' object" in result.stderr
    assert "mcps:   (none)" in result.stdout
    assert _list_json("--out", str(tmp_path))[0]["mcps"] == []


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="chmod 000 is not enforced here"
)
def test_list_unreadable_mcp_file_warns_and_lists_the_rest(tmp_path: Path) -> None:
    _install(tmp_path, "claude")
    mcp = tmp_path / CLAUDE_PLUGIN / ".mcp.json"
    mcp.chmod(0)
    try:
        result = _run("list", "--out", str(tmp_path))
    finally:
        mcp.chmod(0o644)

    assert result.exit_code == 0, result.output
    assert "could not be read" in result.stderr
    assert "agents: reviewer" in result.stdout
    assert "mcps:   (none)" in result.stdout
