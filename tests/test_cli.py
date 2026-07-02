"""End-to-end tests for ``crewspec install``: the parse, resolve, emit CLI.

These drive the real pipeline against the ``happy`` fixture pack through click's
``CliRunner`` and assert on the emitted file tree, the printed summary, and exit
codes. Artifacts are written under ``tmp_path`` so the fixture stays read-only.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner, Result

from crewspec.cli import cli
from crewspec.ir import ResolvedIR

FIXTURE = Path(__file__).parent / "fixtures" / "happy"
PLUGIN = (".claude", "skills", "crewspec")


def _run(*args: str) -> Result:
    return CliRunner().invoke(cli, list(args), prog_name="crewspec")


def _tree(root: Path) -> set[Path]:
    return set(root.rglob("*"))


def test_install_emits_full_plugin_tree(tmp_path: Path) -> None:
    result = _run("install", str(FIXTURE), "--out", str(tmp_path))
    assert result.exit_code == 0, result.output

    root = tmp_path.joinpath(*PLUGIN)
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / "agents" / "reviewer.md").is_file()
    assert (root / "skills" / "lint" / "SKILL.md").is_file()
    assert (root / "skills" / "format" / "SKILL.md").is_file()
    # Extra skill subdirectories ride along with their skill.
    assert (root / "skills" / "lint" / "references" / "rules.md").is_file()

    mcp = yaml.safe_load((root / ".mcp.json").read_text())
    assert "github" in mcp["mcpServers"]


def test_install_prints_summary_with_counts_and_path(tmp_path: Path) -> None:
    result = _run("install", str(FIXTURE), "--out", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "agents: 1" in result.output
    assert "skills: 2" in result.output
    assert "mcps: 1" in result.output
    assert str(tmp_path.joinpath(*PLUGIN)) in result.output


def test_install_accepts_a_direct_crewfile(tmp_path: Path) -> None:
    result = _run("install", str(FIXTURE / "team.crewfile"), "--out", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert (tmp_path.joinpath(*PLUGIN) / "agents" / "reviewer.md").is_file()


def test_out_directory_is_created_when_missing(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dest"
    result = _run("install", str(FIXTURE), "--out", str(out))

    assert result.exit_code == 0, result.output
    assert out.joinpath(*PLUGIN, "agents", "reviewer.md").is_file()


def test_dry_run_resolves_but_writes_nothing(tmp_path: Path) -> None:
    result = _run("install", str(FIXTURE), "--out", str(tmp_path), "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Dry run OK" in result.output
    assert "agents: 1" in result.output
    assert list(tmp_path.iterdir()) == []


def test_unknown_target_errors_and_lists_supported(tmp_path: Path) -> None:
    result = _run("install", str(FIXTURE), "--target", "bogus", "--out", str(tmp_path))

    assert result.exit_code == 2
    assert "claude" in result.stderr


def test_missing_path_errors(tmp_path: Path) -> None:
    result = _run("install", str(tmp_path / "nope"), "--out", str(tmp_path))

    assert result.exit_code == 2  # click rejects a nonexistent PATH argument


def test_parse_error_exits_nonzero_with_message(tmp_path: Path) -> None:
    bad = tmp_path / "team.crewfile"
    bad.write_text("agents: [unterminated\n")  # invalid YAML
    result = _run("install", str(bad), "--out", str(tmp_path / "out"))

    assert result.exit_code == 1
    assert "Error" in result.stderr
    assert not (tmp_path / "out").exists()


def test_directory_without_a_crewfile_errors(tmp_path: Path) -> None:
    result = _run("install", str(tmp_path), "--out", str(tmp_path / "out"))

    assert result.exit_code == 1
    assert "crewfile" in result.stderr.lower()


def test_validate_prints_resolved_plan() -> None:
    result = _run("validate", str(FIXTURE))

    assert result.exit_code == 0, result.output
    assert "reviewer" in result.output
    # The resolved skill closure is printed dependency-first.
    assert "format, lint" in result.output
    assert "mcp servers (deduplicated): 1" in result.output


def test_validate_writes_no_files() -> None:
    before = _tree(FIXTURE)
    result = _run("validate", str(FIXTURE))

    assert result.exit_code == 0, result.output
    assert _tree(FIXTURE) == before


def test_validate_json_round_trips_into_the_ir() -> None:
    result = _run("validate", str(FIXTURE), "--json")

    assert result.exit_code == 0, result.output
    ir = ResolvedIR.model_validate_json(result.output)
    assert [agent.name for agent in ir.agents] == ["reviewer"]
    assert [mcp.name for mcp in ir.mcps] == ["github"]


def test_validate_parse_error_exits_nonzero_with_message(tmp_path: Path) -> None:
    bad = tmp_path / "team.crewfile"
    bad.write_text("agents: [unterminated\n")  # invalid YAML

    result = _run("validate", str(bad))

    assert result.exit_code == 1
    assert "Error" in result.stderr


def test_validate_missing_path_errors(tmp_path: Path) -> None:
    result = _run("validate", str(tmp_path / "nope"))

    assert result.exit_code == 2  # click rejects a nonexistent SOURCE argument


def test_validate_resolve_cycle_exits_nonzero(tmp_path: Path) -> None:
    _write_cycle_crew(tmp_path)

    result = _run("validate", str(tmp_path / "team.crewfile"))

    assert result.exit_code == 1
    assert "cycle" in result.stderr.lower()


def _write_cycle_crew(root: Path) -> None:
    """A crew whose two skills depend on each other via SKILL.md frontmatter."""

    for name, dep in (("s1", "s2"), ("s2", "s1")):
        skill_dir = root / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n"
            f"x-crewspec:\n  skills: [{dep}]\n---\n\n# {name}\n"
        )
    (root / "agent.md").write_text("---\nname: a\ndescription: a\n---\n\nbody\n")
    (root / "team.crewfile").write_text(
        'version: "0.1"\n'
        "agents:\n"
        "  a:\n"
        "    name: a\n"
        "    persona: agent.md\n"
        "    depends_on:\n"
        "      skills: [s1]\n"
        "skills:\n"
        "  s1:\n    name: s1\n    path: s1\n"
        "  s2:\n    name: s2\n    path: s2\n"
    )
