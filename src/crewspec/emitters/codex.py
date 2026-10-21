"""Emitter that renders a crewspec into Codex-compatible plugin artifacts.

Codex plugins are installable bundles with a required
``.codex-plugin/plugin.json`` manifest. For repo-local testing and sharing,
Codex discovers plugins through ``.agents/plugins/marketplace.json`` entries
whose ``source.path`` points at the plugin directory, relative to the marketplace
root.

crewspec agents do not map to a first-class Codex plugin ``agents`` directory.
Instead, each resolved agent is emitted as a Codex skill that contains the
persona instructions plus the resolved skill/MCP wiring. The source skills are
copied alongside those generated agent skills so Codex can load them through the
normal skill mechanism.
"""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import yaml

from crewspec import __version__
from crewspec.emitters.base import Emitter
from crewspec.emitters.claude import (
    _SKILL_COPY_IGNORE,
    _normalize_skill_md,
    _split_frontmatter,
)
from crewspec.emitters.errors import EmitError, MissingPersonaDescription
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models.interpolate import EnvRef, parse_refs
from crewspec.models.mcp import McpServer, StdioMcpServer


class CodexEmitter(Emitter):
    """Render the crewspec IR into a repo-local Codex plugin under ``dest``."""

    name = "codex"

    #: Plugin directory, relative to ``dest``. crewspec owns this subtree.
    plugin_path = ("plugins", "crewspec")
    #: Repo-local marketplace file, relative to ``dest``.
    marketplace_path = (".agents", "plugins", "marketplace.json")
    #: The plugin's ``name`` in its ``plugin.json`` manifest.
    plugin_name = "crewspec"
    mcp_file = ".mcp.json"

    def output_path(self, dest: Path) -> Path:
        """The plugin root under ``dest`` that crewspec owns and writes into."""
        return dest.joinpath(*self.plugin_path)

    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        plugin_root = self.output_path(dest)

        # crewspec owns the plugin subtree. The marketplace file is merged
        # separately because other local plugins may share that catalog.
        if plugin_root.exists():
            shutil.rmtree(plugin_root)
        plugin_root.mkdir(parents=True)

        self._write_manifest(plugin_root, has_mcps=bool(ir.mcps))
        self._copy_skills(plugin_root, ir.agents)
        for agent in ir.agents:
            self._write_agent_skill(plugin_root, agent)
        self._write_mcp(plugin_root, ir.mcps)
        self._write_marketplace(dest)

    def _write_manifest(self, plugin_root: Path, *, has_mcps: bool) -> None:
        manifest: dict[str, object] = {
            "name": self.plugin_name,
            "version": __version__,
            "description": "Generated crewspec workflows for Codex.",
            "skills": "./skills/",
            "interface": {
                "displayName": "crewspec",
                "shortDescription": "Generated crewspec workflows",
                "longDescription": (
                    "Generated Codex plugin containing crewspec agent workflows, "
                    "skills, and MCP server configuration."
                ),
                "developerName": "crewspec",
                "category": "Developer Tools",
                "capabilities": ["Read", "Write"],
            },
        }
        if has_mcps:
            manifest["mcpServers"] = "./" + self.mcp_file

        path = plugin_root / ".codex-plugin" / "plugin.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2) + "\n")

    def _copy_skills(
        self, plugin_root: Path, agents: tuple[ResolvedAgent, ...]
    ) -> None:
        skills: dict[str, ResolvedSkill] = {}
        for agent in agents:
            for skill in agent.skills:
                skills.setdefault(skill.name, skill)

        agent_names = {agent.name for agent in agents}
        conflicts = sorted(agent_names & set(skills))
        if conflicts:
            joined = ", ".join(conflicts)
            raise EmitError(
                "Codex emitter maps agents to skills, so agent and skill names "
                f"must not collide; conflicting name(s): {joined}"
            )

        for skill in skills.values():
            dest_dir = plugin_root / "skills" / skill.name
            shutil.copytree(skill.path, dest_dir, ignore=_SKILL_COPY_IGNORE)
            _normalize_skill_md(dest_dir)

    def _write_agent_skill(self, plugin_root: Path, agent: ResolvedAgent) -> None:
        description, body = self._read_persona(agent)
        frontmatter = {"name": agent.name, "description": description}
        block = yaml.safe_dump(
            frontmatter, sort_keys=False, allow_unicode=True, width=1000
        )

        text = f"---\n{block}---\n\n" + body.lstrip("\n")
        wiring = _agent_wiring(agent)
        if wiring:
            text = text.rstrip() + "\n\n" + wiring

        path = plugin_root / "skills" / agent.name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("\n" if not text.endswith("\n") else ""))

    def _read_persona(self, agent: ResolvedAgent) -> tuple[str, str]:
        text = agent.persona_path.read_text()
        meta, body = _split_frontmatter(text)
        description = meta.get("description") if meta else None
        if not isinstance(description, str) or not description:
            raise MissingPersonaDescription(agent.name, agent.persona_path)
        return description, body

    def _write_mcp(self, plugin_root: Path, mcps: tuple[ResolvedMcp, ...]) -> None:
        if not mcps:
            return

        file_secrets: set[str] = set()
        servers = {mcp.name: _server_dict(mcp.server, file_secrets) for mcp in mcps}
        for target in sorted(file_secrets):
            warnings.warn(
                f"file secret '${{file:{target}}}' has no Codex .mcp.json "
                "equivalent; it is written verbatim and will not resolve until "
                "a launcher mounts it",
                stacklevel=2,
            )

        path = plugin_root / self.mcp_file
        path.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")

    def _write_marketplace(self, dest: Path) -> None:
        path = dest.joinpath(*self.marketplace_path)
        marketplace = _read_marketplace(path)
        marketplace["name"] = marketplace.get("name") or "local-repo"
        plugins = marketplace.setdefault("plugins", [])
        if not isinstance(plugins, list):
            raise EmitError(f"Codex marketplace '{path}' has non-list 'plugins'")

        entry = {
            "name": self.plugin_name,
            "source": {
                "source": "local",
                "path": "./" + "/".join(self.plugin_path),
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
            "interface": {"displayName": "crewspec"},
        }

        kept = [
            item for item in plugins if not _is_plugin_entry(item, self.plugin_name)
        ]
        kept.append(entry)
        marketplace["plugins"] = kept

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marketplace, indent=2) + "\n")


def _agent_wiring(agent: ResolvedAgent) -> str:
    sections: list[str] = []
    if agent.skills:
        names = ", ".join(f"${skill.name}" for skill in agent.skills)
        sections.append(
            "## crewspec Skill Wiring\n\n"
            f"Before doing this workflow, read and apply these bundled skills: {names}."
        )
    if agent.mcps:
        names = ", ".join(mcp.name for mcp in agent.mcps)
        sections.append(
            "## crewspec MCP Wiring\n\n"
            f"Use these bundled MCP servers when the workflow needs them: {names}."
        )
    if agent.tools:
        names = ", ".join(agent.tools)
        sections.append(
            "## crewspec Tool Guidance\n\n"
            f"The source crew allowed these built-in tools for this agent: {names}."
        )
    return "\n\n".join(sections)


def _read_marketplace(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"name": "local-repo", "plugins": []}

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise EmitError(f"Codex marketplace '{path}' is not valid JSON") from exc
    if not isinstance(data, dict):
        raise EmitError(f"Codex marketplace '{path}' must be a JSON object")
    return data


def _is_plugin_entry(item: object, name: str) -> bool:
    return isinstance(item, dict) and item.get("name") == name


def _server_dict(server: McpServer, file_secrets: set[str]) -> dict[str, object]:
    """Render one MCP server into Codex plugin ``.mcp.json`` config."""

    if isinstance(server, StdioMcpServer):
        data: dict[str, object] = {
            "command": _render_value(server.command, file_secrets),
        }
        if server.args:
            data["args"] = [_render_value(arg, file_secrets) for arg in server.args]
        if server.env:
            data["env"] = {
                key: _render_value(value, file_secrets)
                for key, value in server.env.items()
            }
        return data

    remote: dict[str, object] = {
        "type": server.type,
        "url": _render_value(server.url, file_secrets),
    }
    if server.headers:
        static_headers: dict[str, str] = {}
        env_headers: dict[str, str] = {}
        for key, value in server.headers.items():
            rendered = _render_value(value, file_secrets)
            env_ref = _single_secret_ref(value)
            if env_ref is not None:
                env_headers[key] = env_ref
            else:
                static_headers[key] = rendered
        if static_headers:
            remote["http_headers"] = static_headers
        if env_headers:
            remote["env_http_headers"] = env_headers
    return remote


def _single_secret_ref(value: str) -> str | None:
    fragments = parse_refs(value)
    if len(fragments) != 1:
        return None
    ref = fragments[0]
    if not isinstance(ref, str | EnvRef) and ref.kind == "secret":
        return ref.target
    return None


def _render_value(value: str, file_secrets: set[str]) -> str:
    """Rewrite ``${...}`` references for the Codex plugin ``.mcp.json`` target."""

    out: list[str] = []
    for fragment in parse_refs(value):
        if isinstance(fragment, str):
            out.append(fragment)
        elif isinstance(fragment, EnvRef):
            if fragment.default is not None:
                out.append(f"${{{fragment.name}:-{fragment.default}}}")
            else:
                out.append(f"${{{fragment.name}}}")
        elif fragment.kind == "secret":
            out.append(f"${{{fragment.target}}}")
        elif fragment.kind == "file":
            file_secrets.add(fragment.target)
            out.append(f"${{file:{fragment.target}}}")
        else:
            out.append(fragment.raw)
    return "".join(out)
