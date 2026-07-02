"""Load a ``.crewfile`` / ``crewspec.yaml`` into the typed node graph.

The loader is the bridge from YAML on disk to a validated, in-memory graph the
resolver consumes. It validates the document against :class:`CrewDoc`, resolves
artifact pointers relative to the crewfile's directory, merges each skill's
crewfile-declared deps with the deps declared in its ``SKILL.md`` frontmatter,
and verifies every ``depends_on`` reference resolves to a defined node.

Merge precedence is locked: **frontmatter is the floor**. A skill's merged deps
are the union of its frontmatter deps and its crewfile deps (frontmatter first,
deduplicated); the crewfile may add deps but cannot drop one the skill author
declared.

``crewspec.yaml``'s ``include`` is parsed onto :attr:`CrewDoc.include`
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import yaml
from pydantic import ValidationError

from crewspec.models.crew import CrewDoc
from crewspec.models.deps import SkillDeps
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

#: Skill subdirectories the emitter copies verbatim when present.
_SKILL_EXTRA_DIRS = ("references", "scripts", "assets")


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
        agents=_load_agents(doc, root, crewfile),
        skills=_load_skills(doc, root, crewfile),
        mcps=dict(doc.mcps),
    )
    _validate_references(registry, crewfile)
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

    try:
        return CrewDoc.model_validate(raw)
    except ValidationError as exc:
        raise CrewfileSyntaxError(
            f"does not match the crewspec schema:\n{exc}", path=crewfile
        ) from exc


def _load_agents(doc: CrewDoc, root: Path, crewfile: Path) -> dict[str, AgentNode]:
    agents: dict[str, AgentNode] = {}
    for name, agent in doc.agents.items():
        persona = root / agent.persona
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
        skill_dir = root / skill.path
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
            extra_dirs=tuple(
                skill_dir / sub
                for sub in _SKILL_EXTRA_DIRS
                if (skill_dir / sub).is_dir()
            ),
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
        _check(registry.mcps, deps.mcps, crewfile, "agents", name, "mcps")
        _check(registry.agents, deps.agents, crewfile, "agents", name, "agents")

    for name, skill in registry.skills.items():
        _check(registry.skills, skill.deps.skills, crewfile, "skills", name, "skills")
        _check(registry.mcps, skill.deps.mcps, crewfile, "skills", name, "mcps")


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
