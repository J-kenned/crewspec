"""Load a ``.crewfile`` / ``crewspec.yaml`` into the typed node graph.

The loader is the bridge from YAML on disk to a validated, in-memory graph the
resolver consumes. It validates the document against :class:`CrewDoc`, resolves
artifact pointers (personas, skill directories, the optional ``instructions``
file) relative to the crewfile's directory, merges each skill's
crewfile-declared deps with the deps declared in its ``SKILL.md`` frontmatter,
and verifies every ``depends_on`` reference resolves to a defined node.

Merge precedence is locked: **frontmatter is the floor**. A skill's merged deps
are the union of its frontmatter deps and its crewfile deps (frontmatter first,
deduplicated); the crewfile may add deps but cannot drop one the skill author
declared.

``crewspec.yaml`` is accepted by file name only: its composition keys
(``include``, ``crew``) are rejected here with a one-line error, before the
document reaches :class:`CrewDoc`, until composition lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import yaml
from pydantic import ValidationError

from crewspec.models.crew import CrewDoc
from crewspec.models.deps import SkillDeps
from crewspec.models.interpolate import SecretRef, parse_refs
from crewspec.models.mcp import McpServer, StdioMcpServer
from crewspec.models.skill import SkillDef, XCrewspecFrontmatter
from crewspec.parser.errors import (
    CrewfileSyntaxError,
    MissingArtifactError,
    UnknownReferenceError,
)
from crewspec.parser.frontmatter import parse_skill_frontmatter
from crewspec.parser.registry import (
    AgentNode,
    LoadedCrew,
    NodeRegistry,
    SkillNode,
)


def resolve_crewfile(path: str | Path) -> Path:
    """Resolve ``path`` to a concrete crewfile on disk.

    ``path`` may be a crewfile itself or a directory containing one. A directory
    prefers ``crewspec.yaml`` (the composition superset), falling back to a single
    ``*.crewfile``. Raises :class:`MissingArtifactError` when the path does not
    exist, a directory holds no crewfile, or it holds several ``*.crewfile`` with
    no ``crewspec.yaml`` to disambiguate.
    """

    target = Path(path)
    if target.is_file():
        return target
    if not target.is_dir():
        raise MissingArtifactError("path does not exist", path=target)

    canonical = target / "crewspec.yaml"
    if canonical.is_file():
        return canonical

    crewfiles = sorted(target.glob("*.crewfile"))
    if len(crewfiles) == 1:
        return crewfiles[0]
    if not crewfiles:
        raise MissingArtifactError(
            "directory contains no crewspec.yaml or *.crewfile", path=target
        )
    names = ", ".join(p.name for p in crewfiles)
    raise MissingArtifactError(
        f"directory contains multiple crewfiles ({names}); pass one explicitly",
        path=target,
    )


def load_crewfile(path: str | Path) -> LoadedCrew:
    """Load and validate the crewfile at ``path``.

    Accepts both a ``.crewfile`` and the ``crewspec.yaml`` superset. Raises a
    :class:`~crewspec.parser.errors.CrewfileError` subclass on any malformed
    document, missing artifact, bad frontmatter, or unknown dependency.
    """

    crewfile = Path(path)
    root = crewfile.parent

    doc = _parse_doc(crewfile)
    registry = NodeRegistry(
        name=doc.name,
        instructions=_load_instructions(doc, root, crewfile),
        agents=_load_agents(doc, root, crewfile),
        skills=_load_skills(doc, root, crewfile),
        mcps=dict(doc.mcps),
    )
    _validate_references(registry, crewfile)
    if doc.version == "0.2":
        _validate_secret_refs(doc, registry, crewfile)
    return LoadedCrew(doc=doc, registry=registry, root=root)


def _parse_doc(crewfile: Path) -> CrewDoc:
    try:
        text = crewfile.read_text()
    except FileNotFoundError as exc:
        raise CrewfileSyntaxError("crewfile does not exist", path=crewfile) from exc
    except OSError as exc:
        raise CrewfileSyntaxError(
            f"crewfile could not be read: {exc.strerror}", path=crewfile
        ) from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        line = exc.problem_mark.line + 1 if exc.problem_mark is not None else None
        raise CrewfileSyntaxError(
            f"invalid YAML: {exc.problem}", path=crewfile, line=line
        ) from exc
    except yaml.YAMLError as exc:
        raise CrewfileSyntaxError(f"invalid YAML: {exc}", path=crewfile) from exc

    if not isinstance(raw, dict):
        raise CrewfileSyntaxError("crewfile must be a YAML mapping", path=crewfile)

    for key in ("include", "crew"):
        if key in raw:
            raise CrewfileSyntaxError(
                f"`{key}` is not supported yet; see Not in V1 in the README",
                path=crewfile,
            )
    if "name" not in raw:
        raise CrewfileSyntaxError(
            "top-level `name` is required: lowercase letters, digits, and hyphens",
            path=crewfile,
        )
    _reject_agent_mcps(raw, crewfile)

    try:
        return CrewDoc.model_validate(raw)
    except ValidationError as exc:
        raise CrewfileSyntaxError(
            f"does not match the crewspec schema:\n{exc}", path=crewfile
        ) from exc


def _reject_agent_mcps(raw: dict[str, object], crewfile: Path) -> None:
    """Fail a legacy ``agents.<x>.depends_on.mcps`` with a migration hint.

    ``extra="forbid"`` would reject the key too, but with a generic pydantic
    message; this one says what changed and what to do.
    """

    agents = raw.get("agents")
    if not isinstance(agents, dict):
        return
    for name, agent in agents.items():
        deps = agent.get("depends_on") if isinstance(agent, dict) else None
        if isinstance(deps, dict) and "mcps" in deps:
            raise CrewfileSyntaxError(
                f"agents.{name}.depends_on.mcps is no longer supported: MCPs are "
                "global and every server under `mcps:` is installed; delete the key",
                path=crewfile,
            )


def _pack_path(root: Path, relative: str, crewfile: Path, what: str) -> Path:
    """Resolve an artifact path the crewfile names, keeping it inside the pack.

    Same rule as a ``github:`` subdir: a pack may only read its own files, so a
    fetched pack cannot pull an arbitrary local file into the plugin or into
    ``CLAUDE.md``. Absolute paths and ``..`` segments are rejected lexically;
    a symlink that leads outside the pack is caught by resolving both sides.
    Returns ``root / relative`` unresolved so reported paths stay readable.
    """

    target = Path(relative)
    if target.is_absolute() or ".." in target.parts:
        raise CrewfileSyntaxError(
            f"{what} '{relative}' must be a relative path inside the pack "
            "(no absolute path or '..')",
            path=crewfile,
        )
    path = root / target
    if not path.resolve().is_relative_to(root.resolve()):
        raise CrewfileSyntaxError(
            f"{what} '{relative}' resolves outside the pack", path=crewfile
        )
    return path


def _load_instructions(doc: CrewDoc, root: Path, crewfile: Path) -> Path | None:
    if doc.instructions is None:
        return None
    path = _pack_path(root, doc.instructions, crewfile, "instructions")
    if not path.is_file():
        raise MissingArtifactError(
            f"instructions file '{doc.instructions}' does not exist", path=crewfile
        )
    return path


def _load_agents(doc: CrewDoc, root: Path, crewfile: Path) -> dict[str, AgentNode]:
    agents: dict[str, AgentNode] = {}
    for name, agent in doc.agents.items():
        persona = _pack_path(root, agent.persona, crewfile, f"agent '{name}' persona")
        if not persona.is_file():
            raise MissingArtifactError(
                f"agent '{name}' persona file '{agent.persona}' does not exist",
                path=crewfile,
            )
        agents[name] = AgentNode(definition=agent, persona=persona)
    return agents


def _load_skills(doc: CrewDoc, root: Path, crewfile: Path) -> dict[str, SkillNode]:
    skills: dict[str, SkillNode] = {}
    for name, skill in doc.skills.items():
        skill_dir = _pack_path(root, skill.path, crewfile, f"skill '{name}' path")
        if not skill_dir.is_dir():
            raise MissingArtifactError(
                f"skill '{name}' directory '{skill.path}' does not exist",
                path=crewfile,
            )

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            raise MissingArtifactError(
                f"skill '{name}' is missing '{skill.path}/SKILL.md'",
                path=crewfile,
            )

        frontmatter = parse_skill_frontmatter(skill_md)
        skills[name] = SkillNode(
            definition=skill,
            root=skill_dir,
            skill_md=skill_md,
            deps=_merge_skill_deps(skill, frontmatter),
        )
    return skills


def _merge_skill_deps(skill: SkillDef, frontmatter: XCrewspecFrontmatter) -> SkillDeps:
    """Union of frontmatter and crewfile deps, frontmatter first, deduplicated.

    Frontmatter is the floor: a crewfile may add deps but cannot remove one the
    skill's author declared.
    """

    return SkillDeps(
        skills=_ordered_union(frontmatter.skills, skill.depends_on.skills),
        mcps=_ordered_union(frontmatter.mcps, skill.depends_on.mcps),
    )


def _ordered_union(*sources: Iterable[str]) -> list[str]:
    merged: dict[str, None] = {}
    for source in sources:
        for name in source:
            merged.setdefault(name, None)
    return list(merged)


def _validate_references(registry: NodeRegistry, crewfile: Path) -> None:
    """Ensure every ``depends_on`` entry names a node defined in the crew."""

    for name, agent in registry.agents.items():
        deps = agent.definition.depends_on
        _check(registry.skills, deps.skills, crewfile, "agents", name, "skills")

    for name, skill in registry.skills.items():
        _check(registry.skills, skill.deps.skills, crewfile, "skills", name, "skills")
        _check(registry.mcps, skill.deps.mcps, crewfile, "skills", name, "mcps")


def _validate_secret_refs(doc: CrewDoc, registry: NodeRegistry, crewfile: Path) -> None:
    """0.2: every ``${secret:NAME}`` an MCP server uses must be declared.

    Declared means it appears under ``secrets:`` of at least one agent, and of
    every agent that can reach the server: through an ``allowed_tools`` entry
    ``mcp__<server>__…``, through a directly depended-on skill whose merged
    deps list the server, or by inheriting every tool (empty ``allowed_tools``).
    Undeclared is a hole in blinding, so it fails at load.
    0.1 documents keep the passthrough behaviour and never reach here.
    """

    reach = {a: _reachable_mcps(n, registry) for a, n in registry.agents.items()}
    for mcp_name, server in doc.mcps.items():
        for secret in _secret_names(server):
            declared = {
                a
                for a, n in registry.agents.items()
                if secret in (n.definition.secrets or {})
            }
            if not declared:
                _secret_error(secret, mcp_name, "no agent declares it", crewfile)
            for agent, mcps in reach.items():
                if mcp_name in mcps and agent not in declared:
                    _secret_error(
                        secret,
                        mcp_name,
                        f"agent '{agent}' reaches the server but does not declare it",
                        crewfile,
                    )


def _reachable_mcps(node: AgentNode, registry: NodeRegistry) -> set[str]:
    if not node.definition.allowed_tools:
        return set(registry.mcps)  # inherit all: every server is in reach
    via_tools = {
        tool.split("__", 2)[1]
        for tool in node.definition.allowed_tools
        if tool.startswith("mcp__") and tool.count("__") >= 2
    }
    via_skills = {
        mcp
        for s in node.definition.depends_on.skills
        for mcp in registry.skills[s].deps.mcps
    }
    return via_tools | via_skills


def _secret_names(server: McpServer) -> list[str]:
    if isinstance(server, StdioMcpServer):
        values = [server.command, *server.args, *server.env.values()]
    else:
        values = [server.url, *server.headers.values()]
    names: dict[str, None] = {}
    for value in values:
        for ref in parse_refs(value):
            if isinstance(ref, SecretRef) and ref.kind == "secret":
                names.setdefault(ref.target, None)
    return list(names)


def _secret_error(secret: str, mcp: str, why: str, crewfile: Path) -> None:
    needle = f"${{secret:{secret}}}"
    # ponytail: reports the first line containing the needle, not the one under
    # the named mcp; walk the YAML node tree if it misleads.
    lines = crewfile.read_text().splitlines()
    line = next((i for i, text in enumerate(lines, 1) if needle in text), None)
    raise UnknownReferenceError(
        f"mcps.{mcp} uses {needle} but {why}; declare it under agents.<name>.secrets",
        path=crewfile,
        line=line,
    )


def _check(
    defined: Mapping[str, object],
    referenced: Iterable[str],
    crewfile: Path,
    kind: str,
    owner: str,
    edge: str,
) -> None:
    for ref in referenced:
        if ref not in defined:
            raise UnknownReferenceError(
                f"{kind}.{owner}.depends_on.{edge}[{ref}] is not defined",
                path=crewfile,
            )
