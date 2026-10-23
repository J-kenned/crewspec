"""Tests for the crewfile parser and artifact loader."""

from pathlib import Path

import pytest

from crewspec.models import StdioMcpServer
from crewspec.parser import (
    CrewfileSyntaxError,
    FrontmatterError,
    MissingArtifactError,
    UnknownReferenceError,
    load_crewfile,
)

HAPPY = Path(__file__).parent / "fixtures" / "happy" / "team.crewfile"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _skill_md(skills: str = "", mcps: str = "") -> str:
    block = ""
    if skills or mcps:
        block = "x-crewspec:\n"
        if skills:
            block += f"  skills: [{skills}]\n"
        if mcps:
            block += f"  mcps: [{mcps}]\n"
    return f"---\nname: s\ndescription: A skill\n{block}---\n\n# Skill\n"


def _build_min_crew(tmp_path: Path) -> Path:
    """A minimal valid crew: one agent, one skill, one mcp. Returns the path."""

    _write(tmp_path / "agents" / "a.md", "# Agent\n")
    _write(tmp_path / "skills" / "s" / "SKILL.md", _skill_md())
    return _write(
        tmp_path / "crew.crewfile",
        """
version: "0.1"
name: mini
agents:
  a:
    persona: agents/a.md
skills:
  s:
    path: skills/s
mcps:
  m:
    type: stdio
    command: serve
""",
    )


def test_happy_path_loads_registry() -> None:
    crew = load_crewfile(HAPPY)

    assert crew.root == HAPPY.parent
    assert crew.registry.name == "team"
    assert crew.registry.instructions is None
    assert set(crew.registry.agents) == {"reviewer"}
    assert set(crew.registry.skills) == {"lint", "format"}
    assert set(crew.registry.mcps) == {"github"}
    assert isinstance(crew.registry.mcps["github"], StdioMcpServer)


def test_happy_path_resolves_artifact_paths() -> None:
    crew = load_crewfile(HAPPY)

    assert (
        crew.registry.agents["reviewer"].persona == HAPPY.parent / "agents/reviewer.md"
    )
    lint = crew.registry.skills["lint"]
    assert lint.root == HAPPY.parent / "skills/lint"
    assert lint.skill_md == HAPPY.parent / "skills/lint/SKILL.md"


def test_missing_name_is_a_one_line_error(tmp_path: Path) -> None:
    crewfile = _write(tmp_path / "crew.crewfile", 'version: "0.1"\n')
    with pytest.raises(CrewfileSyntaxError) as exc:
        load_crewfile(crewfile)
    assert str(exc.value) == (
        f"{crewfile}: top-level `name` is required: lowercase letters, digits, "
        "and hyphens"
    )


def test_legacy_agent_mcps_errors_with_migration_hint(tmp_path: Path) -> None:
    _write(tmp_path / "agents" / "a.md", "# Agent\n")
    crewfile = _write(
        tmp_path / "crew.crewfile",
        """
version: "0.1"
name: legacy
agents:
  a:
    persona: agents/a.md
    depends_on:
      mcps: [github]
mcps:
  github: {type: stdio, command: gh}
""",
    )
    with pytest.raises(CrewfileSyntaxError) as exc:
        load_crewfile(crewfile)
    message = str(exc.value)
    assert "agents.a.depends_on.mcps is no longer supported" in message
    assert "MCPs are global" in message
    assert "delete the key" in message
    assert "pydantic" not in message


def test_instructions_resolves_against_crewfile_dir(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    _write(tmp_path / "docs" / "rules.md", "# Rules\n")
    crewfile.write_text(crewfile.read_text() + "instructions: docs/rules.md\n")

    crew = load_crewfile(crewfile)
    assert crew.registry.instructions == tmp_path / "docs" / "rules.md"


def _crew_with_paths(
    root: Path,
    *,
    persona: str = "agents/a.md",
    skill: str = "skills/s",
    instructions: str | None = None,
) -> Path:
    """A valid crew whose artifact paths are overridable, one per kind."""

    _write(root / "agents" / "a.md", "# Agent\n")
    _write(root / "skills" / "s" / "SKILL.md", _skill_md())
    _write(root / "rules.md", "# Rules\n")
    text = 'version: "0.1"\nname: paths\n'
    if instructions is not None:
        text += f"instructions: {instructions}\n"
    text += f"agents:\n  a:\n    persona: {persona}\nskills:\n  s:\n    path: {skill}\n"
    return _write(root / "crew.crewfile", text)


@pytest.mark.parametrize("kind", ["persona", "skill", "instructions"])
@pytest.mark.parametrize(
    "target", ["/etc/hosts", "../outside.md", "docs/../../outside.md"]
)
def test_artifact_paths_must_stay_inside_the_pack(
    tmp_path: Path, kind: str, target: str
) -> None:
    # Fix 9: a github: pack must not read arbitrary local files into the plugin
    # or CLAUDE.md. One guard covers every path the crewfile names.
    _write(tmp_path / "outside.md", "secret\n")
    crewfile = _crew_with_paths(tmp_path / "pack", **{kind: target})

    with pytest.raises(CrewfileSyntaxError, match=f"{kind}.*inside the pack"):
        load_crewfile(crewfile)


@pytest.mark.parametrize("kind", ["persona", "skill", "instructions"])
def test_symlink_escaping_the_pack_is_rejected(tmp_path: Path, kind: str) -> None:
    outside = tmp_path / "outside"
    _write(outside / "a.md", "# Agent\n")
    _write(outside / "SKILL.md", _skill_md())
    _write(outside / "rules.md", "# Rules\n")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "link").symlink_to(outside, target_is_directory=True)
    target = {"persona": "link/a.md", "skill": "link", "instructions": "link/rules.md"}

    crewfile = _crew_with_paths(pack, **{kind: target[kind]})

    with pytest.raises(CrewfileSyntaxError, match=f"{kind}.*outside the pack"):
        load_crewfile(crewfile)


def test_missing_instructions_file_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    crewfile.write_text(crewfile.read_text() + "instructions: docs/rules.md\n")

    with pytest.raises(MissingArtifactError, match="instructions file") as exc:
        load_crewfile(crewfile)
    assert exc.value.path == crewfile


@pytest.mark.parametrize("key", ["include", "crew"])
def test_composition_keys_are_rejected(tmp_path: Path, key: str) -> None:
    crewfile = _write(
        tmp_path / "crewspec.yaml", f'version: "0.1"\nname: c\n{key}: [x]\n'
    )
    with pytest.raises(CrewfileSyntaxError) as exc:
        load_crewfile(crewfile)
    message = str(exc.value)
    assert message == (
        f"{crewfile}: `{key}` is not supported yet; see Not in V1 in the README"
    )
    assert "validation error" not in message
    assert "pydantic" not in message


def test_merge_unions_frontmatter_and_crewfile_deps() -> None:
    lint = load_crewfile(HAPPY).registry.skills["lint"]
    # frontmatter declares skills:[format] mcps:[github]; crewfile declares
    # skills:[format]. The mcp comes only from frontmatter; format dedupes.
    assert lint.deps.skills == ["format"]
    assert lint.deps.mcps == ["github"]


def test_merge_is_frontmatter_first_and_deduplicated(tmp_path: Path) -> None:
    _write(tmp_path / "agents" / "a.md", "# Agent\n")
    _write(tmp_path / "skills" / "s" / "SKILL.md", _skill_md(skills="x", mcps="p"))
    for name in ("x", "y"):
        _write(tmp_path / "skills" / name / "SKILL.md", _skill_md())
    crewfile = _write(
        tmp_path / "crew.crewfile",
        """
version: "0.1"
name: merge
skills:
  s:
    path: skills/s
    depends_on:
      skills: [y, x]
      mcps: [q]
  x:
    path: skills/x
  y:
    path: skills/y
mcps:
  p: {type: stdio, command: p}
  q: {type: stdio, command: q}
""",
    )

    s = load_crewfile(crewfile).registry.skills["s"]
    # Frontmatter (x, p) first, then crewfile additions (y, q); x deduped.
    assert s.deps.skills == ["x", "y"]
    assert s.deps.mcps == ["p", "q"]


def test_missing_persona_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    (tmp_path / "agents" / "a.md").unlink()
    with pytest.raises(MissingArtifactError, match="persona") as exc:
        load_crewfile(crewfile)
    assert exc.value.path == crewfile


def test_missing_skill_dir_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    (tmp_path / "skills" / "s" / "SKILL.md").unlink()
    (tmp_path / "skills" / "s").rmdir()
    with pytest.raises(MissingArtifactError, match="directory"):
        load_crewfile(crewfile)


def test_missing_skill_md_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    (tmp_path / "skills" / "s" / "SKILL.md").unlink()
    with pytest.raises(MissingArtifactError, match="SKILL.md"):
        load_crewfile(crewfile)


def test_missing_frontmatter_fence_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    _write(tmp_path / "skills" / "s" / "SKILL.md", "# No frontmatter here\n")
    with pytest.raises(FrontmatterError, match="frontmatter fence"):
        load_crewfile(crewfile)


def test_malformed_frontmatter_yaml_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    _write(
        tmp_path / "skills" / "s" / "SKILL.md",
        "---\nname: s\n  bad: : indent\n---\n",
    )
    with pytest.raises(FrontmatterError, match="malformed YAML") as exc:
        load_crewfile(crewfile)
    assert exc.value.line is not None


def test_unknown_x_crewspec_key_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    _write(
        tmp_path / "skills" / "s" / "SKILL.md",
        "---\nname: s\nx-crewspec:\n  widgets: [w]\n---\n",
    )
    with pytest.raises(FrontmatterError, match="x-crewspec"):
        load_crewfile(crewfile)


def test_unknown_dependency_reference_raises(tmp_path: Path) -> None:
    _write(tmp_path / "agents" / "a.md", "# Agent\n")
    crewfile = _write(
        tmp_path / "crew.crewfile",
        """
version: "0.1"
name: dangling
agents:
  a:
    persona: agents/a.md
    depends_on:
      skills: [ghost]
""",
    )
    with pytest.raises(UnknownReferenceError, match="ghost"):
        load_crewfile(crewfile)


def test_unknown_skill_mcp_reference_raises(tmp_path: Path) -> None:
    crewfile = _build_min_crew(tmp_path)
    _write(tmp_path / "skills" / "s" / "SKILL.md", _skill_md(mcps="ghost"))
    with pytest.raises(UnknownReferenceError, match="skills.s.depends_on.mcps"):
        load_crewfile(crewfile)


def test_missing_crewfile_raises_syntax_error(tmp_path: Path) -> None:
    missing = tmp_path / "absent.crewfile"
    with pytest.raises(CrewfileSyntaxError, match="does not exist") as exc:
        load_crewfile(missing)
    assert exc.value.path == missing


def test_invalid_yaml_raises_syntax_error(tmp_path: Path) -> None:
    crewfile = _write(tmp_path / "crew.crewfile", "version: : nope\n")
    with pytest.raises(CrewfileSyntaxError, match="invalid YAML") as exc:
        load_crewfile(crewfile)
    assert exc.value.path == crewfile


def test_schema_violation_raises_syntax_error(tmp_path: Path) -> None:
    crewfile = _write(
        tmp_path / "crew.crewfile", 'version: "0.1"\nname: x\nbogus: true\n'
    )
    with pytest.raises(CrewfileSyntaxError, match="schema"):
        load_crewfile(crewfile)
