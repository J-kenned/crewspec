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

PLUGIN = ("plugins", "crewspec")
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
    ir = ResolvedIR(agents=(ResolvedAgent(name="reviewer", persona_path=persona),))

    root = _emit(tmp_path, ir)
    manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
    marketplace = json.loads((tmp_path / "out").joinpath(*MARKETPLACE).read_text())

    assert manifest["name"] == "crewspec"
    assert manifest["version"] == "0.1.1"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert marketplace == {
        "name": "local-repo",
        "plugins": [
            {
                "name": "crewspec",
                "source": {"source": "local", "path": "./plugins/crewspec"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Developer Tools",
                "interface": {"displayName": "crewspec"},
            }
        ],
    }


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
    ir = ResolvedIR(
        agents=(
            ResolvedAgent(
                name="reviewer",
                persona_path=persona,
                tools=("Read", "Edit"),
                skills=(fmt, lint),
                mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
            ),
        ),
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


def test_mcp_json_uses_codex_plugin_manifest_and_server_shape(
    tmp_path: Path,
) -> None:
    ir = ResolvedIR(
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
                        "name": "crewspec",
                        "source": {"source": "local", "path": "./old"},
                    },
                ],
            }
        )
    )
    persona = _persona(tmp_path, "a", description="A", body="# A\n")

    CodexEmitter().emit(
        ResolvedIR(agents=(ResolvedAgent(name="a", persona_path=persona),)),
        dest,
    )

    marketplace = json.loads(path.read_text())
    assert marketplace["name"] == "existing"
    assert [plugin["name"] for plugin in marketplace["plugins"]] == [
        "other",
        "crewspec",
    ]
    assert marketplace["plugins"][1]["source"]["path"] == "./plugins/crewspec"


def test_agent_skill_name_collision_raises(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "lint", description="Lint agent", body="# L\n")
    skill = _skill(tmp_path, "lint")
    ir = ResolvedIR(
        agents=(ResolvedAgent(name="lint", persona_path=persona, skills=(skill,)),),
    )

    with pytest.raises(EmitError, match="must not collide"):
        CodexEmitter().emit(ir, tmp_path / "out")


def test_missing_persona_description_raises(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description=None, body="# A\n")
    ir = ResolvedIR(agents=(ResolvedAgent(name="a", persona_path=persona),))

    with pytest.raises(MissingPersonaDescription, match="agent 'a'"):
        CodexEmitter().emit(ir, tmp_path / "out")


def test_reemit_is_byte_identical_and_clears_stale_files(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    skill = _skill(tmp_path, "lint")
    ir = ResolvedIR(
        agents=(ResolvedAgent(name="a", persona_path=persona, skills=(skill,)),),
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
