"""Import a Claude Code layout into a crewfile pack.

The inverse of :mod:`crewspec.emitters.claude`, loosened to accept a
hand-made project as well as a crewspec-emitted plugin:

* agents: ``.claude/agents/*.md`` and ``.claude/skills/*/agents/*.md`` — the
  file stem is the agent key; frontmatter ``tools`` (comma string) becomes
  ``allowed_tools`` and ``skills`` becomes ``depends_on.skills``. The persona
  file is copied byte-for-byte so unknown frontmatter keys survive.
* skills: every directory under ``.claude/skills`` that holds a ``SKILL.md``
  (case-insensitive), copied whole.
* MCP servers: ``mcpServers`` from ``.mcp.json`` (project) and ``.claude.json``
  (user scope). When the layout was emitted by crewspec, the pack's
  ``crewspec.json`` state limits the import to the servers that pack owns.
* instructions: the pack's managed block in ``CLAUDE.md`` if present, else the
  whole file.

Secret values never reach the crewfile. A secret-looking env value or header
becomes ``${secret:NAME}`` (``NAME`` is the env var it referenced, else the key)
and each agent that can reach the server gets a ``secrets:`` entry with a
``vault://<pack>/NAME`` ref, so the result validates under 0.2.

What a Claude layout cannot give back: the original ``version``, pack-level
comments, ``depends_on`` edges the crewfile added on top of a skill's own
frontmatter, every 0.2 runtime block (Claude never received them), and the
exact secret *ref* (only the name survives, so the ref is regenerated).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from crewspec.emitters.base import STATE_FILE, PackState
from crewspec.models.crew import NAME_PATTERN
from crewspec.models.interpolate import EnvRef, SecretRef, parse_refs
from crewspec.parser.frontmatter import parse_skill_frontmatter, split_frontmatter

__all__ = ["ImportError_", "import_claude", "slugify"]

_NAME = re.compile(NAME_PATTERN)
_SKILL_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store")

#: Keys whose values are credentials whatever they look like.
_SECRET_KEY = re.compile(
    r"(TOKEN|SECRET|PASSW(OR)?D|API_?KEY|CREDENTIAL|AUTH|PRIVATE_KEY)", re.IGNORECASE
)
#: Values that are credentials whatever the key is called.
_SECRET_VALUE = re.compile(
    r"^(ghp_|github_pat_|gho_|xox[abprs]-|sk-|AKIA|glpat-)[A-Za-z0-9_\-]{8,}$"
    r"|^[A-Za-z0-9_\-]{40,}$"
)


class ImportError_(Exception):
    """The directory is not a Claude Code layout crewspec can import."""


@dataclass
class _Found:
    name: str
    agents: dict[str, Path] = field(default_factory=dict)
    skills: dict[str, Path] = field(default_factory=dict)
    mcps: dict[str, dict[str, object]] = field(default_factory=dict)
    instructions: str | None = None


def slugify(text: str) -> str:
    """Lower-case ``text`` into a valid pack name, or raise."""

    slug = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    if not _NAME.match(slug):
        raise ImportError_(f"cannot derive a pack name from {text!r}; pass --name")
    return slug


def import_claude(src: Path, out: Path, *, name: str | None = None) -> Path:
    """Write a crewfile pack for the Claude layout at ``src`` into ``out``.

    Returns the path of the written crewfile. ``out`` is created; an existing
    crewfile there is an error so an import never overwrites hand edits.
    """

    src = Path(src)
    if not src.is_dir():
        raise ImportError_(f"{src} is not a directory")
    found = _discover(src, name)
    if not found.agents and not found.skills:
        raise ImportError_(f"{src} holds no .claude/agents or .claude/skills")

    out = Path(out)
    crewfile = out / f"{found.name}.crewfile"
    if crewfile.exists() or (out / "crewspec.yaml").exists():
        raise ImportError_(f"{out} already holds a crewfile; refusing to overwrite")
    out.mkdir(parents=True, exist_ok=True)

    for agent_id, persona in found.agents.items():
        dest = out / "agents" / f"{agent_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(persona, dest)
    for skill, path in found.skills.items():
        shutil.copytree(path, out / "skills" / skill, ignore=_SKILL_COPY_IGNORE)
    if found.instructions is not None:
        (out / "instructions.md").write_text(found.instructions)

    doc = _crewdoc(found, out)
    header = "# Imported by `crewspec init --from-claude`; review before committing.\n"
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)
    crewfile.write_text(header + body)
    return crewfile


# ------------------------------------------------------------------ discovery


def _discover(src: Path, name: str | None) -> _Found:
    dot = src / ".claude"
    states = [
        PackState.read(p)
        for p in sorted(dot.glob(f"skills/*/.claude-plugin/{STATE_FILE}"))
    ]
    state = states[0] if len(states) == 1 and states[0] is not None else None
    pack = name or (state.name if state else slugify(src.resolve().name))
    if not _NAME.match(pack):
        raise ImportError_(f"{pack!r} is not a valid pack name ({NAME_PATTERN})")
    found = _Found(name=pack)

    for agents_dir in (dot / "agents", *sorted(dot.glob("skills/*/agents"))):
        for persona in sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []:
            found.agents.setdefault(persona.stem, persona)

    for skill_md in sorted(dot.glob("skills/**/*")):
        if skill_md.is_file() and skill_md.name.lower() == "skill.md":
            found.skills.setdefault(skill_md.parent.name, skill_md.parent)

    servers: dict[str, dict[str, object]] = {}
    for config in (src / ".claude.json", src / ".mcp.json"):
        if config.is_file():
            servers.update(_mcp_servers(config))
    if state is not None:
        servers = {k: v for k, v in servers.items() if k in state.mcps}
    found.mcps = servers

    for claude_md in (dot / "CLAUDE.md", src / "CLAUDE.md"):
        if claude_md.is_file():
            found.instructions = _instructions(claude_md.read_text(), pack)
            break
    return found


def _mcp_servers(config: Path) -> dict[str, dict[str, object]]:
    try:
        data = json.loads(config.read_text())
    except ValueError as exc:
        raise ImportError_(f"{config} is not valid JSON") from exc
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        raise ImportError_(f"{config}: mcpServers must be an object")
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def _instructions(text: str, pack: str) -> str:
    begin, end = f"<!-- crewspec:{pack}:begin -->", f"<!-- crewspec:{pack}:end -->"
    start = text.find(begin)
    stop = text.find(end, start + len(begin)) if start >= 0 else -1
    if start >= 0 and stop >= 0:
        return text[start + len(begin) : stop].strip("\n") + "\n"
    return text


# ------------------------------------------------------------------ crewfile


def _crewdoc(found: _Found, out: Path) -> dict[str, object]:
    mcps: dict[str, dict[str, object]] = {}
    secrets_by_mcp: dict[str, set[str]] = {}
    for mcp_name, raw in found.mcps.items():
        server, names = _server(mcp_name, raw)
        mcps[mcp_name] = server
        secrets_by_mcp[mcp_name] = names

    skill_mcps = {
        skill: set(parse_skill_frontmatter(_skill_md(out / "skills" / skill)).mcps)
        for skill in found.skills
    }

    agents: dict[str, dict[str, object]] = {}
    covered: set[str] = set()
    for agent_id, persona in found.agents.items():
        meta, _ = split_frontmatter(persona.read_text())
        meta = meta or {}
        agent: dict[str, object] = {"persona": f"agents/{agent_id}.md"}
        tools = _tools(meta.get("tools"))
        if tools:
            agent["allowed_tools"] = tools
        raw_skills = meta.get("skills")
        skills = [str(s) for s in raw_skills] if isinstance(raw_skills, list) else []
        if skills:
            agent["depends_on"] = {"skills": skills}
        reach = _reach(tools, skills, skill_mcps, set(mcps))
        declared = sorted({n for m in reach for n in secrets_by_mcp.get(m, ())})
        if declared:
            agent["secrets"] = {
                n: {"ref": f"vault://{found.name}/{n}", "blind": True} for n in declared
            }
            covered.update(declared)
        agents[agent_id] = agent

    for mcp_name, names in secrets_by_mcp.items():
        for orphan in sorted(names - covered):
            print(
                f"warning: mcps.{mcp_name} uses ${{secret:{orphan}}} but no agent "
                "reaches that server; declare it or drop the server before validate",
                file=sys.stderr,
            )

    doc: dict[str, object] = {"version": "0.2", "name": found.name}
    if found.instructions is not None:
        doc["instructions"] = "instructions.md"
    doc["agents"] = agents
    doc["skills"] = {s: {"path": f"skills/{s}"} for s in found.skills}
    if mcps:
        doc["mcps"] = mcps
    return doc


def _skill_md(skill_dir: Path) -> Path:
    for child in skill_dir.iterdir():
        if child.name.lower() == "skill.md":
            return child
    raise ImportError_(f"{skill_dir} has no SKILL.md")


def _tools(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


def _reach(
    tools: list[str],
    skills: list[str],
    skill_mcps: dict[str, set[str]],
    all_mcps: set[str],
) -> set[str]:
    """Mirror of ``crewspec.parser.reachable_mcps`` on raw data."""

    if not tools:
        return all_mcps
    via_tools = {
        t.split("__", 2)[1]
        for t in tools
        if t.startswith("mcp__") and t.count("__") >= 2
    }
    via_skills = {m for s in skills for m in skill_mcps.get(s, ())}
    return via_tools | via_skills


def _server(name: str, raw: dict[str, object]) -> tuple[dict[str, object], set[str]]:
    """One crewfile ``mcps`` entry plus the secret names it references."""

    secrets: set[str] = set()
    kind = raw.get("type") or ("stdio" if "command" in raw else "http")
    if kind == "stdio":
        server: dict[str, object] = {
            "type": "stdio",
            "command": str(raw.get("command", "")),
        }
        args = raw.get("args")
        if isinstance(args, list) and args:
            server["args"] = [str(a) for a in args]
        env = raw.get("env")
        if isinstance(env, dict) and env:
            server["env"] = {
                str(k): _secretise(name, str(k), str(v), secrets)
                for k, v in env.items()
            }
        return server, secrets
    if kind not in ("http", "sse"):
        raise ImportError_(f"mcpServers.{name}: unsupported type {kind!r}")
    server = {"type": kind, "url": str(raw.get("url", ""))}
    headers = raw.get("headers")
    if isinstance(headers, dict) and headers:
        server["headers"] = {
            str(k): _secretise(name, str(k), str(v), secrets)
            for k, v in headers.items()
        }
    return server, secrets


def _secretise(mcp: str, key: str, value: str, secrets: set[str]) -> str:
    """Return ``value`` with credentials replaced by ``${secret:NAME}`` refs.

    An env ref under a secret-ish key becomes a secret ref of the same name,
    which is exactly what the Claude emitter produced from ``${secret:NAME}``.
    A literal credential is dropped and named after the key; its value is
    never written anywhere.
    """

    fragments = parse_refs(value)
    refs = [f for f in fragments if not isinstance(f, str)]
    if refs:
        if not _SECRET_KEY.search(key):
            return value
        out: list[str] = []
        for fragment in fragments:
            if isinstance(fragment, EnvRef):
                secrets.add(fragment.name)
                out.append(f"${{secret:{fragment.name}}}")
            elif isinstance(fragment, SecretRef):
                secrets.add(fragment.target)
                out.append(f"${{secret:{fragment.target}}}")
            else:
                out.append(fragment)
        return "".join(out)
    if value and (_SECRET_KEY.search(key) or _SECRET_VALUE.match(value)):
        secret_name = re.sub(r"[^A-Z0-9_]", "_", key.upper())
        secrets.add(secret_name)
        print(
            f"warning: mcps.{mcp}.{key} held a literal credential; "
            f"replaced with ${{secret:{secret_name}}} (value not copied)",
            file=sys.stderr,
        )
        return f"${{secret:{secret_name}}}"
    return value
