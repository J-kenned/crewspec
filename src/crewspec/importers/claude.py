"""Import a Claude Code layout into a crewfile pack.

The inverse of :mod:`crewspec.emitters.claude`, loosened to accept a
hand-made project as well as a crewspec-emitted plugin:

* agents: ``.claude/agents/*.md`` and ``.claude/skills/*/agents/*.md``. The
  agent key is the slugified frontmatter ``name`` when present, else the
  slugified file stem (``^[a-z0-9][a-z0-9-]*$``). Frontmatter ``tools`` (comma
  string or list) becomes ``allowed_tools`` and ``skills`` becomes
  ``depends_on.skills``. The persona file is copied byte-for-byte so unknown
  frontmatter keys survive.
* skills: every directory under ``.claude/skills`` that holds a ``SKILL.md``
  (case-insensitive) with a frontmatter fence, copied whole. ``node_modules``,
  hidden directories, and fence-less ``SKILL.md`` files are skipped with a
  warning, since the loader would reject them.
* MCP servers: ``mcpServers`` from ``.mcp.json`` (project) and ``.claude.json``
  (user scope). When the layout was emitted by crewspec, the pack's
  ``crewspec.json`` state limits the import to the servers that pack owns.
* instructions: the pack's managed block in ``CLAUDE.md`` if present, else the
  whole file.

Secret values never reach the crewfile. Every server position the loader
treats as secret-bearing (``command``, ``args``, ``env`` values, ``url``,
header values) is scanned. A pre-existing ``${secret:NAME}`` is kept and
declared. A ``${VAR}`` env reference becomes ``${secret:VAR}`` under a
credential key (``*TOKEN*``, ``*SECRET*``, ``*KEY*``, ...) or in any header,
and stays an env reference elsewhere. A literal that looks like a credential
(a known token prefix, a 40+ character mixed-case-and-digit run, a bearer
header, or any literal under a credential key) is replaced wholesale by
``${secret:<SLOT>}`` named after its key or position, with a warning, and the
value is written nowhere. Each agent that can reach the server gets a
``secrets:`` entry with a ``vault://<pack>/NAME`` ref so the pack validates
under 0.2. A server whose secrets no agent reaches is dropped with a warning,
because 0.2 refuses an undeclared secret and the server is unreachable anyway.

What a Claude layout cannot give back: the original ``version``, pack-level
comments and key order, ``depends_on`` edges the crewfile added on top of a
skill's own frontmatter, every 0.2 runtime block (Claude never received them),
the exact secret *ref* (only the name survives, so the ref is regenerated), and
an agent key that differed from both the frontmatter name and the file stem.
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
from crewspec.models.interpolate import (
    EnvRef,
    InterpolationError,
    SecretRef,
    parse_refs,
)
from crewspec.parser.frontmatter import parse_skill_frontmatter, split_frontmatter

__all__ = ["ImportError_", "import_claude", "slugify"]

_NAME = re.compile(NAME_PATTERN)
_SKILL_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store")
#: Directories never scanned for skills: dependency trees and dotfiles.
_SKIP_DIRS = {"node_modules"}

#: Keys whose values are credentials whatever they look like.
_SECRET_KEY = re.compile(
    r"(TOKEN|SECRET|PASSW(OR)?D|API_?KEY|CREDENTIAL|AUTH|PRIVATE_KEY)", re.IGNORECASE
)
#: Well-known credential prefixes, anywhere in a literal.
_KNOWN_PREFIX = re.compile(
    r"(ghp_|github_pat_|gho_|ghs_|xox[abprs]-|sk-|AKIA|glpat-)[A-Za-z0-9_\-]{8,}"
)
#: A long opaque run; it is a credential only with mixed case and a digit.
_LONG_RUN = re.compile(r"[A-Za-z0-9_\-]{40,}")
#: A header carrying a bearer/basic/token scheme is a credential.
_SCHEME = re.compile(r"^\s*(Bearer|Basic|Token)\s+\S", re.IGNORECASE)


class ImportError_(Exception):
    """The directory is not a Claude Code layout crewspec can import."""


@dataclass
class _Found:
    name: str
    agents: dict[str, Path] = field(default_factory=dict)
    skills: dict[str, Path] = field(default_factory=dict)
    mcps: dict[str, dict[str, object]] = field(default_factory=dict)
    mcp_sources: dict[str, Path] = field(default_factory=dict)
    instructions: str | None = None


def slugify(text: str) -> str:
    """Lower-case ``text`` into a valid pack or agent name, or raise."""

    slug = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    if not _NAME.match(slug):
        raise ImportError_(f"cannot derive a name from {text!r}; pass --name")
    return slug


def import_claude(
    src: Path, out: Path | None = None, *, name: str | None = None
) -> Path:
    """Write a crewfile pack for the Claude layout at ``src`` into ``out``.

    ``out`` defaults to ``<src parent>/<name>-crew``, never the working
    directory. ``out`` may not sit inside ``src`` nor contain it: the pack
    copies skills out of ``src``, and either nesting would copy the pack into
    itself or let a later emit overwrite the source. Returns the path of the
    written crewfile; an existing crewfile in ``out`` is an error so an import
    never overwrites hand edits.
    """

    src = Path(src).resolve()
    if not src.is_dir():
        raise ImportError_(f"{src} is not a directory")
    found = _discover(src, name)
    if not found.agents and not found.skills:
        raise ImportError_(f"{src} holds no .claude/agents or .claude/skills")

    out = (src.parent / f"{found.name}-crew") if out is None else Path(out).resolve()
    if out == src or out.is_relative_to(src):
        raise ImportError_(f"--out {out} is inside the source {src}; pick a sibling")
    if src.is_relative_to(out):
        raise ImportError_(f"source {src} is inside --out {out}; pick a sibling")
    crewfile = out / f"{found.name}.crewfile"
    if crewfile.exists() or (out / "crewspec.yaml").exists():
        raise ImportError_(f"{out} already holds a crewfile; refusing to overwrite")

    doc = _crewdoc(found)  # may raise before anything is written
    out.mkdir(parents=True, exist_ok=True)
    for agent_id, persona in found.agents.items():
        dest = out / "agents" / f"{agent_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(persona, dest)
    for skill, path in found.skills.items():
        shutil.copytree(path, out / "skills" / skill, ignore=_SKILL_COPY_IGNORE)
    if found.instructions is not None:
        (out / "instructions.md").write_text(found.instructions)

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
    pack = name or (state.name if state else slugify(src.name))
    if not _NAME.match(pack):
        raise ImportError_(f"{pack!r} is not a valid pack name ({NAME_PATTERN})")
    found = _Found(name=pack)

    for agents_dir in (dot / "agents", *sorted(dot.glob("skills/*/agents"))):
        if not agents_dir.is_dir():
            continue
        for persona in sorted(agents_dir.glob("*.md")):
            if persona.name.startswith("."):
                continue
            found.agents.setdefault(_agent_key(persona), persona)

    skills_root = dot / "skills"
    for skill_md in sorted(skills_root.glob("**/*")):
        if not (skill_md.is_file() and skill_md.name.lower() == "skill.md"):
            continue
        rel = skill_md.parent.relative_to(skills_root).parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel):
            continue
        meta, _ = split_frontmatter(skill_md.read_text())
        if meta is None:
            _warn(f"skipped {skill_md}: SKILL.md has no frontmatter fence")
            continue
        found.skills.setdefault(skill_md.parent.name, skill_md.parent)

    for config in (src / ".claude.json", src / ".mcp.json"):
        if config.is_file():
            for key, raw in _mcp_servers(config).items():
                found.mcps[key] = raw
                found.mcp_sources[key] = config
    if state is not None:
        found.mcps = {k: v for k, v in found.mcps.items() if k in state.mcps}

    for claude_md in (dot / "CLAUDE.md", src / "CLAUDE.md"):
        if claude_md.is_file():
            found.instructions = _instructions(claude_md.read_text(), pack)
            break
    return found


def _agent_key(persona: Path) -> str:
    """Slugified frontmatter ``name`` when present, else the slugified stem."""

    meta, _ = split_frontmatter(persona.read_text())
    raw = (meta or {}).get("name")
    return slugify(raw if isinstance(raw, str) and raw.strip() else persona.stem)


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


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


# ------------------------------------------------------------------ crewfile


def _crewdoc(found: _Found) -> dict[str, object]:
    mcps: dict[str, dict[str, object]] = {}
    secrets_by_mcp: dict[str, set[str]] = {}
    for mcp_name, raw in found.mcps.items():
        server, names = _server(mcp_name, raw, found.mcp_sources[mcp_name])
        mcps[mcp_name] = server
        secrets_by_mcp[mcp_name] = names

    skill_mcps = {
        skill: set(parse_skill_frontmatter(_skill_md(path)).mcps)
        for skill, path in found.skills.items()
    }

    agents: dict[str, dict[str, object]] = {}
    reached: set[str] = set()
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
        reached |= reach
        declared = sorted({n for m in reach for n in secrets_by_mcp.get(m, ())})
        if declared:
            agent["secrets"] = {
                n: {"ref": f"vault://{found.name}/{n}", "blind": True} for n in declared
            }
        agents[agent_id] = agent

    # 0.2 refuses a secret no agent declares, and a server no agent reaches is
    # dead config; drop it so `init` → `validate` succeeds, and say so.
    for mcp_name in list(mcps):
        if secrets_by_mcp[mcp_name] and mcp_name not in reached:
            listed = ", ".join(sorted(secrets_by_mcp[mcp_name]))
            _warn(
                f"dropped mcps.{mcp_name}: it uses ${{secret:...}} ({listed}) but no "
                f"agent reaches it; grant an agent mcp__{mcp_name}__* and re-run"
            )
            del mcps[mcp_name]

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


def _server(
    name: str, raw: dict[str, object], config: Path
) -> tuple[dict[str, object], set[str]]:
    """One crewfile ``mcps`` entry plus the secret names it references.

    Every position the loader scans for secrets is scanned here too.
    """

    secrets: set[str] = set()
    scrub = _Scrubber(name, config, secrets)
    kind = raw.get("type") or ("stdio" if "command" in raw else "http")
    if kind == "stdio":
        server: dict[str, object] = {
            "type": "stdio",
            "command": scrub.positional("command", str(raw.get("command", ""))),
        }
        args = raw.get("args")
        if isinstance(args, list) and args:
            server["args"] = [
                scrub.positional(f"ARG{i}", str(a)) for i, a in enumerate(args)
            ]
        env = raw.get("env")
        if isinstance(env, dict) and env:
            server["env"] = {
                str(k): scrub.keyed(str(k), str(v), env_secret=_is_secret_key(k))
                for k, v in env.items()
            }
        return server, secrets
    if kind not in ("http", "sse"):
        raise ImportError_(f"{config}: mcpServers.{name}: unsupported type {kind!r}")
    server = {"type": kind, "url": scrub.positional("url", str(raw.get("url", "")))}
    headers = raw.get("headers")
    if isinstance(headers, dict) and headers:
        # A header env ref is always a credential; a header literal only when
        # it looks like one or the key says so (Authorization, X-Api-Key, ...).
        server["headers"] = {
            str(k): scrub.keyed(str(k), str(v), env_secret=True)
            for k, v in headers.items()
        }
    return server, secrets


def _is_secret_key(key: object) -> bool:
    return bool(_SECRET_KEY.search(str(key)))


class _Scrubber:
    """Replace credentials in one server's values with ``${secret:NAME}`` refs.

    * ``${secret:NAME}`` is kept and declared, in every slot.
    * ``${VAR}`` becomes ``${secret:VAR}`` where ``env_secret`` holds (a
      credential key, or any header) and stays an env reference elsewhere.
    * A literal is replaced wholesale when it looks like a credential or sits
      under a credential key; the value is never written anywhere. Positional
      slots (command, args, url) prefix the generated name with the server
      name, since ``ARG3`` alone would not identify it.
    """

    def __init__(self, mcp: str, config: Path, secrets: set[str]) -> None:
        self.mcp, self.config, self.secrets = mcp, config, secrets

    def keyed(self, key: str, value: str, *, env_secret: bool) -> str:
        return self._scrub(key, value, env_secret, _is_secret_key(key), key)

    def positional(self, slot: str, value: str) -> str:
        return self._scrub(slot, value, False, False, f"{self.mcp}_{slot}")

    def _scrub(
        self, slot: str, value: str, env_secret: bool, literal_secret: bool, name: str
    ) -> str:
        try:
            fragments = parse_refs(value)
        except InterpolationError as exc:
            raise ImportError_(
                f"{self.config}:{_line_of(self.config, value)}: "
                f"mcpServers.{self.mcp}.{slot}: {exc}"
            ) from exc

        if any(not isinstance(f, str) for f in fragments):
            out: list[str] = []
            for fragment in fragments:
                if isinstance(fragment, SecretRef):
                    self.secrets.add(fragment.target)
                    out.append(f"${{{fragment.kind}:{fragment.target}}}")
                elif isinstance(fragment, EnvRef) and env_secret:
                    self.secrets.add(fragment.name)
                    out.append(f"${{secret:{fragment.name}}}")
                elif isinstance(fragment, EnvRef) and fragment.default is not None:
                    out.append(f"${{{fragment.name}:-{fragment.default}}}")
                elif isinstance(fragment, EnvRef):
                    out.append(f"${{{fragment.name}}}")
                else:
                    out.append(fragment)
            return "".join(out)

        if value and (literal_secret or _looks_secret(value)):
            secret_name = _upper(name)
            self.secrets.add(secret_name)
            _warn(
                f"mcpServers.{self.mcp}.{slot} held a literal credential; replaced "
                f"with ${{secret:{secret_name}}} (value not copied)"
            )
            return f"${{secret:{secret_name}}}"
        return value


def _upper(text: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", text.upper()).strip("_")


def _looks_secret(value: str) -> bool:
    if _SCHEME.match(value) or _KNOWN_PREFIX.search(value):
        return True
    return any(
        any(c.isupper() for c in run)
        and any(c.islower() for c in run)
        and any(c.isdigit() for c in run)
        for run in _LONG_RUN.findall(value)
    )


def _line_of(config: Path, needle: str) -> int | str:
    try:
        lines = config.read_text().splitlines()
    except OSError:
        return "?"
    escaped = json.dumps(needle)[1:-1]
    return next((i for i, t in enumerate(lines, 1) if escaped in t), "?")
