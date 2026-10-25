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

MCP servers stay in the plugin's ``.mcp.json`` and ``instructions`` is not
written for this target. Mirroring the Claude target's user-scope outputs
(``~/.codex/AGENTS.md`` and ``[mcp_servers.*]`` in ``config.toml``) is a
follow-up.
"""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import yaml

from crewspec import __version__
from crewspec.emitters.base import Emitter, PackState
from crewspec.emitters.claude import (
    _SKILL_COPY_IGNORE,
    _normalize_skill_md,
)
from crewspec.emitters.errors import EmitError
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedMcp, ResolvedSkill
from crewspec.models.interpolate import EnvRef, parse_refs
from crewspec.models.mcp import McpServer, StdioMcpServer


class CodexEmitter(Emitter):
    """Render the crewspec IR into a repo-local Codex plugin under ``dest``."""

    name = "codex"
    plugins_dir = ("plugins",)
    meta_dir = ".codex-plugin"

    #: Repo-local marketplace file, relative to ``dest``.
    marketplace_path = (".agents", "plugins", "marketplace.json")
    mcp_file = ".mcp.json"

    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        plugin_root = self.output_path(dest, ir.name)
        marketplace_path = dest.joinpath(*self.marketplace_path)

        # Validate every input and read every file this install touches before
        # changing anything, so a failure cannot leave a half-written plugin
        # directory that later installs refuse to replace.
        self.previous_state(dest, ir.name)
        _check_name_collisions(ir)
        agents = [(agent, self.read_persona(agent)) for agent in ir.agents]
        servers = _servers(ir.mcps)
        marketplace = _merged_marketplace(marketplace_path, ir.name, self.plugins_dir)

        # crewspec owns the plugin subtree. The marketplace file is merged
        # separately because other local plugins may share that catalog.
        if plugin_root.exists():
            shutil.rmtree(plugin_root)
        plugin_root.mkdir(parents=True)

        # State first: from here on the pack owns the directory.
        PackState(name=ir.name, mcps=tuple(servers)).write(
            self.state_path(dest, ir.name)
        )
        self._write_manifest(plugin_root, ir.name, has_mcps=bool(servers))
        self._copy_skills(plugin_root, ir.skills)
        for agent, (description, body) in agents:
            self._write_agent_skill(plugin_root, agent, description, body, ir.mcps)
        if servers:
            path = plugin_root / self.mcp_file
            path.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")
        marketplace_path.parent.mkdir(parents=True, exist_ok=True)
        marketplace_path.write_text(json.dumps(marketplace, indent=2) + "\n")

    def uninstall(self, pack: str, dest: Path) -> None:
        self.read_state(dest, pack)
        marketplace_file = dest.joinpath(*self.marketplace_path)
        if marketplace_file.exists():
            marketplace = _read_marketplace(marketplace_file)
            plugins = marketplace.get("plugins")
            if isinstance(plugins, list):
                marketplace["plugins"] = [
                    item for item in plugins if not _is_plugin_entry(item, pack)
                ]
                marketplace_file.write_text(json.dumps(marketplace, indent=2) + "\n")
        shutil.rmtree(self.output_path(dest, pack))

    def _write_manifest(self, plugin_root: Path, name: str, *, has_mcps: bool) -> None:
        manifest: dict[str, object] = {
            "name": name,
            "version": __version__,
            "description": "Generated crewspec workflows for Codex.",
            "skills": "./skills/",
            "interface": {
                "displayName": name,
                "shortDescription": "Generated crewspec workflows",
                "longDescription": (
                    "Generated Codex plugin containing crewspec agent workflows, "
                    "skills, and MCP server configuration."
                ),
                "developerName": name,
                "category": "Developer Tools",
                "capabilities": ["Read", "Write"],
            },
        }
        if has_mcps:
            manifest["mcpServers"] = "./" + self.mcp_file

        path = plugin_root / self.meta_dir / "plugin.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2) + "\n")

    def _copy_skills(
        self, plugin_root: Path, skills: tuple[ResolvedSkill, ...]
    ) -> None:
        for skill in skills:
            dest_dir = plugin_root / "skills" / skill.name
            shutil.copytree(skill.path, dest_dir, ignore=_SKILL_COPY_IGNORE)
            _normalize_skill_md(dest_dir)

    def _write_agent_skill(
        self,
        plugin_root: Path,
        agent: ResolvedAgent,
        description: str,
        body: str,
        mcps: tuple[ResolvedMcp, ...],
    ) -> None:
        frontmatter = {"name": agent.name, "description": description}
        block = yaml.safe_dump(
            frontmatter, sort_keys=False, allow_unicode=True, width=1000
        )

        text = f"---\n{block}---\n\n" + body.lstrip("\n")
        wiring = _agent_wiring(agent, mcps)
        if wiring:
            text = text.rstrip() + "\n\n" + wiring

        path = plugin_root / "skills" / agent.name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("\n" if not text.endswith("\n") else ""))


def _check_name_collisions(ir: ResolvedIR) -> None:
    """Codex maps agents to skills, so the two name sets may not overlap."""

    conflicts = sorted({a.name for a in ir.agents} & {s.name for s in ir.skills})
    if conflicts:
        raise EmitError(
            "Codex emitter maps agents to skills, so agent and skill names "
            f"must not collide; conflicting name(s): {', '.join(conflicts)}"
        )


def _servers(mcps: tuple[ResolvedMcp, ...]) -> dict[str, dict[str, object]]:
    """Render every MCP server for the plugin ``.mcp.json``, warning on file secrets."""

    file_secrets: set[str] = set()
    servers = {mcp.name: _server_dict(mcp.server, file_secrets) for mcp in mcps}
    for target in sorted(file_secrets):
        warnings.warn(
            f"file secret '${{file:{target}}}' has no Codex .mcp.json "
            "equivalent; it is written verbatim and will not resolve until "
            "a launcher mounts it",
            stacklevel=3,
        )
    return servers


def _merged_marketplace(
    path: Path, name: str, plugins_dir: tuple[str, ...]
) -> dict[str, object]:
    """The marketplace at ``path`` with this pack's entry replaced or appended."""

    marketplace = _read_marketplace(path)
    marketplace["name"] = marketplace.get("name") or "local-repo"
    plugins = marketplace.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise EmitError(f"Codex marketplace '{path}' has non-list 'plugins'")

    entry = {
        "name": name,
        "source": {"source": "local", "path": "./" + "/".join((*plugins_dir, name))},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Developer Tools",
        "interface": {"displayName": name},
    }
    kept = [item for item in plugins if not _is_plugin_entry(item, name)]
    kept.append(entry)
    marketplace["plugins"] = kept
    return marketplace


def _agent_wiring(agent: ResolvedAgent, mcps: tuple[ResolvedMcp, ...]) -> str:
    sections: list[str] = []
    if agent.skills:
        names = ", ".join(f"${skill.name}" for skill in agent.skills)
        sections.append(
            "## crewspec Skill Wiring\n\n"
            f"Before doing this workflow, read and apply these bundled skills: {names}."
        )
    if mcps:
        names = ", ".join(mcp.name for mcp in mcps)
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
