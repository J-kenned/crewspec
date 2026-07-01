"""Tests for the Claude emitter: IR to plugin file tree.

The IR is built in memory (as in ``test_resolver``), but the emitter does real
IO, it reads persona files and copies skill trees, so personas and skills are
written under ``tmp_path``. Assertions are golden: they pin the produced tree and
file contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from crewspec.emitters import ClaudeEmitter, MissingPersonaDescription
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models import HttpMcpServer, StdioMcpServer
from crewspec.models.mcp import McpServer

PLUGIN = (".claude", "skills", "crewspec")


def _persona(tmp_path: Path, name: str, *, description: str | None, body: str) -> Path:
    meta = f"name: {name}\n"
    if description is not None:
        meta += f"description: {description}\n"
    path = tmp_path / "src" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{meta}---\n\n{body}")
    return path


def _skill(
    tmp_path: Path,
    name: str,
    *,
    extra: dict[str, str] | None = None,
) -> ResolvedSkill:
    root = tmp_path / "src" / "skills" / name
    root.mkdir(parents=True, exist_ok=True)
    skill_md = root / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: A {name} skill\n---\n\n# {name}\n"
    )

    extra_dirs: list[Path] = []
    for subdir, contents in (extra or {}).items():
        sub = root / subdir
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "file.md").write_text(contents)
        extra_dirs.append(sub)

    return ResolvedSkill(
        name=name,
        path=root,
        skill_md=skill_md,
        extra_dirs=tuple(extra_dirs),
    )


def _mcp(name: str, server: McpServer) -> ResolvedMcp:
    return ResolvedMcp(name=name, server=server)


def _emit(tmp_path: Path, ir: ResolvedIR) -> Path:
    dest = tmp_path / "out"
    ClaudeEmitter().emit(ir, dest)
    return dest.joinpath(*PLUGIN)


def _frontmatter(agent_md: Path) -> tuple[dict[str, object], str]:
    text = agent_md.read_text()
    lines = text.split("\n")
    end = lines.index("---", 1)
    meta = yaml.safe_load("\n".join(lines[1:end]))
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def test_agent_frontmatter_maps_tools_skills_and_omits_mcpservers(
    tmp_path: Path,
) -> None:
    persona = _persona(
        tmp_path,
        "reviewer",
        description="Reviews code for quality",
        body="# Reviewer\n\nReview it.\n",
    )
    skill = _skill(tmp_path, "lint")
    ir = ResolvedIR(
        agents=(
            ResolvedAgent(
                name="reviewer",
                persona_path=persona,
                tools=("Read", "Edit"),
                skills=(skill,),
                mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
            ),
        ),
        mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
    )

    root = _emit(tmp_path, ir)
    meta, body = _frontmatter(root / "agents" / "reviewer.md")

    assert meta["name"] == "reviewer"
    assert meta["description"] == "Reviews code for quality"
    # Allowlist appends the granted MCP so it survives the allowlist.
    assert meta["tools"] == "Read, Edit, mcp__github"
    assert meta["skills"] == "lint"
    # Plugin agents may not declare mcpServers; scoping lives in .mcp.json.
    assert "mcpServers" not in meta
    assert body == "# Reviewer\n\nReview it.\n"


def test_empty_allowed_tools_omits_tools_field(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "helper", description="Helps out", body="# Helper\n")
    ir = ResolvedIR(
        agents=(
            ResolvedAgent(
                name="helper",
                persona_path=persona,
                tools=(),
                mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
            ),
        ),
    )

    root = _emit(tmp_path, ir)
    meta, _ = _frontmatter(root / "agents" / "helper.md")

    assert "tools" not in meta
    assert "skills" not in meta


def test_mcp_json_renders_servers_and_rewrites_refs(tmp_path: Path) -> None:
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
                    headers={"Authorization": "Bearer ${secret:KEY}"},
                ),
            ),
            _mcp("bare", StdioMcpServer(command="run")),
        ),
    )

    root = _emit(tmp_path, ir)
    data = yaml.safe_load((root / ".mcp.json").read_text())

    assert data == {
        "mcpServers": {
            "auth": {
                "type": "stdio",
                "command": "serve",
                # Env ref kept verbatim; secret ref collapsed to a bare var.
                "args": ["--token", "${TOKEN}"],
                "env": {"HOST": "${HOST:-localhost}"},
            },
            "remote": {
                "type": "http",
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "Bearer ${KEY}"},
            },
            # No args/env keys when empty.
            "bare": {"type": "stdio", "command": "run"},
        }
    }


def test_file_secret_warns_and_is_kept_verbatim(tmp_path: Path) -> None:
    ir = ResolvedIR(
        mcps=(
            _mcp(
                "vault",
                StdioMcpServer(command="serve", env={"KEYFILE": "${file:/tmp/key}"}),
            ),
        ),
    )

    with pytest.warns(UserWarning, match="file:/tmp/key"):
        root = _emit(tmp_path, ir)

    data = yaml.safe_load((root / ".mcp.json").read_text())
    assert data["mcpServers"]["vault"]["env"]["KEYFILE"] == "${file:/tmp/key}"


def test_skill_copied_with_extra_dirs(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description="An agent", body="# A\n")
    skill = _skill(tmp_path, "lint", extra={"references": "see this", "scripts": "run"})
    ir = ResolvedIR(
        agents=(ResolvedAgent(name="a", persona_path=persona, skills=(skill,)),),
    )

    root = _emit(tmp_path, ir)
    skill_dir = root / "skills" / "lint"

    assert (skill_dir / "SKILL.md").read_text() == skill.skill_md.read_text()
    assert (skill_dir / "references" / "file.md").read_text() == "see this"
    assert (skill_dir / "scripts" / "file.md").read_text() == "run"


def test_shared_skill_copied_once(tmp_path: Path) -> None:
    p1 = _persona(tmp_path, "a", description="A", body="# A\n")
    p2 = _persona(tmp_path, "b", description="B", body="# B\n")
    skill = _skill(tmp_path, "lint")
    ir = ResolvedIR(
        agents=(
            ResolvedAgent(name="a", persona_path=p1, skills=(skill,)),
            ResolvedAgent(name="b", persona_path=p2, skills=(skill,)),
        ),
    )

    root = _emit(tmp_path, ir)
    assert sorted(p.name for p in (root / "skills").iterdir()) == ["lint"]


def test_plugin_manifest_written(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    ir = ResolvedIR(agents=(ResolvedAgent(name="a", persona_path=persona),))

    root = _emit(tmp_path, ir)
    manifest = yaml.safe_load((root / ".claude-plugin" / "plugin.json").read_text())

    assert manifest == {"name": "crewspec", "description": "Generated by crewspec."}


def test_no_mcps_skips_mcp_json_and_skills_dir(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    ir = ResolvedIR(agents=(ResolvedAgent(name="a", persona_path=persona),))

    root = _emit(tmp_path, ir)

    assert not (root / ".mcp.json").exists()
    assert not (root / "skills").exists()
    assert (root / "agents" / "a.md").exists()


def test_missing_persona_description_raises(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description=None, body="# A\n")
    ir = ResolvedIR(agents=(ResolvedAgent(name="a", persona_path=persona),))

    with pytest.raises(MissingPersonaDescription, match="agent 'a'"):
        ClaudeEmitter().emit(ir, tmp_path / "out")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_reemit_is_byte_identical_and_clears_stale_files(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description="A", body="# A\n")
    skill = _skill(tmp_path, "lint")
    ir = ResolvedIR(
        agents=(ResolvedAgent(name="a", persona_path=persona, skills=(skill,)),),
        mcps=(_mcp("m", StdioMcpServer(command="serve")),),
    )

    dest = tmp_path / "out"
    ClaudeEmitter().emit(ir, dest)
    root = dest.joinpath(*PLUGIN)
    first = _snapshot(root)

    # A stale artifact from a previous run must not survive re-emission.
    stale = root / "agents" / "removed.md"
    stale.write_text("old\n")

    ClaudeEmitter().emit(ir, dest)
    second = _snapshot(root)

    assert second == first
    assert not stale.exists()
