"""Tests for the GitHub Actions emitter: triggers to workflows, plus a golden tree."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from crewspec.emitters import ActionsEmitter, EmitError
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp
from crewspec.models import StdioMcpServer
from crewspec.models.runtime import Limits, Secret, Trigger

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "night-shift"
HTTP_FIXTURE = Path(__file__).parent / "fixtures" / "http-crew"
GOLDEN = REPO_ROOT / "tests" / "golden" / "actions"
UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"
PACK = "pack"


def _persona(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "src" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {name} agent\n---\n\n# {name}\n\nDo it.\n"
    )
    return path


def _agent(tmp_path: Path, name: str, **kwargs: object) -> ResolvedAgent:
    return ResolvedAgent(name=name, persona_path=_persona(tmp_path, name), **kwargs)  # type: ignore[arg-type]


def _emit(tmp_path: Path, ir: ResolvedIR) -> Path:
    dest = tmp_path / "repo"
    ActionsEmitter().emit(ir, dest)
    return dest


def _workflow(dest: Path, pack: str, agent: str) -> dict[str, object]:
    return yaml.safe_load(
        (dest / ".github" / "workflows" / f"{pack}-{agent}.yml").read_text()
    )


def test_label_and_cron_triggers_become_events_with_a_label_guard(
    tmp_path: Path,
) -> None:
    agent = _agent(
        tmp_path,
        "ns",
        tools=("Read", "Bash", "mcp__gh__*"),
        triggers=(
            Trigger(
                provider="github",
                type="label_change",
                filter={"repo": "o/r", "label": "night"},
            ),
            Trigger(provider="cron", cron="0 1 * * *"),
        ),
        limits=Limits(
            tokens_per_run=1,
            wall_clock_s=3601,
            deny=["gh pr merge*", "mcp__gh__merge_pull_request", "Bash"],
        ),
        secrets={
            "github_token": Secret(ref="vault://g", hosts=["api.github.com"]),
            "slack_bot": Secret(ref="vault://s", blind=False),
        },
    )
    gh = ResolvedMcp(
        name="gh",
        server=StdioMcpServer(
            type="stdio",
            command="npx",
            args=["-y", "gh"],
            env={"TOKEN": "${secret:github_token}"},
        ),
    )
    dest = _emit(tmp_path, ResolvedIR(name=PACK, agents=(agent,), mcps=(gh,)))

    wf = _workflow(dest, PACK, "ns")
    job = wf["jobs"]["ns"]
    assert wf["on"] == {
        "issues": {"types": ["labeled"]},
        "schedule": [{"cron": "0 1 * * *"}],
    }
    assert (
        job["if"]
        == "github.event_name != 'issues' || github.event.label.name == 'night'"
    )
    assert job["timeout-minutes"] == 61
    # Secrets by name only; github_token is the workflow's own token.
    assert job["env"] == {
        "github_token": "${{ secrets.GITHUB_TOKEN }}",
        "slack_bot": "${{ secrets.SLACK_BOT }}",
    }
    step = job["steps"][1]
    assert step["uses"] == "anthropics/claude-code-action@v1"
    # The event lead is an expression that renders empty on a schedule run.
    assert step["with"]["prompt"].startswith(
        "${{ github.event_name != 'schedule' && format("
    )
    assert "# ns\n\nDo it." in step["with"]["prompt"]
    args = step["with"]["claude_args"]
    assert '--allowedTools "Read,Bash,mcp__gh__*"' in args
    assert (
        '--disallowedTools "Bash(gh pr merge*),mcp__gh__merge_pull_request,Bash"'
        in args
    )
    assert f"--mcp-config .github/crewspec/{PACK}/mcp.json" in args
    mcp = json.loads((dest / ".github" / "crewspec" / PACK / "mcp.json").read_text())
    assert mcp["mcpServers"]["gh"] == {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "gh"],
        "env": {"TOKEN": "${github_token}"},
    }
    assert (
        "vault://"
        not in (dest / ".github" / "workflows" / f"{PACK}-ns.yml").read_text()
    )


def test_pr_and_webhook_triggers(tmp_path: Path) -> None:
    agent = _agent(
        tmp_path,
        "chk",
        triggers=(
            Trigger(provider="github", type="pr_change", filter={"repo": "o/r"}),
            Trigger(provider="webhook", type="nudge"),
            Trigger(provider="channel"),
        ),
    )
    wf = _workflow(_emit(tmp_path, ResolvedIR(name=PACK, agents=(agent,))), PACK, "chk")

    assert wf["on"] == {
        "pull_request": {"types": ["opened", "synchronize", "reopened"]},
        "repository_dispatch": {"types": ["nudge"]},
    }
    assert "if" not in wf["jobs"]["chk"]
    assert "--mcp-config" not in wf["jobs"]["chk"]["steps"][1]["with"]["claude_args"]


def test_agent_without_runnable_trigger_is_skipped_and_empty_pack_errors(
    tmp_path: Path,
) -> None:
    runnable = _agent(
        tmp_path, "a", triggers=(Trigger(provider="cron", cron="* * * * *"),)
    )
    idle = _agent(tmp_path, "b")
    dest = _emit(tmp_path, ResolvedIR(name=PACK, agents=(runnable, idle)))
    assert (dest / ".github" / "workflows" / f"{PACK}-a.yml").exists()
    assert not (dest / ".github" / "workflows" / f"{PACK}-b.yml").exists()

    with pytest.raises(EmitError, match="nothing to run"):
        ActionsEmitter().emit(
            ResolvedIR(name="idle", agents=(idle,)), tmp_path / "other"
        )


def test_uninstall_removes_only_its_workflows(tmp_path: Path) -> None:
    agent = _agent(
        tmp_path, "a", triggers=(Trigger(provider="cron", cron="* * * * *"),)
    )
    dest = _emit(tmp_path, ResolvedIR(name=PACK, agents=(agent,)))
    (dest / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")

    ActionsEmitter().uninstall(PACK, dest)

    assert not (dest / ".github" / "workflows" / f"{PACK}-a.yml").exists()
    assert (dest / ".github" / "workflows" / "ci.yml").exists()
    assert not (dest / ".github" / "crewspec" / PACK).exists()


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize(
    ("source", "name"), [(EXAMPLE, "night-shift"), (HTTP_FIXTURE, "http-crew")]
)
def test_pack_matches_golden(tmp_path: Path, run_cli, source: Path, name: str) -> None:
    out = tmp_path / "repo"
    result = run_cli("install", str(source), "--target", "actions", "--out", str(out))
    assert result.exit_code == 0, result.output
    emitted = _snapshot(out)
    golden = GOLDEN / name

    if UPDATE_GOLDEN:
        if golden.exists():
            shutil.rmtree(golden)
        for rel, text in emitted.items():
            (golden / rel).parent.mkdir(parents=True, exist_ok=True)
            (golden / rel).write_text(text)

    assert golden.is_dir(), (
        "regenerate with UPDATE_GOLDEN=1 uv run pytest tests/test_emitter_actions.py"
    )
    assert emitted == _snapshot(golden)


def test_http_server_and_instructions_in_golden() -> None:
    golden = GOLDEN / "http-crew"
    mcp = json.loads(
        (golden / ".github" / "crewspec" / "http-crew" / "mcp.json").read_text()
    )
    assert mcp["mcpServers"]["hooks"] == {
        "type": "http",
        "url": "https://hooks.example.com/mcp",
        "headers": {"Authorization": "Bearer ${hooks_token}"},
    }
    wf = yaml.safe_load(
        (golden / ".github" / "workflows" / "http-crew-notifier.yml").read_text()
    )
    prompt = wf["jobs"]["notifier"]["steps"][1]["with"]["prompt"]
    assert prompt.startswith("Always report in one paragraph.\n\n# Notifier")
    assert wf["jobs"]["notifier"]["timeout-minutes"] == 2
