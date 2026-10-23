"""Tests for the Codex emitter: IR to repo-local plugin and marketplace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from crewspec.emitters import CodexEmitter, EmitError, MissingPersonaDescription
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models import HttpMcpServer, StdioMcpServer
from crewspec.models.mcp import McpServer

PACK = "pack"
PLUGIN = ("plugins", PACK)
MARKETPLACE = (".agents", "plugins", "marketplace.json")


def _persona(tmp_path: Path, name: str, *, description: str | None, body: str) -> Path:
    meta = f"name: {name}\n"
    if description is not None:
        meta += f"description: {description}\n"
    path = tmp_path / "src" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{meta}---\n\n{body}")
    return path


def _skill(tmp_path: Path, name: str) -> ResolvedSkill:
    root = tmp_path / "src" / "skills" / name
    root.mkdir(parents=True, exist_ok=True)
    skill_md = root / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: A {name} skill\n---\n\n# {name}\n"
    )
    (root / "references").mkdir()
    (root / "references" / "rules.md").write_text("rules\n")
    return ResolvedSkill(name=name, path=root, skill_md=skill_md)


def _mcp(name: str, server: McpServer) -> ResolvedMcp:
    return ResolvedMcp(name=name, server=server)


def _ir(**kwargs: object) -> ResolvedIR:
    return ResolvedIR(name=PACK, **kwargs)  # type: ignore[arg-type]


def _emit(tmp_path: Path, ir: ResolvedIR) -> Path:
    dest = tmp_path / "out"
    CodexEmitter().emit(ir, dest)
    return dest.joinpath(*PLUGIN)


def _frontmatter(skill_md: Path) -> tuple[dict[str, object], str]:
    text = skill_md.read_text()
    lines = text.split("\n")
    end = lines.index("---", 1)
    meta = yaml.safe_load("\n".join(lines[1:end]))
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def test_manifest_and_repo_marketplace_are_written(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "reviewer", description="Reviews code", body="# R\n")
    ir = _ir(agents=(ResolvedAgent(name="reviewer", persona_path=persona),))

    root = _emit(tmp_path, ir)
    manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
    marketplace = json.loads((tmp_path / "out").joinpath(*MARKETPLACE).read_text())
    state = json.loads((root / ".codex-plugin" / "crewspec.json").read_text())

    assert root == tmp_path / "out" / "plugins" / PACK
    assert manifest["name"] == PACK
    assert manifest["version"] == "0.1.1"
    assert manifest["skills"] == "./skills/"
    assert manifest["interface"]["displayName"] == PACK
    assert manifest["interface"]["developerName"] == PACK
    assert "mcpServers" not in manifest
    assert marketplace == {
        "name": "local-repo",
        "plugins": [
            {
                "name": PACK,
                "source": {"source": "local", "path": f"./plugins/{PACK}"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Developer Tools",
                "interface": {"displayName": PACK},
            }
        ],
    }
    assert state == {"name": PACK, "mcps": [], "instructions": False}


def test_agent_is_emitted_as_codex_skill_with_resolved_wiring(
    tmp_path: Path,
) -> None:
    persona = _persona(
        tmp_path,
        "reviewer",
        description="Reviews code for quality",
        body="# Reviewer\n\nReview it.\n",
    )
    lint = _skill(tmp_path, "lint")
    fmt = _skill(tmp_path, "format")
    orphan = _skill(tmp_path, "orphan")
    ir = _ir(
        agents=(
            ResolvedAgent(
                name="reviewer",
                persona_path=persona,
                tools=("Read", "Edit"),
                skills=(fmt, lint),
            ),
        ),
        skills=(fmt, lint, orphan),
        mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
    )

    root = _emit(tmp_path, ir)
    meta, body = _frontmatter(root / "skills" / "reviewer" / "SKILL.md")

    assert meta == {
        "name": "reviewer",
        "description": "Reviews code for quality",
    }
    assert body.startswith("# Reviewer\n\nReview it.\n")
    assert "$format, $lint" in body
    assert "github" in body
    assert "Read, Edit" in body
    assert (root / "skills" / "lint" / "SKILL.md").is_file()
    assert (root / "skills" / "lint" / "references" / "rules.md").read_text() == (
        "rules\n"
    )
    # Every declared skill ships, listed by an agent or not.
    assert (root / "skills" / "orphan" / "SKILL.md").is_file()


def test_mcp_json_uses_codex_plugin_manifest_and_server_shape(
    tmp_path: Path,
) -> None:
    ir = _ir(
        mcps=(
            _mcp(
                "auth",
                StdioMcpServer(
                    command="serve",
                    args=["--token", "${secret:TOKEN}"],
                    env={"HOST": "${HOST:-localhost}"},
                ),
            ),
            _mcp(
                "remote",
                HttpMcpServer(
                    url="https://example.test/mcp",
                    headers={
                        "Authorization": "${secret:KEY}",
                        "X-Region": "eu",
                    },
                ),
            ),
        ),
    )

    root = _emit(tmp_path, ir)
    manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
    data = json.loads((root / ".mcp.json").read_text())

    assert manifest["mcpServers"] == "./.mcp.json"
    assert data == {
        "mcpServers": {
            "auth": {
                "command": "serve",
                "args": ["--token", "${TOKEN}"],
                "env": {"HOST": "${HOST:-localhost}"},
            },
            "remote": {
                "type": "http",
                "url": "https://example.test/mcp",
                "http_headers": {"X-Region": "eu"},
                "env_http_headers": {"Authorization": "KEY"},
            },
        }
    }


def test_marketplace_preserves_other_plugins_and_replaces_crewspec(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "out"
    path = dest.joinpath(*MARKETPLACE)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "name": "existing",
                "plugins": [
                    {"name": "other", "source": {"source": "local", "path": "./x"}},
                    {
                        "name": PACK,
                        "source": {"source": "local", "path": "./old"},
                    },
                ],
            }
        )
    )
    persona = _persona(tmp_path, "a", description="A", body="# A\n")

    CodexEmitter().emit(
        _ir(agents=(ResolvedAgent(name="a", persona_path=persona),)),
        dest,
    )

    marketplace = json.loads(path.read_text())
    assert marketplace["name"] == "existing"
    assert [plugin["name"] for plugin in marketplace["plugins"]] == ["other", PACK]
    assert marketplace["plugins"][1]["source"]["path"] == f"./plugins/{PACK}"


def test_uninstall_removes_plugin_and_marketplace_entry_only(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    path = dest.joinpath(*MARKETPLACE)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "name": "existing",
                "plugins": [
                    {"name": "other", "source": {"source": "local", "path": "./x"}}
                ],
            }
        )
    )
    (dest / "plugins" / "theirs").mkdir(parents=True)
    (dest / "plugins" / "theirs" / "keep.txt").write_text("keep\n")
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    CodexEmitter().emit(
        _ir(agents=(ResolvedAgent(name="a", persona_path=persona),)), dest
    )

    CodexEmitter().uninstall(PACK, dest)

    assert not dest.joinpath(*PLUGIN).exists()
    assert (dest / "plugins" / "theirs" / "keep.txt").read_text() == "keep\n"
    marketplace = json.loads(path.read_text())
    assert [plugin["name"] for plugin in marketplace["plugins"]] == ["other"]


def test_uninstall_unknown_pack_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(EmitError, match="no pack 'ghost' is installed for codex"):
        CodexEmitter().uninstall("ghost", tmp_path / "out")


def test_uninstall_rejects_names_that_are_not_pack_names(tmp_path: Path) -> None:
    # Fix 1: the shared guard applies to Codex too.
    dest = tmp_path / "out"
    (dest / "plugins" / "theirs").mkdir(parents=True)

    with pytest.raises(EmitError, match="is not a valid pack name"):
        CodexEmitter().uninstall("..", dest)

    assert (dest / "plugins" / "theirs").is_dir()


def test_install_refuses_to_wipe_a_directory_it_did_not_write(tmp_path: Path) -> None:
    # Fix 2: a plugin directory without a crewspec state file is not ours.
    dest = tmp_path / "out"
    theirs = dest.joinpath(*PLUGIN) / ".codex-plugin" / "plugin.json"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("{}")
    persona = _persona(tmp_path, "a", description="A", body="# A\n")

    with pytest.raises(EmitError, match="was not written by crewspec"):
        CodexEmitter().emit(
            _ir(agents=(ResolvedAgent(name="a", persona_path=persona),)), dest
        )

    assert theirs.read_text() == "{}"


def test_failed_install_does_not_lock_the_pack_out(tmp_path: Path) -> None:
    # N1: an error after the wipe used to leave plugin.json without a state
    # file, so every later install was refused as "not written by crewspec".
    dest = tmp_path / "out"
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    lint = _skill(tmp_path, "lint")
    good = _ir(agents=(ResolvedAgent(name="a", persona_path=persona),), skills=(lint,))
    CodexEmitter().emit(good, dest)
    root = dest.joinpath(*PLUGIN)

    clash = _persona(tmp_path, "lint", description="L", body="# L\n")
    bad = _ir(agents=(ResolvedAgent(name="lint", persona_path=clash),), skills=(lint,))
    with pytest.raises(EmitError, match="must not collide"):
        CodexEmitter().emit(bad, dest)
    # The previous install is intact, state file included.
    assert (root / ".codex-plugin" / "crewspec.json").is_file()
    assert (root / "skills" / "a" / "SKILL.md").is_file()

    market = (tmp_path / "out").joinpath(*MARKETPLACE)
    market.write_text("{not json")
    with pytest.raises(EmitError, match="not valid JSON"):
        CodexEmitter().emit(good, dest)
    assert (root / ".codex-plugin" / "crewspec.json").is_file()

    market.unlink()
    CodexEmitter().emit(good, dest)
    CodexEmitter().uninstall(PACK, dest)
    assert not root.exists()


def test_agent_skill_name_collision_raises(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "lint", description="Lint agent", body="# L\n")
    skill = _skill(tmp_path, "lint")
    ir = _ir(
        agents=(ResolvedAgent(name="lint", persona_path=persona, skills=(skill,)),),
        skills=(skill,),
    )

    with pytest.raises(EmitError, match="must not collide"):
        CodexEmitter().emit(ir, tmp_path / "out")


def test_missing_persona_description_raises(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description=None, body="# A\n")
    ir = _ir(agents=(ResolvedAgent(name="a", persona_path=persona),))

    with pytest.raises(MissingPersonaDescription, match="agent 'a'"):
        CodexEmitter().emit(ir, tmp_path / "out")


def test_reemit_is_byte_identical_and_clears_stale_files(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    skill = _skill(tmp_path, "lint")
    ir = _ir(
        agents=(ResolvedAgent(name="a", persona_path=persona, skills=(skill,)),),
        skills=(skill,),
        mcps=(_mcp("m", StdioMcpServer(command="serve")),),
    )

    dest = tmp_path / "out"
    CodexEmitter().emit(ir, dest)
    root = dest.joinpath(*PLUGIN)
    first = _snapshot(root)

    stale = root / "skills" / "removed" / "SKILL.md"
    stale.parent.mkdir()
    stale.write_text("old\n")

    CodexEmitter().emit(ir, dest)
    second = _snapshot(root)

    assert second == first
    assert not stale.exists()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
