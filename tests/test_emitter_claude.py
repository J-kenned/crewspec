"""Tests for the Claude emitter: IR to plugin tree, ``.claude.json``, ``CLAUDE.md``.

The IR is built in memory (as in ``test_resolver``), but the emitter does real
IO, it reads persona files and copies skill trees, so personas and skills are
written under ``tmp_path``. Assertions are golden: they pin the produced tree and
file contents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from crewspec.emitters import ClaudeEmitter, EmitError, MissingPersonaDescription
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models import HttpMcpServer, StdioMcpServer
from crewspec.models.mcp import McpServer

PACK = "pack"
PLUGIN = (".claude", "skills", PACK)
BEGIN = f"<!-- crewspec:{PACK}:begin -->"
END = f"<!-- crewspec:{PACK}:end -->"


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

    for subdir, contents in (extra or {}).items():
        sub = root / subdir
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "file.md").write_text(contents)

    return ResolvedSkill(name=name, path=root, skill_md=skill_md)


def _mcp(name: str, server: McpServer) -> ResolvedMcp:
    return ResolvedMcp(name=name, server=server)


def _agent(tmp_path: Path, name: str = "a", **kwargs: object) -> ResolvedAgent:
    persona = _persona(tmp_path, name, description=f"Agent {name}", body=f"# {name}\n")
    return ResolvedAgent(name=name, persona_path=persona, **kwargs)  # type: ignore[arg-type]


def _instructions(tmp_path: Path, text: str = "# Rules\n\nBe brief.\n") -> Path:
    path = tmp_path / "src" / "instructions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _ir(**kwargs: object) -> ResolvedIR:
    return ResolvedIR(name=PACK, **kwargs)  # type: ignore[arg-type]


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


def _claude_json(dest: Path) -> dict[str, object]:
    data = json.loads((dest / ".claude.json").read_text())
    assert isinstance(data, dict)
    return data


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --- plugin directory -------------------------------------------------------


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
    ir = _ir(
        agents=(
            ResolvedAgent(
                name="reviewer",
                persona_path=persona,
                tools=("Read", "Edit"),
                skills=(skill,),
            ),
        ),
        skills=(skill,),
        mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
    )

    root = _emit(tmp_path, ir)
    meta, body = _frontmatter(root / "agents" / "reviewer.md")

    assert meta["name"] == "reviewer"
    assert meta["description"] == "Reviews code for quality"
    # Fix 6: exactly the declared allowlist; the global MCP is not appended.
    assert meta["tools"] == "Read, Edit"
    # Fix 7: skills is a YAML list, as the Claude Code docs show.
    assert meta["skills"] == ["lint"]
    # Plugin agents may not declare mcpServers; servers live in .claude.json.
    assert "mcpServers" not in meta
    assert body == "# Reviewer\n\nReview it.\n"


def test_tools_allowlist_is_exactly_what_the_agent_declares(tmp_path: Path) -> None:
    ir = _ir(
        agents=(_agent(tmp_path, tools=("Read", "mcp__github__*")),),
        mcps=(
            _mcp("github", StdioMcpServer(command="gh")),
            _mcp("deploy", StdioMcpServer(command="deploy")),
        ),
    )

    meta, _ = _frontmatter(_emit(tmp_path, ir) / "agents" / "a.md")

    assert meta["tools"] == "Read, mcp__github__*"


def test_empty_allowed_tools_omits_tools_field(tmp_path: Path) -> None:
    ir = _ir(
        agents=(_agent(tmp_path, "helper"),),
        mcps=(_mcp("github", StdioMcpServer(command="gh-mcp")),),
    )

    root = _emit(tmp_path, ir)
    meta, _ = _frontmatter(root / "agents" / "helper.md")

    assert "tools" not in meta
    assert "skills" not in meta


def test_plugin_is_named_after_the_pack(tmp_path: Path) -> None:
    root = _emit(tmp_path, _ir(agents=(_agent(tmp_path),)))
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())

    assert root == tmp_path / "out" / ".claude" / "skills" / PACK
    assert manifest == {"name": PACK, "description": "Generated by crewspec."}


def test_state_file_records_what_was_written(tmp_path: Path) -> None:
    ir = _ir(
        instructions=_instructions(tmp_path),
        mcps=(_mcp("m", StdioMcpServer(command="serve")),),
    )
    root = _emit(tmp_path, ir)

    state = json.loads((root / ".claude-plugin" / "crewspec.json").read_text())
    assert state == {"name": PACK, "mcps": ["m"], "instructions": True}


def test_every_declared_skill_is_copied_even_if_no_agent_lists_it(
    tmp_path: Path,
) -> None:
    lint = _skill(tmp_path, "lint", extra={"references": "see this", "scripts": "run"})
    orphan = _skill(tmp_path, "orphan")
    ir = _ir(agents=(_agent(tmp_path, skills=(lint,)),), skills=(lint, orphan))

    root = _emit(tmp_path, ir)

    assert sorted(p.name for p in (root / "skills").iterdir()) == ["lint", "orphan"]
    assert (root / "skills" / "lint" / "SKILL.md").read_text() == (
        lint.skill_md.read_text()
    )
    assert (root / "skills" / "lint" / "references" / "file.md").read_text() == (
        "see this"
    )
    assert (root / "skills" / "lint" / "scripts" / "file.md").read_text() == "run"


def test_skill_copies_whole_directory_not_just_standard_subdirs(
    tmp_path: Path,
) -> None:
    # A skill *is* its directory: loose files and non-standard subdirs survive.
    skill = _skill(tmp_path, "lint")
    (skill.path / "LICENSE.txt").write_text("MIT")
    (skill.path / "reference.md").write_text("# ref")
    (skill.path / "data").mkdir()
    (skill.path / "data" / "table.json").write_text("{}")

    skill_dir = _emit(tmp_path, _ir(skills=(skill,))) / "skills" / "lint"
    assert (skill_dir / "LICENSE.txt").read_text() == "MIT"
    assert (skill_dir / "reference.md").read_text() == "# ref"
    assert (skill_dir / "data" / "table.json").read_text() == "{}"


def test_skill_copy_drops_vcs_and_cache_junk(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "lint")
    (skill.path / ".git").mkdir()
    (skill.path / ".git" / "config").write_text("[core]\n")
    (skill.path / "__pycache__").mkdir()
    (skill.path / "__pycache__" / "m.cpython-312.pyc").write_text("x")
    (skill.path / "run.pyc").write_text("x")
    (skill.path / ".DS_Store").write_text("x")

    skill_dir = _emit(tmp_path, _ir(skills=(skill,))) / "skills" / "lint"
    assert (skill_dir / "SKILL.md").is_file()
    assert not (skill_dir / ".git").exists()
    assert not (skill_dir / "__pycache__").exists()
    assert not (skill_dir / "run.pyc").exists()
    assert not (skill_dir / ".DS_Store").exists()


def test_lowercase_skill_md_normalized_on_emit(tmp_path: Path) -> None:
    # A source cased as skill.md (valid on macOS) must emit as SKILL.md so the
    # plugin loads on a case-sensitive host.
    root = tmp_path / "src" / "skills" / "lower"
    root.mkdir(parents=True)
    (root / "skill.md").write_text(
        "---\nname: lower\ndescription: A lower skill\n---\n\n# lower\n"
    )
    skill = ResolvedSkill(name="lower", path=root, skill_md=root / "SKILL.md")

    skill_dir = _emit(tmp_path, _ir(skills=(skill,))) / "skills" / "lower"
    names = [p.name for p in skill_dir.iterdir()]
    assert "SKILL.md" in names
    assert "skill.md" not in names


def test_no_mcps_or_instructions_touches_nothing_outside_the_plugin(
    tmp_path: Path,
) -> None:
    root = _emit(tmp_path, _ir(agents=(_agent(tmp_path),)))

    assert not (root / ".mcp.json").exists()
    assert not (root / "skills").exists()
    assert (root / "agents" / "a.md").exists()
    assert not (tmp_path / "out" / ".claude.json").exists()
    assert not (tmp_path / "out" / ".claude" / "CLAUDE.md").exists()


def test_missing_persona_description_raises(tmp_path: Path) -> None:
    persona = _persona(tmp_path, "a", description=None, body="# A\n")
    ir = _ir(agents=(ResolvedAgent(name="a", persona_path=persona),))

    with pytest.raises(MissingPersonaDescription, match="agent 'a'"):
        ClaudeEmitter().emit(ir, tmp_path / "out")


def test_install_refuses_to_wipe_a_directory_it_did_not_write(tmp_path: Path) -> None:
    # Fix 2: a hand-written skill that happens to share the pack's name.
    dest = tmp_path / "out"
    mine = dest.joinpath(*PLUGIN) / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("mine\n")

    with pytest.raises(EmitError, match="was not written by crewspec") as exc:
        ClaudeEmitter().emit(_ir(agents=(_agent(tmp_path),)), dest)

    assert str(dest.joinpath(*PLUGIN)) in str(exc.value)
    assert mine.read_text() == "mine\n"
    assert not (dest / ".claude.json").exists()


# --- .claude.json -------------------------------------------------------------


def test_mcps_land_in_user_scope_claude_json_with_rewritten_refs(
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
                    headers={"Authorization": "Bearer ${secret:KEY}"},
                ),
            ),
            _mcp("bare", StdioMcpServer(command="run")),
        ),
    )

    root = _emit(tmp_path, ir)

    assert not (root / ".mcp.json").exists()
    assert _claude_json(tmp_path / "out") == {
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


def test_merge_keeps_foreign_keys_and_writes_indent_two(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / ".claude.json").write_text(
        json.dumps(
            {
                "numStartups": 7,
                "projects": {"/Users/me/réseau": {"allowedTools": ["Bash"]}},
                "mcpServers": {"theirs": {"type": "stdio", "command": "x"}},
                "tipsHistory": {"a": 1},
            },
            indent=4,
            ensure_ascii=False,
        )
    )

    ClaudeEmitter().emit(_ir(mcps=(_mcp("m", StdioMcpServer(command="serve")),)), dest)

    text = (dest / ".claude.json").read_text()
    assert json.loads(text) == {
        "numStartups": 7,
        "projects": {"/Users/me/réseau": {"allowedTools": ["Bash"]}},
        "mcpServers": {
            "theirs": {"type": "stdio", "command": "x"},
            "m": {"type": "stdio", "command": "serve"},
        },
        "tipsHistory": {"a": 1},
    }
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
    assert "réseau" in text


def test_dropped_mcp_is_removed_but_unowned_key_is_kept(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / ".claude.json").write_text(
        json.dumps({"mcpServers": {"theirs": {"type": "stdio", "command": "x"}}})
    )
    old = _mcp("old", StdioMcpServer(command="old"))
    new = _mcp("new", StdioMcpServer(command="new"))

    ClaudeEmitter().emit(_ir(mcps=(old, new)), dest)
    ClaudeEmitter().emit(_ir(mcps=(new,)), dest)

    servers = _claude_json(dest)["mcpServers"]
    assert isinstance(servers, dict)
    assert list(servers) == ["theirs", "new"]


def test_dropping_every_mcp_still_cleans_up(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    ClaudeEmitter().emit(_ir(mcps=(_mcp("m", StdioMcpServer(command="m")),)), dest)
    ClaudeEmitter().emit(_ir(), dest)

    assert _claude_json(dest) == {"mcpServers": {}}


def test_invalid_claude_json_is_an_error_not_overwritten(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / ".claude.json").write_text("{not json")

    with pytest.raises(EmitError, match="not valid JSON"):
        ClaudeEmitter().emit(_ir(mcps=(_mcp("m", StdioMcpServer(command="m")),)), dest)
    assert (dest / ".claude.json").read_text() == "{not json"


def test_failed_install_keeps_ownership_of_previous_keys(tmp_path: Path) -> None:
    # Fix 3: a failing install must not lose the record of what was written.
    dest = tmp_path / "out"
    a = _mcp("a", StdioMcpServer(command="a"))
    b = _mcp("b", StdioMcpServer(command="b"))
    ClaudeEmitter().emit(_ir(mcps=(a, b)), dest)

    bad = _mcp("c", StdioMcpServer(command="c", env={"K": "${file:/k}"}))
    with pytest.raises(EmitError, match="file:/k"):
        ClaudeEmitter().emit(_ir(mcps=(a, b, bad)), dest)

    state_path = dest.joinpath(*PLUGIN) / ".claude-plugin" / "crewspec.json"
    assert json.loads(state_path.read_text())["mcps"] == ["a", "b"]
    assert list(_claude_json(dest)["mcpServers"]) == ["a", "b"]  # type: ignore[arg-type]

    ClaudeEmitter().emit(_ir(mcps=(a,)), dest)

    assert list(_claude_json(dest)["mcpServers"]) == ["a"]  # type: ignore[arg-type]


def test_mcp_key_owned_by_another_pack_is_an_error(tmp_path: Path) -> None:
    # Fix 4: two packs may not write the same mcpServers key.
    dest = tmp_path / "out"
    github = _mcp("github", StdioMcpServer(command="gh"))
    ClaudeEmitter().emit(ResolvedIR(name="one", mcps=(github,)), dest)

    with pytest.raises(EmitError, match="already installed by pack 'one'") as exc:
        ClaudeEmitter().emit(ResolvedIR(name="two", mcps=(github,)), dest)

    assert "github" in str(exc.value)
    assert "pack 'two'" in str(exc.value)
    assert not (dest / ".claude" / "skills" / "two").exists()

    # A different key is fine, and uninstalling one pack leaves the other's.
    other = _mcp("other", StdioMcpServer(command="other"))
    ClaudeEmitter().emit(ResolvedIR(name="two", mcps=(other,)), dest)
    ClaudeEmitter().uninstall("one", dest)

    assert list(_claude_json(dest)["mcpServers"]) == ["other"]  # type: ignore[arg-type]


def test_project_scoped_mcp_is_never_written_to_user_scope(tmp_path: Path) -> None:
    # A project-scoped server is declared, not installed: it stays out of
    # .claude.json and the state file, and an unowned key of the same name
    # survives both install and uninstall.
    dest = tmp_path / "out"
    dest.mkdir()
    theirs = {"type": "stdio", "command": "mine"}
    (dest / ".claude.json").write_text(json.dumps({"mcpServers": {"local": theirs}}))
    ir = _ir(
        mcps=(
            _mcp("shared", StdioMcpServer(command="shared")),
            _mcp("local", StdioMcpServer(command="local", scope="project")),
            _mcp(
                "remote",
                HttpMcpServer(url="https://example.test/mcp", scope="project"),
            ),
        ),
    )

    ClaudeEmitter().emit(ir, dest)

    assert _claude_json(dest)["mcpServers"] == {
        "local": theirs,
        "shared": {"type": "stdio", "command": "shared"},
    }
    state_path = dest.joinpath(*PLUGIN) / ".claude-plugin" / "crewspec.json"
    assert json.loads(state_path.read_text())["mcps"] == ["shared"]

    ClaudeEmitter().uninstall(PACK, dest)

    assert _claude_json(dest) == {"mcpServers": {"local": theirs}}


def test_file_secret_is_an_error(tmp_path: Path) -> None:
    ir = _ir(
        mcps=(
            _mcp(
                "vault",
                StdioMcpServer(command="serve", env={"KEYFILE": "${file:/tmp/key}"}),
            ),
        ),
    )

    with pytest.raises(EmitError, match=r"\$\{file:/tmp/key\}"):
        ClaudeEmitter().emit(ir, tmp_path / "out")
    assert not (tmp_path / "out" / ".claude.json").exists()


# --- CLAUDE.md managed block ----------------------------------------------------


def test_instructions_create_claude_md_as_a_managed_block(tmp_path: Path) -> None:
    _emit(tmp_path, _ir(instructions=_instructions(tmp_path)))

    assert (tmp_path / "out" / ".claude" / "CLAUDE.md").read_text() == (
        f"{BEGIN}\n# Rules\n\nBe brief.\n{END}\n"
    )


def test_block_is_appended_after_a_blank_line(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    claude_md = dest / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("# Mine\n\nKeep this.\n")

    ClaudeEmitter().emit(_ir(instructions=_instructions(tmp_path, "Rules.")), dest)

    assert claude_md.read_text() == (
        f"# Mine\n\nKeep this.\n\n{BEGIN}\nRules.\n{END}\n"
    )


def test_block_is_replaced_in_place_leaving_the_rest_untouched(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "out"
    claude_md = dest / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    # No trailing newline after the tail and CRLF before: both must survive.
    claude_md.write_bytes(
        f"head\r\n\n{BEGIN}\nold\n{END}\n\n# Tail\n\n  spaced".encode()
    )

    ClaudeEmitter().emit(_ir(instructions=_instructions(tmp_path, "new\n\n")), dest)

    assert claude_md.read_bytes() == (
        f"head\r\n\n{BEGIN}\nnew\n{END}\n\n# Tail\n\n  spaced".encode()
    )


def test_block_is_removed_when_instructions_are_dropped(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    claude_md = dest / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("# Mine\n")

    ClaudeEmitter().emit(_ir(instructions=_instructions(tmp_path)), dest)
    ClaudeEmitter().emit(_ir(), dest)

    assert claude_md.read_text() == "# Mine\n\n"
    state = json.loads(
        (dest.joinpath(*PLUGIN) / ".claude-plugin" / "crewspec.json").read_text()
    )
    assert state["instructions"] is False


def test_other_packs_blocks_are_left_alone(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    claude_md = dest / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    other = "<!-- crewspec:other:begin -->\ntheirs\n<!-- crewspec:other:end -->\n"
    claude_md.write_text(other)

    ClaudeEmitter().emit(_ir(instructions=_instructions(tmp_path, "mine")), dest)
    ClaudeEmitter().emit(_ir(), dest)

    assert claude_md.read_text() == other + "\n"


@pytest.mark.parametrize("which", ["claude_md", "instructions"])
def test_non_utf8_input_is_a_clean_error(tmp_path: Path, which: str) -> None:
    # Fix 5: a non-UTF-8 CLAUDE.md or instructions file is an EmitError, raised
    # before anything is written.
    dest = tmp_path / "out"
    claude_md = dest / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_bytes(b"# Mine\n")
    instructions = _instructions(tmp_path)
    (claude_md if which == "claude_md" else instructions).write_bytes(b"\xff\xfe bad")

    with pytest.raises(EmitError, match="not valid UTF-8"):
        ClaudeEmitter().emit(_ir(instructions=instructions), dest)

    assert not dest.joinpath(*PLUGIN).exists()
    if which == "instructions":
        assert claude_md.read_bytes() == b"# Mine\n"


def test_unterminated_block_is_an_error(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    claude_md = dest / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text(f"{BEGIN}\nno end\n")

    with pytest.raises(EmitError, match="without a matching"):
        ClaudeEmitter().emit(_ir(instructions=_instructions(tmp_path)), dest)


# --- idempotency and uninstall ----------------------------------------------------


def test_two_installs_are_byte_identical_and_clear_stale_files(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / ".claude.json").write_text(json.dumps({"theirs": True}))
    (dest / ".claude").mkdir()
    (dest / ".claude" / "CLAUDE.md").write_text("# Mine\n")
    skill = _skill(tmp_path, "lint")
    ir = _ir(
        instructions=_instructions(tmp_path),
        agents=(_agent(tmp_path, skills=(skill,)),),
        skills=(skill,),
        mcps=(_mcp("m", StdioMcpServer(command="serve")),),
    )

    ClaudeEmitter().emit(ir, dest)
    first = _snapshot(dest)

    # A stale artifact from a previous run must not survive re-emission.
    stale = dest.joinpath(*PLUGIN) / "agents" / "removed.md"
    stale.write_text("old\n")

    ClaudeEmitter().emit(ir, dest)

    assert _snapshot(dest) == first
    assert not stale.exists()


def test_uninstall_removes_only_what_the_pack_wrote(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / ".claude.json").write_text(
        json.dumps({"mcpServers": {"theirs": {"type": "stdio", "command": "x"}}})
    )
    (dest / ".claude" / "skills" / "my-skill").mkdir(parents=True)
    (dest / ".claude" / "skills" / "my-skill" / "SKILL.md").write_text("mine\n")
    (dest / ".claude" / "CLAUDE.md").write_text("# Mine\n")
    ir = _ir(
        instructions=_instructions(tmp_path),
        agents=(_agent(tmp_path),),
        mcps=(_mcp("m", StdioMcpServer(command="serve")),),
    )
    ClaudeEmitter().emit(ir, dest)

    ClaudeEmitter().uninstall(PACK, dest)

    assert not dest.joinpath(*PLUGIN).exists()
    assert (dest / ".claude" / "skills" / "my-skill" / "SKILL.md").read_text() == (
        "mine\n"
    )
    assert _claude_json(dest) == {
        "mcpServers": {"theirs": {"type": "stdio", "command": "x"}}
    }
    assert (dest / ".claude" / "CLAUDE.md").read_text() == "# Mine\n\n"


def test_uninstall_unknown_pack_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(EmitError, match="no pack 'ghost' is installed for claude"):
        ClaudeEmitter().uninstall("ghost", tmp_path / "out")


def test_uninstall_requires_a_state_file(tmp_path: Path) -> None:
    # Fix 1: a plugin directory crewspec did not write is never removed.
    dest = tmp_path / "out"
    manifest = dest.joinpath(*PLUGIN) / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")

    with pytest.raises(EmitError, match=f"no pack '{PACK}' is installed"):
        ClaudeEmitter().uninstall(PACK, dest)

    assert manifest.exists()


@pytest.mark.parametrize("name", [".", "..", "../../victim", "Pack", "a/b"])
def test_uninstall_rejects_names_that_are_not_pack_names(
    tmp_path: Path, name: str
) -> None:
    # Fix 1: NAME is validated before any path is built or removed.
    dest = tmp_path / "out"
    ClaudeEmitter().emit(_ir(agents=(_agent(tmp_path),)), dest)
    victim = tmp_path / "victim"
    victim.mkdir()

    with pytest.raises(EmitError, match="is not a valid pack name"):
        ClaudeEmitter().uninstall(name, dest)

    assert dest.joinpath(*PLUGIN).is_dir()
    assert victim.is_dir()


def test_symlinked_user_files_are_written_through_the_link(tmp_path: Path) -> None:
    # N2: a dotfiles-style symlink must survive; the file behind it is updated.
    dest = tmp_path / "out"
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "claude.json").write_text('{"theirs": true}')
    (dotfiles / "CLAUDE.md").write_text("# Mine\n")
    (dest / ".claude").mkdir(parents=True)
    (dest / ".claude.json").symlink_to(dotfiles / "claude.json")
    (dest / ".claude" / "CLAUDE.md").symlink_to(dotfiles / "CLAUDE.md")

    ClaudeEmitter().emit(
        _ir(
            instructions=_instructions(tmp_path, "Rules."),
            mcps=(_mcp("m", StdioMcpServer(command="m")),),
        ),
        dest,
    )

    assert (dest / ".claude.json").is_symlink()
    assert (dest / ".claude" / "CLAUDE.md").is_symlink()
    assert json.loads((dotfiles / "claude.json").read_text()) == {
        "theirs": True,
        "mcpServers": {"m": {"type": "stdio", "command": "m"}},
    }
    assert (dotfiles / "CLAUDE.md").read_text() == (
        f"# Mine\n\n{BEGIN}\nRules.\n{END}\n"
    )


def test_fresh_user_files_get_umask_mode_not_0600(tmp_path: Path) -> None:
    # N4: mkstemp creates 0600; a new file should look like one `open` made.
    dest = tmp_path / "out"
    ClaudeEmitter().emit(
        _ir(
            instructions=_instructions(tmp_path),
            mcps=(_mcp("m", StdioMcpServer(command="m")),),
        ),
        dest,
    )

    umask = os.umask(0)
    os.umask(umask)
    expected = 0o666 & ~umask
    assert (dest / ".claude.json").stat().st_mode & 0o777 == expected
    assert (dest / ".claude" / "CLAUDE.md").stat().st_mode & 0o777 == expected


def test_corrupt_sibling_state_warns_instead_of_blocking(tmp_path: Path) -> None:
    # N3: match `list`: a damaged unrelated pack is a warning, not a failure.
    dest = tmp_path / "out"
    ClaudeEmitter().emit(ResolvedIR(name="other", agents=(_agent(tmp_path),)), dest)
    (
        dest / ".claude" / "skills" / "other" / ".claude-plugin" / "crewspec.json"
    ).write_text("{not json")

    with pytest.warns(UserWarning, match="not a valid crewspec state file"):
        ClaudeEmitter().emit(_ir(mcps=(_mcp("m", StdioMcpServer(command="m")),)), dest)

    assert dest.joinpath(*PLUGIN).is_dir()


def test_user_files_are_written_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fix 8: the rename is the commit point, so a failure before it leaves the
    # original untouched and no temp file behind.
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / ".claude.json").write_text('{"theirs": true}')

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        ClaudeEmitter().emit(_ir(mcps=(_mcp("m", StdioMcpServer(command="m")),)), dest)

    assert (dest / ".claude.json").read_text() == '{"theirs": true}'
    assert [p.name for p in dest.iterdir() if p.name != ".claude"] == [".claude.json"]

    monkeypatch.undo()
    ClaudeEmitter().emit(
        _ir(
            instructions=_instructions(tmp_path),
            mcps=(_mcp("m", StdioMcpServer(command="m")),),
        ),
        dest,
    )
    assert sorted(p.name for p in dest.iterdir()) == [".claude", ".claude.json"]
    assert sorted(p.name for p in (dest / ".claude").iterdir()) == [
        "CLAUDE.md",
        "skills",
    ]
