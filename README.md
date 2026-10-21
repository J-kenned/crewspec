# crewspec

Declare an AI agent crew once. Compile it into the native plugin format of
Claude Code or Codex.

A crewfile names agents, skills, and MCP servers and the dependencies between
them: agent to skill, skill to skill, skill to MCP. `SKILL.md` alone cannot say
"this skill needs the `github` MCP". crewspec records that, follows the
dependencies, removes duplicates, rejects conflicts, and writes each target's
wiring. It defines agents. It does not run them.

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

`--target claude` writes a Claude Code plugin to `<out>/.claude/skills/crewspec/`
containing `.claude-plugin/plugin.json`, `.mcp.json`, `agents/`, and `skills/`.

`--target codex` writes a Codex plugin to `<out>/plugins/crewspec/` and adds an
entry to `<out>/.agents/plugins/marketplace.json`. Codex plugins have no agents
directory, so each agent becomes a skill under `skills/<agent>/SKILL.md`.

crewspec owns those plugin directories and rewrites them on every install. The
Codex marketplace file is merged, not replaced.

To check a pack without writing anything:

```bash
crewspec install ./examples/code-review-crew --dry-run
crewspec validate ./examples/code-review-crew
```

See [`examples/code-review-crew`](examples/code-review-crew) for the full
layout: a `.crewfile`, an agent persona, and skills that declare their own
dependencies in `SKILL.md` frontmatter.

## The file model

One schema, one parser, two file names:

| File            | Role |
| --------------- | ---- |
| `*.crewfile`    | One agent or pack. Points at persona and skill files, lists dependency edges, and holds MCP launch configs. Persona and skill content lives in its own files. |
| `crewspec.yaml` | The same schema. It will gain `include:` and `crew:` for composing several crewfiles; until composition lands, both keys are rejected with an error (see [Not in V1](#not-in-v1)). |

Content stays in its native form. Skill bodies are `SKILL.md` directories (the
Agent Skills format), personas are markdown, and MCP servers are launch
configs. The crewfile only references and wires them.

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
| `crewspec validate SOURCE` | Parse and resolve, then print the resolved plan: each agent's tools, skills, and MCPs, the deduplicated MCP list, and any warnings. `--json` prints JSON instead. Writes nothing. |
| `crewspec list`            | List the packs installed under `--out DIR` (default `.`): per target, the plugin path, agents, skills, and MCP servers. `--target` limits it to `claude` or `codex`. `--json` prints JSON instead. Writes nothing. |

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

| In the crewfile  | In the emitted `.mcp.json` | Notes |
| ---------------- | -------------------------- | ----- |
| `${VAR}` or `${VAR:-default}` | unchanged | Claude Code and Codex expand it at runtime. |
| `${secret:NAME}` | `${NAME}` | The secret value never lands on disk. Set `NAME` in the environment that launches the host. |
| `${file:PATH}`   | unchanged, with a warning | Neither target mounts files yet. |

## Design docs

- [crewspec Spec and Design](https://linear.app/kenned/document/crewspec-spec-and-design-959937905db9): the format and CLI.
- [crewspec Product Use Cases and Design Decisions](https://linear.app/kenned/document/crewspec-product-use-cases-and-design-decisions-a766eb8b8d0d): the use cases and the decisions behind V1.

## Not in V1

V1 is the single-agent install to Claude Code and Codex. These gaps are
intentional:

- Composition. `include:` and `crew:` in `crewspec.yaml` are rejected with an error.
- `crewspec add`. Pull a remote pack into a manifest without emitting.
- Kiro target. The emitter interface is pluggable, so it slots in later.
- `--resolve-ai`. AI-assisted install.
- Lockfile pinning for `github:` sources.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
