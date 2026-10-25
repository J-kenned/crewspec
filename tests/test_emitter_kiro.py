"""Tests for the Kiro emitter: IR to kiro-cli agent specs, plus a golden tree."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from crewspec.emitters import KiroEmitter
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models import StdioMcpServer

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "night-shift"
GOLDEN = REPO_ROOT / "tests" / "golden" / "kiro" / "night-shift"
UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"
PACK = "pack"


def _persona(
    tmp_path: Path, name: str, description: str | None = "Does things"
) -> Path:
    meta = f"name: {name}\n" + (f"description: {description}\n" if description else "")
    path = tmp_path / "src" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{meta}---\n\n# {name}\n\nBe {name}.\n")
    return path


def _skill(tmp_path: Path, name: str, mcps: tuple[str, ...] = ()) -> ResolvedSkill:
    root = tmp_path / "src" / "skills" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n")
    return ResolvedSkill(name=name, path=root, skill_md=root / "SKILL.md", mcps=mcps)


def _mcp(name: str) -> ResolvedMcp:
    return ResolvedMcp(
        name=name,
        server=StdioMcpServer(
            type="stdio", command="npx", args=["-y", name], env={"T": "${secret:tok}"}
        ),
    )


def _emit(tmp_path: Path, ir: ResolvedIR) -> Path:
    dest = tmp_path / "kiro"
    KiroEmitter().emit(ir, dest)
    return dest


def _spec(dest: Path, name: str) -> dict[str, object]:
    return json.loads((dest / "agents" / f"{name}.json").read_text())


def test_agent_spec_translates_tools_and_inlines_prompt(tmp_path: Path) -> None:
    agent = ResolvedAgent(
        name="rev",
        persona_path=_persona(tmp_path, "rev"),
        tools=("Read", "Grep", "Bash", "mcp__gh__*", "mcp__jira__get"),
        skills=(_skill(tmp_path, "lint"),),
        extensions={"x-operative": {"model": "claude-opus-5-5"}},
    )
    ir = ResolvedIR(
        name=PACK,
        agents=(agent,),
        skills=agent.skills,
        mcps=(_mcp("gh"), _mcp("jira"), _mcp("other")),
    )

    dest = _emit(tmp_path, ir)
    spec = _spec(dest, "rev")

    assert spec["name"] == "rev"
    assert spec["description"] == "Does things"
    assert spec["prompt"].startswith("# rev\n")
    # Claude names collapse onto kiro-cli built-ins, deduplicated, in order.
    assert spec["tools"] == ["fs_read", "execute_bash", "@gh", "@jira/get"]
    assert spec["allowedTools"] == spec["tools"]
    # Only reachable servers are mounted, and the global mcp.json stays off.
    assert sorted(spec["mcpServers"]) == ["gh", "jira"]
    assert spec["mcpServers"]["gh"]["env"] == {"T": "${tok}"}
    assert spec["includeMcpJson"] is False
    assert spec["resources"] == [
        f"skill://{(dest / 'skills' / 'lint' / 'SKILL.md').as_posix()}"
    ]
    assert spec["model"] == "claude-opus-5-5"
    assert (dest / "skills" / "lint" / "SKILL.md").is_file()
    manifest = json.loads((dest / "crewspec" / PACK / "app.json").read_text())
    assert manifest["agents"] == ["../../agents/rev.json"]


def test_no_grants_means_every_tool_and_server(tmp_path: Path) -> None:
    agent = ResolvedAgent(name="all", persona_path=_persona(tmp_path, "all"))
    ir = ResolvedIR(name=PACK, agents=(agent,), mcps=(_mcp("a"), _mcp("b")))

    spec = _spec(_emit(tmp_path, ir), "all")

    assert spec["tools"] == ["*"]
    assert sorted(spec["mcpServers"]) == ["a", "b"]
    assert "model" not in spec


def test_skill_deps_reach_servers_and_unknown_tool_warns(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "s", mcps=("viaskill",))
    agent = ResolvedAgent(
        name="a",
        persona_path=_persona(tmp_path, "a"),
        tools=("Read", "Odd"),
        skills=(skill,),
    )
    ir = ResolvedIR(
        name=PACK, agents=(agent,), skills=(skill,), mcps=(_mcp("viaskill"), _mcp("x"))
    )

    with pytest.warns(UserWarning, match="tool 'Odd' has no kiro-cli name"):
        spec = _spec(_emit(tmp_path, ir), "a")

    assert spec["tools"] == ["fs_read", "Odd"]
    assert list(spec["mcpServers"]) == ["viaskill"]


def test_missing_description_is_an_error(tmp_path: Path) -> None:
    from crewspec.emitters import MissingPersonaDescription

    agent = ResolvedAgent(
        name="n", persona_path=_persona(tmp_path, "n", description=None)
    )
    with pytest.raises(MissingPersonaDescription):
        _emit(tmp_path, ResolvedIR(name=PACK, agents=(agent,)))


def test_uninstall_removes_agents_skills_and_pack_dir(tmp_path: Path) -> None:
    agent = ResolvedAgent(
        name="rev",
        persona_path=_persona(tmp_path, "rev"),
        skills=(_skill(tmp_path, "lint"),),
    )
    dest = _emit(tmp_path, ResolvedIR(name=PACK, agents=(agent,), skills=agent.skills))
    (dest / "agents" / "mine.json").write_text("{}")

    KiroEmitter().uninstall(PACK, dest)

    assert not (dest / "agents" / "rev.json").exists()
    assert not (dest / "skills" / "lint").exists()
    assert not (dest / "crewspec" / PACK).exists()
    assert (dest / "agents" / "mine.json").exists(), (
        "a file crewspec did not write is kept"
    )


def test_withheld_blocks_never_reach_the_spec(tmp_path: Path) -> None:
    """kiro-cli rejects unknown keys, so nothing crewspec-specific may be written."""

    from crewspec.parser import load_crewfile, resolve_crewfile
    from crewspec.resolver import resolve

    ir = resolve(load_crewfile(resolve_crewfile(EXAMPLE)).registry)
    spec = _spec(_emit(tmp_path, ir), "night-shift")

    assert set(spec) <= {
        "name",
        "description",
        "prompt",
        "tools",
        "allowedTools",
        "mcpServers",
        "includeMcpJson",
        "resources",
        "model",
    }
    assert "slack" not in spec["mcpServers"], "unreachable server is not mounted"
    assert all("${secret:" not in json.dumps(v) for v in spec.values())


def _snapshot(root: Path) -> dict[str, str]:
    """Files under ``root`` with the install dir replaced by ``<OUT>`` so the
    absolute ``skill://`` URI in the agent spec compares across machines."""

    return {
        p.relative_to(root).as_posix(): p.read_text().replace(root.as_posix(), "<OUT>")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_night_shift_matches_golden(tmp_path: Path, run_cli) -> None:
    out = tmp_path / "kiro"
    result = run_cli("install", str(EXAMPLE), "--target", "kiro", "--out", str(out))
    assert result.exit_code == 0, result.output
    emitted = _snapshot(out)

    if UPDATE_GOLDEN:
        if GOLDEN.exists():
            shutil.rmtree(GOLDEN)
        for rel, text in emitted.items():
            (GOLDEN / rel).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / rel).write_text(text)

    assert GOLDEN.is_dir(), (
        "regenerate with UPDATE_GOLDEN=1 uv run pytest tests/test_emitter_kiro.py"
    )
    assert emitted == {
        p.relative_to(GOLDEN).as_posix(): p.read_text()
        for p in sorted(GOLDEN.rglob("*"))
        if p.is_file()
    }
