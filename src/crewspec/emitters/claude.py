"""Emitter that renders a crewspec into Claude Code artifacts.

The Claude target writes to three places under ``<dest>``:

* ``.claude/skills/<name>/`` — a *skills-directory plugin* crewspec owns
  outright. Its ``.claude-plugin/plugin.json`` manifest lets Claude Code
  discover it on the next session with no marketplace and no install step, and
  because crewspec owns the whole directory, re-emission wipes and regenerates
  it freely. ``.claude-plugin/crewspec.json`` records what the pack wrote
  outside this directory.
* ``.claude.json`` — the user-scope Claude Code config. Each declared MCP
  server is merged in under ``mcpServers`` (plugin-shipped MCPs are not
  user-scoped, so the plugin cannot carry them). Only keys crewspec wrote are
  ever changed or removed.
* ``.claude/CLAUDE.md`` — the optional ``instructions`` file lands here as a
  block between ``<!-- crewspec:<name>:begin -->`` and ``<!-- crewspec:<name>:end
  -->`` markers. Nothing outside the block is touched.

All Claude-specific frontmatter and schema knowledge lives here so a sibling
emitter can reuse :class:`~crewspec.emitters.base.Emitter` untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Iterable

import yaml

from crewspec.emitters.base import Emitter, PackState
from crewspec.emitters.errors import EmitError
from crewspec.ir import ResolvedAgent, ResolvedIR, ResolvedSkill
from crewspec.models.interpolate import EnvRef, parse_refs
from crewspec.models.mcp import McpServer, StdioMcpServer

#: VCS/OS cruft never worth copying into a skill's emitted tree. ``.git`` in
#: particular can be enormous and would embed a nested repo in the plugin.
_SKILL_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store")

#: User-scope Claude Code config and global instructions, relative to ``dest``.
_USER_CONFIG = ".claude.json"
_INSTRUCTIONS = (".claude", "CLAUDE.md")


class ClaudeEmitter(Emitter):
    """Render the crewspec IR into a Claude Code plugin under ``dest``."""

    name = "claude"
    plugins_dir = (".claude", "skills")
    meta_dir = ".claude-plugin"

    def emit(self, ir: ResolvedIR, dest: Path) -> None:
        plugin_root = self.output_path(dest, ir.name)
        config_path = dest / _USER_CONFIG
        instructions_path = dest.joinpath(*_INSTRUCTIONS)

        # Validate every input and read every file this install touches before
        # changing anything, so a failure leaves the previous install, and its
        # record of what it owns, exactly as it was.
        previous = self.previous_state(dest, ir.name)
        agents = [(agent, self.read_persona(agent)) for agent in ir.agents]
        servers = {mcp.name: _server_dict(mcp.server) for mcp in ir.mcps}
        self._check_mcp_ownership(dest, ir.name, servers)
        stale = previous.mcps if previous else ()
        config = _merged_config(config_path, servers, stale)
        instructions = _merged_instructions(instructions_path, ir.name, ir.instructions)

        # crewspec owns this subtree: wipe and regenerate so re-emission is
        # byte-identical and never leaves a stale agent/skill behind.
        if plugin_root.exists():
            shutil.rmtree(plugin_root)
        plugin_root.mkdir(parents=True)

        # State first: from here on the pack owns what it is about to write.
        PackState(
            name=ir.name,
            mcps=tuple(servers),
            instructions=ir.instructions is not None,
        ).write(self.state_path(dest, ir.name))
        if config is not None:
            _write_atomic(config_path, config)
        if instructions is not None:
            _write_atomic(instructions_path, instructions)

        self._write_manifest(plugin_root, ir.name)
        for agent, (description, body) in agents:
            self._write_agent(plugin_root, agent, description, body)
        self._copy_skills(plugin_root, ir.skills)

    def uninstall(self, pack: str, dest: Path) -> None:
        state = self.read_state(dest, pack)
        config_path = dest / _USER_CONFIG
        instructions_path = dest.joinpath(*_INSTRUCTIONS)
        config = _merged_config(config_path, {}, state.mcps)
        instructions = _merged_instructions(instructions_path, pack, None)

        if config is not None:
            _write_atomic(config_path, config)
        if instructions is not None:
            _write_atomic(instructions_path, instructions)
        shutil.rmtree(self.output_path(dest, pack))

    def _check_mcp_ownership(
        self, dest: Path, pack: str, servers: dict[str, dict[str, object]]
    ) -> None:
        """Refuse an MCP key another installed pack already owns.

        Two packs writing the same ``mcpServers`` key would silently overwrite
        each other and the first ``uninstall`` would delete the survivor's entry.
        """

        for root in self.installed(dest):
            if root.name == pack:
                continue
            try:
                other = PackState.read(self.state_path(dest, root.name))
            except EmitError as exc:
                # Same treatment as `list`: a damaged sibling is reported, not
                # allowed to block an unrelated install.
                warnings.warn(f"{exc}; MCP servers not listed", stacklevel=2)
                continue
            taken = sorted(set(other.mcps if other else ()) & set(servers))
            if taken:
                raise EmitError(
                    f"MCP server(s) {', '.join(taken)} already installed by pack "
                    f"'{root.name}'; pack '{pack}' cannot install them too"
                )

    def _write_manifest(self, plugin_root: Path, name: str) -> None:
        manifest = {"name": name, "description": "Generated by crewspec."}
        path = plugin_root / self.meta_dir / "plugin.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2) + "\n")

    def _write_agent(
        self, plugin_root: Path, agent: ResolvedAgent, description: str, body: str
    ) -> None:
        # Field order is fixed for stable, scannable output. ``mcpServers`` is
        # deliberately absent: plugin-shipped agents may not declare it, so the
        # servers live in the user-scope .claude.json instead.
        frontmatter: dict[str, object] = {
            "name": agent.name,
            "description": description,
        }
        if agent.tools:
            # Exactly what the crewfile grants. An MCP's tools are reachable
            # only when the agent lists them here (e.g. ``mcp__github__*``).
            frontmatter["tools"] = ", ".join(agent.tools)
        if agent.skills:
            frontmatter["skills"] = [skill.name for skill in agent.skills]

        block = yaml.safe_dump(
            frontmatter, sort_keys=False, allow_unicode=True, width=1000
        )
        text = f"---\n{block}---\n\n" + body.lstrip("\n")

        path = plugin_root / "agents" / f"{agent.name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def _copy_skills(
        self, plugin_root: Path, skills: tuple[ResolvedSkill, ...]
    ) -> None:
        for skill in skills:
            dest_dir = plugin_root / "skills" / skill.name
            # A skill *is* its directory: copy the whole tree so nothing the
            # SKILL.md references (reference docs, scripts, data, fonts, a
            # LICENSE, ...) is left behind — only VCS/OS junk is dropped.
            # plugin_root was just wiped, so dest_dir cannot pre-exist.
            shutil.copytree(skill.path, dest_dir, ignore=_SKILL_COPY_IGNORE)
            _normalize_skill_md(dest_dir)


def _read_utf8(path: Path) -> str:
    """Read ``path`` as UTF-8 without newline translation, failing cleanly."""

    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EmitError(f"'{path}' is not valid UTF-8") from exc
    except OSError as exc:
        raise EmitError(f"'{path}' could not be read: {exc.strerror}") from exc


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file in the same directory.

    ``.claude.json`` may be written by Claude Code at the same time; a rename
    means a reader sees either the old file or the new one, never a torn write.
    A symlinked target (a dotfiles repo, say) is followed so the file behind
    the link is replaced and the link survives. An existing file keeps its
    mode; a new one gets the mode ``open`` would have given it.
    """

    real = Path(os.path.realpath(path))
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if real.exists():
            shutil.copymode(real, tmp)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _merged_config(
    path: Path, servers: dict[str, dict[str, object]], stale: Iterable[str]
) -> bytes | None:
    """The user-scope config at ``path`` with ``servers`` merged into ``mcpServers``.

    Keys in ``stale`` that are not in ``servers`` are removed; every other key
    and value in the file is kept. Returns ``None`` when there is nothing to add
    or drop (the file is then left alone, and never created). An absent file
    becomes ``{"mcpServers": {...}}``.
    """

    stale = [key for key in stale if key not in servers]
    if not servers and not stale:
        return None
    if path.exists():
        data = _read_json_object(path)
    elif servers:
        data = {}
    else:
        return None

    existing = data.setdefault("mcpServers", {})
    if not isinstance(existing, dict):
        raise EmitError(f"'{path}' has a non-object 'mcpServers'")
    for key in stale:
        existing.pop(key, None)
    existing.update(servers)
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(_read_utf8(path))
    except ValueError as exc:
        raise EmitError(f"'{path}' is not valid JSON") from exc
    if not isinstance(data, dict):
        raise EmitError(f"'{path}' must be a JSON object")
    return data


def _merged_instructions(path: Path, pack: str, source: Path | None) -> bytes | None:
    """The ``CLAUDE.md`` at ``path`` with the pack's block written or removed.

    With ``source``, its content replaces an existing block in place or is
    appended after a blank line (creating the file if needed). Without it, the
    block and its markers are removed. Returns ``None`` when the file would not
    change. Bytes outside the block are never touched.
    """

    text = _read_utf8(path) if path.exists() else None
    if source is not None:
        new = _splice_block(text or "", pack, _read_utf8(source), path)
    elif text is None:
        return None
    else:
        new = _strip_block(text, pack, path)
    return None if new == text else new.encode("utf-8")


def _markers(pack: str) -> tuple[str, str]:
    return f"<!-- crewspec:{pack}:begin -->", f"<!-- crewspec:{pack}:end -->"


def _find_block(text: str, begin: str, end: str, path: Path) -> tuple[int, int] | None:
    """``(start, stop)`` of the managed block in ``text``, markers inclusive."""

    start = text.find(begin)
    if start < 0:
        return None
    stop = text.find(end, start + len(begin))
    if stop < 0:
        raise EmitError(f"'{path}' has '{begin}' without a matching '{end}'")
    return start, stop + len(end)


def _splice_block(text: str, pack: str, content: str, path: Path) -> str:
    begin, end = _markers(pack)
    body = content.rstrip("\n")
    block = f"{begin}\n{body}\n{end}"
    span = _find_block(text, begin, end, path)
    if span is not None:
        start, stop = span
        return text[:start] + block + text[stop:]
    if not text:
        return block + "\n"
    sep = "" if text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
    return text + sep + block + "\n"


def _strip_block(text: str, pack: str, path: Path) -> str:
    begin, end = _markers(pack)
    span = _find_block(text, begin, end, path)
    if span is None:
        return text
    start, stop = span
    if text.startswith("\n", stop):
        stop += 1
    return text[:start] + text[stop:]


def _normalize_skill_md(skill_dir: Path) -> None:
    """Rename a case-variant ``skill.md`` to the canonical ``SKILL.md``.

    crewspec resolves skills case-insensitively on macOS, so a source skill may
    carry a lowercase ``skill.md``. A case-sensitive host (Linux CI, a cloned
    repo) needs the exact name, so normalise it on emit to keep the plugin
    portable. A dir that already has ``SKILL.md`` is left untouched.
    """

    canonical = skill_dir / "SKILL.md"
    for child in skill_dir.iterdir():
        if child.name != "SKILL.md" and child.name.lower() == "skill.md":
            child.rename(canonical)
            return


def _server_dict(server: McpServer) -> dict[str, object]:
    """Render one MCP server into its Claude ``mcpServers`` entry.

    ``${...}`` references are rendered by kind (see :func:`_render_value`):
    env refs survive verbatim for Claude to expand and ``${secret:NAME}``
    collapses to ``${NAME}``.
    """

    if isinstance(server, StdioMcpServer):
        data: dict[str, object] = {
            "type": server.type,
            "command": _render_value(server.command),
        }
        if server.args:
            data["args"] = [_render_value(arg) for arg in server.args]
        if server.env:
            data["env"] = {
                key: _render_value(value) for key, value in server.env.items()
            }
        return data

    # HttpMcpServer | SseMcpServer — both carry url + headers.
    remote: dict[str, object] = {
        "type": server.type,
        "url": _render_value(server.url),
    }
    if server.headers:
        remote["headers"] = {
            key: _render_value(value) for key, value in server.headers.items()
        }
    return remote


def _render_value(value: str) -> str:
    """Rewrite ``${...}`` references for the Claude ``mcpServers`` target.

    * ``${VAR}`` / ``${VAR:-default}`` (env) — kept verbatim; Claude Code expands
      them natively at runtime.
    * ``${secret:NAME}`` — rewritten to ``${NAME}`` so only the name lands in the
      file and Claude expands it from the runtime environment.
    * ``${file:PATH}`` — an error: no target expands a file secret, so writing
      it would ship a reference that can never resolve.
    """

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
        else:
            raise EmitError(
                f"'${{file:{fragment.target}}}' is not supported: no target expands "
                "a file secret; use ${secret:NAME} or ${VAR} instead"
            )
    return "".join(out)
