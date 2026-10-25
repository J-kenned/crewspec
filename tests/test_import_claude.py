"""``crewspec init --from-claude``: a Claude layout becomes a 0.2 crewfile pack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from crewspec.importers import ImportError_, import_claude, slugify
from crewspec.parser import load_crewfile, resolve_crewfile

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "code-review-crew"


def _load(path: Path):  # type: ignore[no-untyped-def]
    return load_crewfile(resolve_crewfile(path))


def test_round_trip_from_emitted_plugin(tmp_path: Path, run_cli) -> None:  # type: ignore[no-untyped-def]
    installed = tmp_path / "T"
    assert run_cli("install", str(EXAMPLE), "--out", str(installed)).exit_code == 0

    result = run_cli(
        "init", "--from-claude", str(installed), "--out", str(tmp_path / "R")
    )
    assert result.exit_code == 0, result.output
    assert run_cli("validate", str(tmp_path / "R")).exit_code == 0

    original = _load(EXAMPLE)
    imported = _load(tmp_path / "R")
    assert imported.doc.version == "0.2"
    assert imported.doc.name == original.doc.name == "code-review-crew"

    assert set(imported.registry.agents) == set(original.registry.agents)
    for key, node in original.registry.agents.items():
        got = imported.registry.agents[key].definition
        assert got.allowed_tools == node.definition.allowed_tools
        assert got.depends_on.skills == node.definition.depends_on.skills

    assert set(imported.registry.skills) == set(original.registry.skills)
    for key, node in original.registry.skills.items():
        assert imported.registry.skills[key].deps == node.deps

    # The emitter wrote ${GITHUB_TOKEN}; the importer turns it back into the
    # secret ref the original used, and declares it on the agent that reaches it.
    assert imported.registry.mcps == original.registry.mcps
    assert set(imported.doc.agents["reviewer"].secrets or {}) == {"GITHUB_TOKEN"}

    assert imported.registry.instructions is not None
    assert original.registry.instructions is not None
    assert (
        imported.registry.instructions.read_text().strip()
        == original.registry.instructions.read_text().strip()
    )
    # Persona passes through unchanged: the emitted frontmatter keys survive.
    persona = (tmp_path / "R" / "agents" / "reviewer.md").read_text()
    assert "tools: Read, Grep, Bash, mcp__github__*" in persona


def _hand_made(root: Path, *, mcp_file: str = ".mcp.json") -> None:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "helper.md").write_text(
        "---\nname: helper\ndescription: Helps.\ntools: Read, mcp__api__*\n"
        "skills: [tidy]\ncustom: kept\n---\n\nBe helpful.\n"
    )
    skill = root / ".claude" / "skills" / "tidy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tidy\ndescription: Tidy.\n---\n\nTidy.\n"
    )
    (root / mcp_file).write_text(
        json.dumps(
            {
                "mcpServers": {
                    "api": {
                        "command": "npx",
                        "args": ["-y", "some-server"],
                        "env": {"API_KEY": "sk-abcdef1234567890", "PORT": "8080"},
                    },
                    "remote": {
                        "type": "http",
                        "url": "https://mcp.example.com/",
                        "headers": {"Authorization": "Bearer ${EXAMPLE_TOKEN}"},
                    },
                    "plain": {"command": "echo", "env": {"MODE": "${MODE:-fast}"}},
                }
            }
        )
    )
    (root / "CLAUDE.md").write_text("# House rules\n\nBe brief.\n")


def test_hand_made_layout_never_copies_a_credential(tmp_path: Path, run_cli) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "proj"
    _hand_made(src)
    out = tmp_path / "pack"
    crewfile = import_claude(src, out, name="proj")

    text = crewfile.read_text()
    assert "sk-abcdef1234567890" not in text
    assert "${secret:API_KEY}" in text
    assert "Bearer ${secret:EXAMPLE_TOKEN}" in text
    assert "PORT: '8080'" in text or "PORT: 8080" in text
    assert "${MODE:-fast}" in text

    doc = yaml.safe_load(text)
    assert doc["version"] == "0.2"
    helper = doc["agents"]["helper"]
    assert helper["allowed_tools"] == ["Read", "mcp__api__*"]
    assert helper["depends_on"] == {"skills": ["tidy"]}
    # Reaches `api` only (via the tool glob), so only its secret is declared.
    assert helper["secrets"] == {
        "API_KEY": {"ref": "vault://proj/API_KEY", "blind": True}
    }
    assert (out / "instructions.md").read_text() == "# House rules\n\nBe brief.\n"
    assert "custom: kept" in (out / "agents" / "helper.md").read_text()

    # EXAMPLE_TOKEN is reachable by nobody, so 0.2 validation rejects the pack
    # until a human declares it: the importer surfaces the gap, not hides it.
    result = run_cli("validate", str(out))
    assert result.exit_code != 0
    assert "EXAMPLE_TOKEN" in result.output


def test_inherit_all_agent_declares_every_secret(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    _hand_made(src)
    (src / ".claude" / "agents" / "boss.md").write_text(
        "---\nname: boss\ndescription: Inherits every tool.\n---\n\nRun it.\n"
    )
    out = tmp_path / "pack"
    import_claude(src, out, name="proj")
    doc = yaml.safe_load((out / "proj.crewfile").read_text())
    assert set(doc["agents"]["boss"]["secrets"]) == {"API_KEY", "EXAMPLE_TOKEN"}
    assert "allowed_tools" not in doc["agents"]["boss"]


def test_pack_name_is_slugified_or_overridden(tmp_path: Path) -> None:
    src = tmp_path / "My Project!"
    _hand_made(src, mcp_file=".claude.json")
    crewfile = import_claude(src, tmp_path / "a")
    assert crewfile.name == "my-project.crewfile"
    crewfile = import_claude(src, tmp_path / "b", name="other")
    assert crewfile.name == "other.crewfile"
    assert slugify("Hello World") == "hello-world"
    with pytest.raises(ImportError_):
        slugify("!!!")


def test_refuses_empty_dir_and_existing_pack(tmp_path: Path, run_cli) -> None:  # type: ignore[no-untyped-def]
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_cli("init", "--from-claude", str(empty))
    assert result.exit_code != 0
    assert "no .claude/agents" in result.output

    src = tmp_path / "proj"
    _hand_made(src)
    out = tmp_path / "pack"
    import_claude(src, out, name="proj")
    with pytest.raises(ImportError_, match="refusing to overwrite"):
        import_claude(src, out, name="proj")
