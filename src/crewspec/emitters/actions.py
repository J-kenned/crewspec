"""Emitter that renders a crewspec into GitHub Actions workflows.

The zero-infrastructure fallback for a label- or schedule-driven agent: each
agent with a ``github``, ``cron`` or ``webhook`` trigger becomes one workflow
under ``.github/workflows/<pack>-<agent>.yml`` whose single job runs
``anthropics/claude-code-action`` with the persona as the prompt. ``dest`` is
the repository root.

What the target can hold is translated, the rest is withheld and reported:

* ``triggers`` → ``on:`` (``issues: labeled`` filtered by ``filter.label``,
  ``pull_request`` types, ``schedule.cron``, ``repository_dispatch``); a
  ``channel`` trigger has no GitHub event and is dropped.
* ``allowed_tools`` → ``--allowedTools``; ``limits.deny`` → ``--disallowedTools``
  in the CLI's ``Bash(<glob>)`` rule syntax; ``limits.wall_clock_s`` →
  ``timeout-minutes``.
* ``secrets`` → job ``env`` entries by NAME only (``name: ${{ secrets.NAME }}``).
  ``github_token`` maps to the workflow's own ``GITHUB_TOKEN``; any other name
  starting ``GITHUB_`` is refused, since GitHub reserves that prefix.
* ``instructions`` → prepended to every workflow's prompt.
* ``mcps`` → ``.github/crewspec/<pack>/mcp.json`` passed with ``--mcp-config``.
* ``sandbox``, ``approvals``, ``channels``, token limits → withheld (a hosted
  runner is the sandbox; there is no approvals inbox to route to).

The pack directory ``.github/crewspec/<pack>/`` holds the state file and the
MCP config; the workflows it wrote are listed in the state so ``uninstall``
removes exactly those.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import yaml

from crewspec.emitters.base import Emitter, PackState
from crewspec.emitters.claude import _server_dict
from crewspec.emitters.errors import EmitError
from crewspec.emitters.kiro import reachable_mcps
from crewspec.ir import ResolvedAgent, ResolvedIR
from crewspec.models.runtime import Trigger

ACTION = "anthropics/claude-code-action@v1"

#: GitHub event types for a ``provider: github`` trigger ``type``.
_PR_TYPES = {
    "pr_opened": ("opened",),
    "pr_pushed": ("synchronize",),
    "pr_reopened": ("reopened",),
    "pr_review": ("review_requested",),
    "pr_change": ("opened", "synchronize", "reopened"),
}


class ActionsEmitter(Emitter):
    """Render the crewspec IR into GitHub Actions workflows under ``dest``."""

    name = "actions"
    plugins_dir = (".github", "crewspec")
    meta_dir = ".crewspec"
    workflows_dir = (".github", "workflows")
    dispositions = {
        "triggers": "translated",
        "limits": "translated",
        "secrets": "translated",
    }

    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        pack_root = self.output_path(dest, ir.name)
        self.previous_state(dest, ir.name)
        workflows = {
            self._workflow_name(ir.name, agent): self._workflow(ir, agent)
            for agent in ir.agents
            if _events(agent.triggers)[0]
        }
        if not workflows:
            raise EmitError(
                f"pack '{ir.name}' has no agent with a github, cron or webhook "
                "trigger; the actions target has nothing to run"
            )

        if pack_root.exists():
            shutil.rmtree(pack_root)
        pack_root.mkdir(parents=True)
        PackState(
            name=ir.name,
            mcps=tuple(m.name for m in ir.mcps),
            workflows=tuple(workflows),
        ).write(self.state_path(dest, ir.name))

        if ir.mcps:
            servers = {m.name: _server_dict(m.server) for m in ir.mcps}
            (pack_root / "mcp.json").write_text(
                json.dumps({"mcpServers": servers}, indent=2) + "\n"
            )
        wf_dir = dest.joinpath(*self.workflows_dir)
        wf_dir.mkdir(parents=True, exist_ok=True)
        for filename, document in workflows.items():
            (wf_dir / filename).write_text(
                yaml.safe_dump(
                    document, sort_keys=False, width=1000, allow_unicode=True
                )
            )

    def uninstall(self, pack: str, dest: Path) -> None:
        state = self.read_state(dest, pack)
        for filename in state.workflows:
            dest.joinpath(*self.workflows_dir, filename).unlink(missing_ok=True)
        shutil.rmtree(self.output_path(dest, pack))

    @staticmethod
    def _workflow_name(pack: str, agent: ResolvedAgent) -> str:
        return f"{pack}-{agent.name}.yml"

    def _workflow(self, ir: ResolvedIR, agent: ResolvedAgent) -> dict[str, object]:
        description, body = self.read_persona(agent)
        body = body.strip("\n")
        if ir.instructions is not None:
            body = ir.instructions.read_text().strip("\n") + "\n\n" + body
        on, condition = _events(agent.triggers)
        job: dict[str, object] = {
            "runs-on": "ubuntu-latest",
            "permissions": {
                "contents": "write",
                "issues": "write",
                "pull-requests": "write",
                "id-token": "write",
            },
        }
        if condition:
            job["if"] = condition
        if agent.limits is not None:
            job["timeout-minutes"] = max(1, math.ceil(agent.limits.wall_clock_s / 60))
        # The env key is the declared secret name so the ``${name}`` placeholders
        # in mcp.json resolve; the value comes from the repository secret store.
        env = {name: _secret_expr(agent.name, name) for name in agent.secrets}
        if env:
            job["env"] = env
        job["steps"] = [
            {"uses": "actions/checkout@v4"},
            {
                "name": agent.name,
                "uses": ACTION,
                "with": {
                    "anthropic_api_key": "${{ secrets.ANTHROPIC_API_KEY }}",
                    "github_token": "${{ secrets.GITHUB_TOKEN }}",
                    "prompt": _prompt(agent.triggers, body),
                    "claude_args": _claude_args(ir, agent),
                },
            },
        ]
        return {
            "name": f"{ir.name}: {agent.name}",
            "on": on,
            "concurrency": {
                "group": f"crewspec-{ir.name}-{agent.name}",
                "cancel-in-progress": False,
            },
            "jobs": {agent.name.replace("-", "_"): job},
        }


def _events(triggers: tuple[Trigger, ...]) -> tuple[dict[str, object], str | None]:
    """The ``on:`` block for ``triggers`` and the job ``if`` for label filters."""

    on: dict[str, object] = {}
    schedule: list[dict[str, str]] = []
    labels: list[str] = []
    for trigger in triggers:
        if trigger.provider == "cron" and trigger.cron:
            schedule.append({"cron": trigger.cron})
            on["schedule"] = schedule
        elif trigger.provider == "webhook":
            on["repository_dispatch"] = {"types": [trigger.type or "crewspec"]}
        elif trigger.provider == "github":
            if trigger.type in _PR_TYPES:
                on["pull_request"] = {"types": list(_PR_TYPES[trigger.type])}
            else:
                on["issues"] = {"types": ["labeled"]}
                label = trigger.filter.get("label")
                if label:
                    labels.append(label)
    condition = None
    if labels:
        checks = " || ".join(
            f"github.event.label.name == '{label}'" for label in labels
        )
        condition = f"github.event_name != 'issues' || {checks}"
    return on, condition


def _prompt(triggers: tuple[Trigger, ...], body: str) -> str:
    """The persona body, prefixed by the triggering event's identity."""

    lead = ""
    kinds = {t.provider for t in triggers}
    if "github" in kinds:
        lead = (
            "${{ github.event_name != 'schedule' && format("
            "'Triggered by GitHub event `{0}` on #{1}: {2}', github.event_name,"
            " github.event.issue.number || github.event.pull_request.number,"
            " github.event.issue.title || github.event.pull_request.title) || '' }}"
            "\n\n"
        )
    return lead + body.strip("\n") + "\n"


def _claude_args(ir: ResolvedIR, agent: ResolvedAgent) -> str:
    args: list[str] = []
    if agent.tools:
        args.append("--allowedTools " + _quote(",".join(agent.tools)))
    if agent.limits is not None and agent.limits.deny:
        rules = [_deny_rule(glob) for glob in agent.limits.deny]
        args.append("--disallowedTools " + _quote(",".join(rules)))
    if reachable_mcps(agent, ir.mcps):
        args.append(f"--mcp-config .github/crewspec/{ir.name}/mcp.json")
    return " ".join(args)


def _deny_rule(glob: str) -> str:
    """A crewfile deny glob in the CLI's rule syntax: command patterns become
    ``Bash(<glob>)``; a bare tool or ``mcp__…`` name is passed through."""

    if glob.startswith("mcp__") or (glob.isidentifier() and glob[0].isupper()):
        return glob
    return f"Bash({glob})"


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _secret_expr(agent: str, secret: str) -> str:
    """``${{ secrets.NAME }}`` for a declared secret.

    ``github_token`` is the one name every GitHub MCP server wants and maps to
    the workflow's own ``GITHUB_TOKEN``. GitHub refuses any other repository
    secret whose name starts ``GITHUB_``, so such a declaration is an error
    here rather than a workflow that fails at its first run.
    """

    name = secret.upper().replace("-", "_")
    if name == "GITHUB_TOKEN":
        return "${{ secrets.GITHUB_TOKEN }}"
    if name.startswith("GITHUB_"):
        raise EmitError(
            f"agent '{agent}' secret '{secret}' would become repository secret "
            f"'{name}'; GitHub reserves the GITHUB_ prefix. Rename it."
        )
    return f"${{{{ secrets.{name} }}}}"
