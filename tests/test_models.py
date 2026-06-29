"""Tests for the crewspec core data models."""

import pytest
from pydantic import TypeAdapter, ValidationError

from crewspec.models import (
    AgentDef,
    CrewDoc,
    HttpMcpServer,
    McpServer,
    SseMcpServer,
    StdioMcpServer,
)

_mcp_adapter: TypeAdapter[McpServer] = TypeAdapter(McpServer)


FULL_DOC = {
    "version": "0.1",
    "agents": {
        "reviewer": {
            "name": "reviewer",
            "persona": "personas/reviewer.md",
            "allowed_tools": ["Read", "Edit", "mcp__linear__*"],
            "depends_on": {"skills": ["lint"], "mcps": ["github"]},
        }
    },
    "skills": {
        "lint": {
            "name": "lint",
            "path": "skills/lint",
            "description": "Run the linter",
            "depends_on": {"skills": [], "mcps": ["github"]},
        }
    },
    "mcps": {
        "github": {"type": "stdio", "command": "gh-mcp", "args": ["serve"]},
        "remote": {"type": "http", "url": "https://api.example.com"},
    },
    "include": ["base.crewfile"],
    "crew": {"team": {"members": ["reviewer"]}},
}


def test_full_doc_parses() -> None:
    doc = CrewDoc.model_validate(FULL_DOC)
    assert doc.version == "0.1"
    assert isinstance(doc.mcps["github"], StdioMcpServer)
    assert isinstance(doc.mcps["remote"], HttpMcpServer)
    assert doc.agents["reviewer"].depends_on.skills == ["lint"]
    assert doc.skills["lint"].depends_on.mcps == ["github"]
    assert doc.include == ["base.crewfile"]
    assert doc.crew == {"team": {"members": ["reviewer"]}}


def test_minimal_doc_defaults() -> None:
    doc = CrewDoc.model_validate({"version": "0.1"})
    assert doc.agents == {}
    assert doc.skills == {}
    assert doc.mcps == {}
    assert doc.include is None
    assert doc.crew is None


def test_mcp_discriminates_on_type() -> None:
    assert isinstance(
        _mcp_adapter.validate_python({"type": "sse", "url": "https://x"}),
        SseMcpServer,
    )


def test_unknown_mcp_type_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _mcp_adapter.validate_python({"type": "grpc", "url": "https://x"})
    assert "grpc" in str(exc.value)


def test_missing_required_field_is_path_aware() -> None:
    with pytest.raises(ValidationError) as exc:
        _mcp_adapter.validate_python({"type": "stdio"})
    err = exc.value.errors()[0]
    assert err["loc"][-1] == "command"


def test_extra_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StdioMcpServer.model_validate({"command": "x", "unexpected": "nope"})


def test_malformed_template_fails_at_parse() -> None:
    with pytest.raises(ValidationError) as exc:
        StdioMcpServer.model_validate({"command": "${BROKEN"})
    assert exc.value.errors()[0]["loc"][-1] == "command"


def test_agent_depends_on_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDef.model_validate(
            {
                "name": "a",
                "persona": "p.md",
                "depends_on": {"widgets": ["x"]},
            }
        )
    assert "widgets" in str(exc.value)


def test_skill_depends_on_has_no_agents_field() -> None:
    # SkillDeps must not accept ``agents``, that belongs to AgentDeps only.
    with pytest.raises(ValidationError):
        CrewDoc.model_validate(
            {
                "version": "0.1",
                "skills": {
                    "s": {
                        "name": "s",
                        "path": "skills/s",
                        "depends_on": {"agents": ["a"]},
                    }
                },
            }
        )


def test_stdio_resolve_interpolates() -> None:
    server = StdioMcpServer.model_validate(
        {
            "command": "${BIN:-server}",
            "args": ["--token", "${TOKEN}"],
            "env": {"HOME": "${HOME_DIR}"},
        }
    )
    resolved = server.resolve({"TOKEN": "abc", "HOME_DIR": "/home/me"})
    assert resolved.command == "server"
    assert resolved.args == ["--token", "abc"]
    assert resolved.env == {"HOME": "/home/me"}
    assert server.args == ["--token", "${TOKEN}"]


def test_http_resolve_interpolates() -> None:
    server = HttpMcpServer.model_validate(
        {
            "url": "https://${HOST}/v1",
            "headers": {"Authorization": "Bearer ${KEY}"},
        }
    )
    resolved = server.resolve({"HOST": "api.example.com", "KEY": "xyz"})
    assert resolved.url == "https://api.example.com/v1"
    assert resolved.headers == {"Authorization": "Bearer xyz"}
