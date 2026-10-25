# crewspec

Declare an AI agent crew once. Compile it into the native plugin format of
Claude Code or Codex.

A crewfile names a pack and its agents, skills, and MCP servers, plus the
dependencies between them: agent to skill, skill to skill, skill to MCP.
`SKILL.md` alone cannot say "this skill needs the `github` MCP". crewspec
records that, checks every reference, installs every declared skill and server,
and writes each target's wiring. It defines agents. It does not run them.

> **Status:** pre-1.0. `install` and `validate` work end to end. See
> [Not in V1](#not-in-v1) for what is missing.

## Install

PyPI publishing is pending. The `crewspec` versions on PyPI today are
placeholders with no working commands. Install from source instead:

```bash
uv tool install git+https://github.com/J-kenned/crewspec
# or
pip install git+https://github.com/J-kenned/crewspec
```

Requires Python 3.12 or later.

## Quickstart

The repo ships an example pack: one code-review agent with two skills that
share a `github` MCP. Install it for Claude Code (the default target) or Codex:

```bash
crewspec install ./examples/code-review-crew --target claude --out .
crewspec install ./examples/code-review-crew --target codex --out .
```

`--target claude` writes to three places under `<out>` (use `--out ~` for a
real install):

| Location | What crewspec writes | Ownership |
| -------- | -------------------- | --------- |
| `.claude/skills/<name>/` | The plugin: `.claude-plugin/plugin.json`, `agents/`, `skills/`, and a `.claude-plugin/crewspec.json` state file. | crewspec owns the directory and rewrites it on every install. |
| `.claude.json` | One `mcpServers` entry per declared MCP. | Only keys crewspec wrote are changed or removed; every other key and value is kept. Created as `{"mcpServers": {...}}` if absent. |
| `.claude/CLAUDE.md` | The `instructions` file, between `<!-- crewspec:<name>:begin -->` and `<!-- crewspec:<name>:end -->` markers. | Only the block changes. It is replaced in place, appended after a blank line, or removed when `instructions` goes away. |

Installing twice yields byte-identical files. The state file records which MCP
keys and instructions block the pack wrote so the next install or `uninstall`
removes exactly those. Two packs may not write the same `mcpServers` key;
`install` refuses to overwrite a plugin directory that has no state file (a
skill of your own with the pack's name, say), and `uninstall` only ever removes
a directory that has one.

**Upgrading from an earlier crewspec.** The old plugin at
`~/.claude/skills/crewspec/` predates the state file, so `list` and `uninstall`
do not manage it. Remove it by hand (`rm -rf ~/.claude/skills/crewspec`); its
`.mcp.json` would otherwise duplicate the servers now written to
`~/.claude.json`.

`--target codex` writes a Codex plugin to `<out>/plugins/<name>/` (with its
MCPs in a plugin-local `.mcp.json`) and adds an entry to
`<out>/.agents/plugins/marketplace.json`. Codex plugins have no agents
directory, so each agent becomes a skill under `skills/<agent>/SKILL.md`. The
marketplace file is merged, not replaced.

To check a pack without writing anything, or to remove one:

```bash
crewspec install ./examples/code-review-crew --dry-run
crewspec validate ./examples/code-review-crew
crewspec uninstall code-review-crew --target claude --out .
```

See [`examples/code-review-crew`](examples/code-review-crew) for the full
layout: a `.crewfile`, an agent persona, an instructions file, and skills that
declare their own dependencies in `SKILL.md` frontmatter.

## The file model

One schema, one parser, two file names:

| File            | Role |
| --------------- | ---- |
| `*.crewfile`    | One agent or pack. Points at persona and skill files, lists dependency edges, and holds MCP launch configs. Persona and skill content lives in its own files. |
| `crewspec.yaml` | The same schema. It will gain `include:` and `crew:` for composing several crewfiles; until composition lands, both keys are rejected with an error (see [Not in V1](#not-in-v1)). |

Content stays in its native form. Skill bodies are `SKILL.md` directories (the
Agent Skills format), personas are markdown, and MCP servers are launch
configs. The crewfile only references and wires them:

```yaml
version: "0.1"
name: code-review-crew          # required: ^[a-z0-9][a-z0-9-]*$
instructions: instructions.md   # optional: becomes a managed block in CLAUDE.md

agents:
  reviewer:
    persona: agents/reviewer.md
    allowed_tools: [Read, Grep, Bash, "mcp__github__*"]
    depends_on:
      skills: [code-review]     # listed in the agent's emitted file

skills:
  code-review: {path: skills/code-review}
  test-analysis: {path: skills/test-analysis}

mcps:
  github:
    type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
```

`name` is the pack's identity: it names the plugin directory and manifest on
every target, and the markers around its `CLAUDE.md` block, so several packs
can live side by side. `instructions` is a path relative to the crewfile and
must exist.

Every skill under `skills:` and every server under `mcps:` is installed. MCPs
are global to the pack: each server is installed once, and an agent reaches its
tools only through its own `allowed_tools` (`mcp__github__*` above). Nothing is
granted implicitly, and agents do not list MCPs under `depends_on`; a crewfile
that still has `agents.<x>.depends_on.mcps` fails with a message saying so.
Dependency edges are reference checks: an unknown name is an error, but they do
not select what is emitted, and a cycle is not an error. Every path the crewfile
names (personas, skill directories, `instructions`) must stay inside the pack:
no absolute paths, no `..`, and no symlinks that lead outside it.

A skill declares its own dependencies under an `x-crewspec:` key in its
`SKILL.md` frontmatter. Other tools ignore the key, so the skill stays portable:

```yaml
# skills/code-review/SKILL.md frontmatter
x-crewspec:
  skills: [test-analysis]
  mcps: [github]
```

The crewfile may add dependencies to a skill but cannot remove ones the skill
declares.

## CLI

| Command                    | What it does |
| -------------------------- | ------------ |
| `crewspec install SOURCE`  | Parse, resolve, and write artifacts. `--target` is `claude` (default) or `codex`. `--out DIR` defaults to `.`. `--dry-run` stops before writing. |
| `crewspec validate SOURCE` | Parse and resolve, then print the resolved plan: the pack name, each agent's tools and skills, every skill and MCP server, and any warnings. `--json` prints JSON instead. Writes nothing. |
| `crewspec uninstall NAME`  | Remove the pack `NAME` installed under `--out DIR` for `--target` (default `claude`): its plugin directory, its `mcpServers` keys and `CLAUDE.md` block on Claude, its marketplace entry on Codex. Nothing else is touched. An unknown name is an error. |
| `crewspec list`            | List the packs installed under `--out DIR` (default `.`): per pack, the target, name, plugin path, agents, skills, and MCP servers. `--target` limits it to `claude` or `codex`. `--json` prints JSON instead. Writes nothing. |

`SOURCE` is a `.crewfile`, a `crewspec.yaml`, a directory containing one, or a
`github:` shorthand (below). A directory with a `crewspec.yaml` uses that file.
Otherwise it must hold exactly one `*.crewfile`.

`crewspec --help` and `crewspec <command> --help` list every option. `-V`
prints the version.

## Install from GitHub

```bash
crewspec install github:owner/repo[@ref][/subdir]
```

The pack is downloaded over HTTPS as a `tar.gz` from codeload.github.com. No
`git` is needed. `@ref` is a branch, tag, or commit SHA (default: the repo's
default branch). `/subdir` selects a pack inside the repo.

To try it on the example pack in this repo:

```bash
crewspec install github:J-kenned/crewspec/examples/code-review-crew --dry-run
```

The shorthand cannot address a branch name containing `/`; pin a commit SHA
instead.

Limits today: public repositories only, and no lockfile.

## Secrets

String fields in MCP configs may use `${...}` references. Nothing is read from
your environment at install time. The reference is rewritten for the target and
the host expands it when the MCP server starts:

| In the crewfile  | In the emitted server config | Notes |
| ---------------- | ---------------------------- | ----- |
| `${VAR}` or `${VAR:-default}` | unchanged | Claude Code and Codex expand it at runtime. |
| `${secret:NAME}` | `${NAME}` | The secret value never lands on disk. Set `NAME` in the environment that launches the host. |
| `${file:PATH}`   | error on the Claude target | No target mounts files, so the reference could never resolve. Codex still writes it verbatim with a warning. |

## Crewfile 0.2

`version: "0.2"` adds six optional blocks to an **agent**. Each is a boundary,
so each lives on the agent only: no pack-level copy, no inheritance. Share with
YAML anchors. See `examples/night-shift/` for a real one.

| Block | Declares |
| ----- | -------- |
| `triggers` | what wakes the agent: `provider` (`github`, `cron`, `webhook`, `channel`), `type`, `filter`, `cron` or `fire_at`, `prompt`, `claim`, `coalesce`, `deliver_only` |
| `channels` | where it talks: `kind` (`slack`, `github`, `cli`, `push`), `binding`, `approvals_inbox` |
| `sandbox` | `kind` (`none`, `docker`, `vm`), `policy`, `network` (`none`, `broker-only`, `allowlist` + `allow`), `host_access`, `disk_mb`, `leaves`, `mounts` (`path`, `mode: ro|rw`; only with `host_access: host-disk`) |
| `secrets` | name → `ref: vault://…`, `blind` (default `true`), `hosts`, `kind`. For `kind: web-login`, `hosts` is the list of origins the runtime's login tool may fill; fill is refused elsewhere. |
| `limits` | `tokens_per_run` and `wall_clock_s` (both required), `tokens_per_day`, `max_prs_per_night`, `retries`, `deny` |
| `approvals` | `policy`, `ask` (closed list: `send post pay sign delete merge promote`), `timeout_s`, `on_timeout`, `deferred`, `unattended` |

Three rules:

1. **Version gates the blocks.** Any of the six under `version: "0.1"` is an
   error naming the agent and block. `version` itself must be `"0.1"` or `"0.2"`.
2. **One secret grammar.** `${secret:NAME}` stays the only reference form, and
   `secrets:` is the only place a name is declared. In 0.2 every `${secret:NAME}`
   an MCP server uses must be declared by at least one agent, and by every agent
   that can reach that server (an `mcp__<server>__…` tool or a skill whose
   `x-crewspec.mcps` lists it; an agent with no `allowed_tools` reaches every server). Undeclared fails at load with the line. 0.1 keeps
   the passthrough above. `${VAR}` is for non-secrets and is unchanged.
3. **`x-<name>` is the escape hatch.** An agent may carry `x-operative:` (or any
   `x-` key) as an opaque mapping for the runtime that owns it. Every other
   unknown key is still rejected, `inject` included.

Claude Code and Codex have no home for these blocks, so both emitters
**withhold** them: nothing is written, nothing fails. `crewspec validate` lists
the withheld blocks per agent (`withheld` in `--json`).

## Import an existing Claude Code layout

```bash
crewspec init --from-claude ~/some-project --out ./some-project
crewspec validate ./some-project
```

`init` reads `.claude/agents/*.md`, every `SKILL.md` under `.claude/skills/`,
`mcpServers` from `.mcp.json` / `.claude.json`, and `CLAUDE.md`, and writes a
0.2 pack. A crewspec-emitted plugin imports back to the same agents, skills,
deps, and servers. Credentials in MCP config are replaced by `${secret:NAME}`
with a generated `secrets:` block (`vault://<pack>/NAME`, blind) on every agent
that can reach the server; a literal credential is dropped with a warning and
never copied. Not recoverable from a Claude layout: the original `version`,
crewfile comments, `depends_on` edges added beyond a skill's own frontmatter,
and the 0.2 runtime blocks (Claude never received them), so `init` is the start
of a pack, not a lossless export.

## Import an existing Claude Code layout

```bash
crewspec init --from-claude ~/some-project --out ./some-project
crewspec validate ./some-project
```

`init` reads `.claude/agents/*.md`, every `SKILL.md` under `.claude/skills/`,
`mcpServers` from `.mcp.json` / `.claude.json`, and `CLAUDE.md`, and writes a
0.2 pack. A crewspec-emitted plugin imports back to the same agents, skills,
deps, and servers. Credentials in MCP config are replaced by `${secret:NAME}`
with a generated `secrets:` block (`vault://<pack>/NAME`, blind) on every agent
that can reach the server; a literal credential is dropped with a warning and
never copied. Not recoverable from a Claude layout: the original `version`,
crewfile comments, `depends_on` edges added beyond a skill's own frontmatter,
and the 0.2 runtime blocks (Claude never received them), so `init` is the start
of a pack, not a lossless export.

## Design docs

- [crewspec Spec and Design](https://linear.app/kenned/document/crewspec-spec-and-design-959937905db9): the format and CLI.
- [crewspec Product Use Cases and Design Decisions](https://linear.app/kenned/document/crewspec-product-use-cases-and-design-decisions-a766eb8b8d0d): the use cases and the decisions behind V1.

## Not in V1

V1 is the single-agent install to Claude Code and Codex. These gaps are
intentional:

- Composition. `include:` and `crew:` in `crewspec.yaml` are rejected with an error.
- Codex user-scope outputs. The Codex target keeps MCPs in the plugin's
  `.mcp.json` and ignores `instructions`; mirroring the Claude target's
  `.claude.json` and `CLAUDE.md` handling into `~/.codex/AGENTS.md` and
  `[mcp_servers.*]` in `config.toml` is a follow-up.
- `${file:PATH}` secrets. No target mounts files; the Claude target rejects them.
- `crewspec add`. Pull a remote pack into a manifest without emitting.
- Kiro target. The emitter interface is pluggable, so it slots in later.
- `--resolve-ai`. AI-assisted install.
- Lockfile pinning for `github:` sources.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
