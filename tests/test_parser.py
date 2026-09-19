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
agents:
  a:
    name: a
    persona: agents/a.md
skills:
  s:
    name: s
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


def test_include_is_parsed_but_not_followed(tmp_path: Path) -> None:
    crewfile = _write(
        tmp_path / "crewspec.yaml",
        'version: "0.1"\ninclude: [base.crewfile]\n',
    )
    crew = load_crewfile(crewfile)
    assert crew.doc.include == ["base.crewfile"]
    assert crew.registry.agents == {}


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
skills:
  s:
    name: s
    path: skills/s
    depends_on:
      skills: [y, x]
      mcps: [q]
  x:
    name: x
    path: skills/x
  y:
    name: y
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
agents:
  a:
    name: a
    persona: agents/a.md
    depends_on:
      mcps: [ghost]
""",
    )
    with pytest.raises(UnknownReferenceError, match="ghost"):
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
    crewfile = _write(tmp_path / "crew.crewfile", 'version: "0.1"\nbogus: true\n')
    with pytest.raises(CrewfileSyntaxError, match="schema"):
        load_crewfile(crewfile)
