"""Emitter that renders a crewspec into kiro-cli agent specs.

kiro-cli reads one JSON file per agent from its agents directory and is handed
the agent by name (``--agent <name>``); Kiro Crew's own docs call this the
"native" path because nothing projects the spec, the harness opens the file
itself (``agent-spec-fields.md``, "Who reads a spec"). The target directory is
that agents root's parent, normally ``~/.kiro``:

* ``agents/<agent>.json`` — ``name``, ``description``, ``prompt`` (the persona
  body, inlined), ``tools`` (the crewfile grant list translated to kiro-cli
  names), ``allowedTools`` (only the read-only subset of those, so nothing that
  writes or runs is auto-approved past kiro-cli's own gate), ``mcpServers``
  (the servers the agent can reach), ``includeMcpJson: false`` so only those
  mount, ``resources`` with one ``skill://`` URI per declared skill plus a
  ``file://`` entry for the pack's ``instructions``, and ``model`` when
  ``x-operative.model`` names one.
* ``skills/<skill>/`` — each skill tree copied where kiro-cli's
  ``skill://~/.kiro/skills/*/SKILL.md`` glob finds it.
* ``crewspec/<pack>/`` — the pack directory holding the state file that lists
  the agent and skill files above, so ``uninstall`` removes exactly those.

kiro-cli validates specs with ``deny_unknown_fields`` and silently falls back to
the default agent on any key it does not know, so nothing crewspec-specific is
written into an agent file; the 0.2 runtime blocks are withheld and reported.
Secret references keep the other emitters' rule: ``${secret:NAME}`` becomes
``${NAME}`` for the host to expand, and no value is read.
"""

from __future__ import annotations

import json
import re
import shutil
import warnings
from pathlib import Path

from crewspec.emitters.base import Emitter, PackState
from crewspec.emitters.claude import (
    _SKILL_COPY_IGNORE,
    _normalize_skill_md,
    _server_dict,
)
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp

#: Claude Code tool names → kiro-cli built-in tool names. A name not listed is
#: passed through unchanged with a warning; kiro-cli rejects an unknown tool at
#: load, so the warning is the author's cue.
_TOOL_NAMES: dict[str, str] = {
    "Read": "fs_read",
    "Glob": "glob",
    "Grep": "grep",
    "LS": "fs_read",
    "Edit": "fs_write",
    "MultiEdit": "fs_write",
    "Write": "fs_write",
    "NotebookEdit": "fs_write",
    "Bash": "execute_bash",
    "WebFetch": "web_fetch",
    "WebSearch": "web_search",
}

#: kiro-cli tools that only read local state. These are the only ones
#: ``allowedTools`` auto-approves; everything else keeps kiro-cli's own
#: permission prompt. ``web_fetch``/``web_search`` are excluded: a URL query is
#: an egress channel, so they stay behind the gate.
_READ_ONLY = frozenset({"fs_read", "glob", "grep"})

#: kiro-cli model ids are dotted (``kiro-cli --list-models``, 2.34.0): the
#: Claude families plus ``gpt-5.6-*`` and ``auto``. A crewfile names its model in
#: pydantic-ai form (``anthropic:claude-sonnet-4-5-…``, ``bedrock:…``) or as a
#: Claude Code alias (``sonnet``); the family word picks the kiro id and
#: anything unrecognised falls back to ``auto`` so kiro-cli never rejects the spec.
_KIRO_MODEL_RE = re.compile(
    r"^(claude-(?:sonnet|haiku|opus)-\d+\.\d+|gpt-[\w.-]+|auto)$"
)
_KIRO_FAMILIES = (
    ("haiku", "claude-haiku-5.5"),
    ("opus", "claude-opus-5.5"),
    ("sonnet", "claude-sonnet-5.5"),
)


def kiro_model_id(model: str) -> str:
    """The kiro-cli model id for a crewfile ``x-operative.model`` value. Warns
    when the mapping changes what was asked for: a generation bump (the crewfile
    said 4.5, kiro offers 5.5) or a fallback to ``auto``."""

    name = model.strip().rpartition(":")[2].lower()
    if _KIRO_MODEL_RE.match(name):
        return name
    for family, kiro_id in _KIRO_FAMILIES:
        if family in name:
            asked = re.search(r"(\d+)[-.](\d+)", name)
            offered = kiro_id.rsplit("-", 1)[1]
            if asked and f"{asked.group(1)}.{asked.group(2)}" != offered:
                warnings.warn(
                    f"kiro: model {model!r} mapped to {kiro_id!r} (generation bumped)",
                    stacklevel=3,
                )
            return kiro_id
    if name != "auto":
        warnings.warn(
            f"kiro: model {model!r} has no kiro-cli id; using auto", stacklevel=3
        )
    return "auto"


class KiroEmitter(Emitter):
    """Render the crewspec IR into kiro-cli agent specs under ``dest``."""

    name = "kiro"
    plugins_dir = ("crewspec",)
    meta_dir = "."
    agents_dir = "agents"
    skills_dir = "skills"
    dispositions = {"x-operative": "translated: model only"}

    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        pack_root = self.output_path(dest, ir.name)
        self.previous_state(dest, ir.name)
        personas = [(agent, self.read_persona(agent)) for agent in ir.agents]

        if pack_root.exists():
            shutil.rmtree(pack_root)
        pack_root.mkdir(parents=True)
        (dest / self.agents_dir).mkdir(parents=True, exist_ok=True)

        PackState(
            name=ir.name,
            mcps=tuple(mcp.name for mcp in ir.mcps),
            agents=tuple(agent.name for agent in ir.agents),
            skills=tuple(skill.name for skill in ir.skills),
        ).write(self.state_path(dest, ir.name))

        for skill in ir.skills:
            target = dest / self.skills_dir / skill.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(skill.path, target, ignore=_SKILL_COPY_IGNORE)
            _normalize_skill_md(target)

        for agent, (description, body) in personas:
            spec = agent_spec(agent, description, body, ir, dest / self.skills_dir)
            path = dest / self.agents_dir / f"{agent.name}.json"
            path.write_text(json.dumps(spec, indent=2) + "\n")

    def uninstall(self, pack: str, dest: Path) -> None:
        state = self.read_state(dest, pack)
        for agent in state.agents:
            (dest / self.agents_dir / f"{agent}.json").unlink(missing_ok=True)
        for skill in state.skills:
            shutil.rmtree(dest / self.skills_dir / skill, ignore_errors=True)
        shutil.rmtree(self.output_path(dest, pack))


def agent_spec(
    agent: ResolvedAgent,
    description: str,
    body: str,
    ir: ResolvedIR,
    skills_root: Path | None = None,
) -> dict[str, object]:
    """One agent's kiro-cli agent json. ``skills_root`` is where the emitter copied
    the skills; ``None`` points ``resources`` at each skill's own ``SKILL.md`` in
    the pack, for a runtime that runs the pack in place."""

    tools = _translate_tools(agent)
    reachable = reachable_mcps(agent, ir.mcps)
    servers = {m.name: _server_dict(m.server) for m in ir.mcps if m.name in reachable}
    resources = [
        skill_uri(
            skill.skill_md
            if skills_root is None
            else skills_root / skill.name / "SKILL.md"
        )
        for skill in agent.skills
    ]
    if ir.instructions is not None:
        resources.append(f"file://{ir.instructions.resolve().as_posix()}")
    spec: dict[str, object] = {
        "name": agent.name,
        "description": description,
        "prompt": body.strip("\n") + "\n",
        "tools": tools,
        "allowedTools": [t for t in tools if t in _READ_ONLY],
        "mcpServers": servers,
        "includeMcpJson": False,
        "resources": resources,
    }
    model = agent.extensions.get("x-operative", {}).get("model")
    if isinstance(model, str) and model:
        spec["model"] = kiro_model_id(model)
    return spec


def reachable_mcps(agent: ResolvedAgent, mcps: tuple[ResolvedMcp, ...]) -> set[str]:
    """The loader's reach rule over the IR: no grants means every server, else the
    servers named by ``mcp__<server>__…`` grants plus those the agent's skills
    declare in ``x-crewspec.mcps``."""

    if not agent.tools:
        return {m.name for m in mcps}
    via_tools = {
        tool.split("__", 2)[1]
        for tool in agent.tools
        if tool.startswith("mcp__") and tool.count("__") >= 2
    }
    via_skills = {name for skill in agent.skills for name in skill.mcps}
    return via_tools | via_skills


def _translate_tools(agent: ResolvedAgent) -> list[str]:
    """kiro-cli names for the agent's grants; empty grants mean every tool."""

    if not agent.tools:
        return ["*"]
    out: list[str] = []
    for tool in agent.tools:
        if tool.startswith("mcp__"):
            _, server, *rest = tool.split("__", 2)
            suffix = rest[0] if rest else "*"
            name = f"@{server}" if suffix == "*" else f"@{server}/{suffix}"
        elif tool in _TOOL_NAMES:
            name = _TOOL_NAMES[tool]
        else:
            warnings.warn(
                f"agent '{agent.name}' tool '{tool}' has no kiro-cli name; "
                "written unchanged",
                stacklevel=3,
            )
            name = tool
        if name not in out:
            out.append(name)
    return out


def skill_uri(skill_md: Path) -> str:
    """kiro-cli's ``skill://`` form: ``~/`` under the home directory, else absolute."""

    try:
        return f"skill://~/{skill_md.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return f"skill://{skill_md.as_posix()}"
