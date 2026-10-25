"""``crewspec init --from-claude``: a Claude layout becomes a 0.2 crewfile pack."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from click.testing import Result

from crewspec.importers import ImportError_, import_claude, slugify
from crewspec.parser import load_crewfile, resolve_crewfile

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "code-review-crew"
GHP = "ghp_Abc123def456GHI789jkl012MNO345pqr678"
Runner = Callable[..., Result]


def _load(path: Path):  # type: ignore[no-untyped-def]
    return load_crewfile(resolve_crewfile(path))


def _doc(out: Path, name: str) -> dict:  # type: ignore[type-arg]
    return yaml.safe_load((out / f"{name}.crewfile").read_text())


def test_round_trip_from_emitted_plugin(tmp_path: Path, run_cli: Runner) -> None:
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


def test_hand_made_layout_never_copies_a_credential(
    tmp_path: Path, run_cli: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "proj"
    _hand_made(src)
    out = tmp_path / "pack"
    crewfile = import_claude(src, out, name="proj")
    err = capsys.readouterr().err

    text = crewfile.read_text()
    assert "sk-abcdef1234567890" not in text
    assert "${secret:API_KEY}" in text
    assert "PORT: '8080'" in text or "PORT: 8080" in text
    assert "${MODE:-fast}" in text  # ${X} under a non-credential key stays env
    assert "mcpServers.api.API_KEY held a literal credential" in err

    doc = yaml.safe_load(text)
    assert doc["version"] == "0.2"
    helper = doc["agents"]["helper"]
    assert helper["allowed_tools"] == ["Read", "mcp__api__*"]
    assert helper["depends_on"] == {"skills": ["tidy"]}
    assert helper["secrets"] == {
        "API_KEY": {"ref": "vault://proj/API_KEY", "blind": True}
    }
    assert (out / "instructions.md").read_text() == "# House rules\n\nBe brief.\n"
    assert "custom: kept" in (out / "agents" / "helper.md").read_text()

    # `remote` needs EXAMPLE_TOKEN but no agent reaches it: dropped with a
    # warning so the pack validates instead of failing on an undeclared secret.
    assert "remote" not in doc["mcps"]
    assert "dropped mcps.remote" in err and "EXAMPLE_TOKEN" in err
    assert run_cli("validate", str(out)).exit_code == 0


def test_inherit_all_agent_declares_every_secret(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    _hand_made(src)
    (src / ".claude" / "agents" / "boss.md").write_text(
        "---\nname: boss\ndescription: Inherits every tool.\n---\n\nRun it.\n"
    )
    out = tmp_path / "pack"
    import_claude(src, out, name="proj")
    doc = _doc(out, "proj")
    assert set(doc["agents"]["boss"]["secrets"]) == {"API_KEY", "EXAMPLE_TOKEN"}
    assert "allowed_tools" not in doc["agents"]["boss"]
    # Reached by `boss` now, so `remote` is kept with a secret ref, not a value.
    assert doc["mcps"]["remote"]["headers"] == {
        "Authorization": "Bearer ${secret:EXAMPLE_TOKEN}"
    }


def _secret_positions(root: Path) -> None:
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "all.md").write_text(
        "---\nname: all\ndescription: Reaches everything.\n---\n\nGo.\n"
    )
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "gh": {
                        "command": "npx",
                        "args": ["-y", "server", "--token", GHP],
                        "env": {"MODE": "${secret:MODE_X}"},
                    },
                    "web": {
                        "type": "http",
                        "url": "https://x.example.com/${REGION}/?k=${API_KEY}",
                        "headers": {
                            "X-Api": f"Bearer {GHP}",
                            "X-Region": "${REGION}",
                            "Accept": "application/json",
                        },
                    },
                    "opaque": {
                        "command": "server",
                        "env": {
                            "SESSION": "aB3" * 14,
                            "LOWER": "abcdefghijklmnopqrstuvwxyz0123456789abcdefgh",
                            "NOTE": f"token={GHP} region=${{REGION}}",
                        },
                    },
                }
            }
        )
    )


def test_every_secret_bearing_position_is_scrubbed(
    tmp_path: Path, run_cli: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "proj"
    _secret_positions(src)
    out = tmp_path / "pack"
    crewfile = import_claude(src, out, name="proj")
    err = capsys.readouterr().err
    text = crewfile.read_text()
    doc = yaml.safe_load(text)

    assert GHP not in text
    assert doc["mcps"]["gh"]["args"] == ["-y", "server", "--token", "${secret:GH_ARG3}"]
    assert "mcpServers.gh.ARG3 held a literal credential" in err
    # A pre-existing ${secret:X} under a non-credential key is kept and declared.
    assert doc["mcps"]["gh"]["env"] == {"MODE": "${secret:MODE_X}"}

    headers = doc["mcps"]["web"]["headers"]
    assert headers["X-Api"] == "${secret:X_API}"  # bearer literal, whole value
    assert headers["X-Region"] == "${secret:REGION}"  # any header env ref
    assert headers["Accept"] == "application/json"  # plain literal survives
    # A credential-named env ref is a secret in any position; others stay env.
    assert (
        doc["mcps"]["web"]["url"]
        == "https://x.example.com/${REGION}/?k=${secret:API_KEY}"
    )
    assert "mcpServers.web.X-Api held a literal credential" in err

    # 40+ chars: mixed case + digit is a credential, lowercase-only is not.
    assert doc["mcps"]["opaque"]["env"]["SESSION"] == "${secret:SESSION}"
    assert doc["mcps"]["opaque"]["env"]["LOWER"].startswith("abcdefghij")
    # A literal credential mixed with a ref is replaced on its own.
    assert doc["mcps"]["opaque"]["env"]["NOTE"] == "${secret:NOTE}${REGION}"
    assert "mcpServers.opaque.NOTE held a literal credential" in err

    assert set(doc["agents"]["all"]["secrets"]) == {
        "GH_ARG3",
        "MODE_X",
        "X_API",
        "REGION",
        "API_KEY",
        "SESSION",
        "NOTE",
    }
    assert run_cli("validate", str(out)).exit_code == 0


def test_duplicate_agent_keys_are_suffixed_not_dropped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "proj"
    (src / ".claude" / "agents").mkdir(parents=True)
    for stem in ("a", "b"):
        (src / ".claude" / "agents" / f"{stem}.md").write_text(
            "---\nname: Same Name\ndescription: Twin.\n---\n\nHi.\n"
        )
    out = tmp_path / "pack"
    import_claude(src, out, name="proj")
    assert set(_doc(out, "proj")["agents"]) == {"same-name", "same-name-2"}
    assert "already taken" in capsys.readouterr().err
    assert (out / "agents" / "same-name-2.md").is_file()


def test_agent_key_prefers_frontmatter_name_and_list_tools(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    (src / ".claude" / "agents").mkdir(parents=True)
    (src / ".claude" / "agents" / "Reviewer Bot.md").write_text(
        "---\nname: Code Reviewer\ndescription: Reviews.\ntools:\n  - Read\n"
        "  - mcp__gh__*\n---\n\nReview.\n"
    )
    (src / ".claude" / "agents" / "plain.md").write_text(
        "---\ndescription: No name key.\n---\n\nHi.\n"
    )
    out = tmp_path / "pack"
    import_claude(src, out, name="proj")
    doc = _doc(out, "proj")
    assert set(doc["agents"]) == {"code-reviewer", "plain"}
    assert doc["agents"]["code-reviewer"]["allowed_tools"] == ["Read", "mcp__gh__*"]
    assert (out / "agents" / "code-reviewer.md").is_file()


def test_skips_node_modules_hidden_and_fenceless_skills(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "proj"
    skills = src / ".claude" / "skills"
    for rel, body in {
        "good/SKILL.md": "---\nname: good\ndescription: Good.\n---\n\nOk.\n",
        "node_modules/dep/SKILL.md": "---\nname: dep\ndescription: Dep.\n---\n",
        ".hidden/SKILL.md": "---\nname: hidden\ndescription: H.\n---\n",
        "bare/SKILL.md": "# No fence here\n",
    }.items():
        path = skills / rel
        path.parent.mkdir(parents=True)
        path.write_text(body)
    out = tmp_path / "pack"
    import_claude(src, out, name="proj")
    err = capsys.readouterr().err
    assert set(_doc(out, "proj")["skills"]) == {"good"}
    assert "skipped" in err and "bare" in err and "no frontmatter fence" in err


def test_out_may_not_nest_with_src_and_defaults_to_sibling(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    _hand_made(src)
    with pytest.raises(ImportError_, match="inside the source"):
        import_claude(src, src / "pack", name="proj")
    with pytest.raises(ImportError_, match="inside --out"):
        import_claude(src, tmp_path, name="proj")
    crewfile = import_claude(src, name="proj")
    assert crewfile == tmp_path / "proj-crew" / "proj.crewfile"


def test_malformed_placeholder_is_a_clean_path_line_error(
    tmp_path: Path, run_cli: Runner
) -> None:
    src = tmp_path / "proj"
    _hand_made(src)
    (src / ".mcp.json").write_text(
        '{\n  "mcpServers": {\n    "bad": {\n      "command": "x",\n'
        '      "env": {"K": "${oops"}\n    }\n  }\n}\n'
    )
    result = run_cli("init", "--from-claude", str(src), "--out", str(tmp_path / "o"))
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert f"{src / '.mcp.json'}:5: mcpServers.bad.K:" in result.output
    assert not (tmp_path / "o").exists()


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


def test_refuses_empty_dir_and_existing_pack(tmp_path: Path, run_cli: Runner) -> None:
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
