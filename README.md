# crewspec

> Declarative spec and transpiler for portable, composable AI agents.

`crewspec` describes an agent crew declaratively and compiles it into a target
tool's native format from a single CLI. It **defines** agents, skills, and their
MCP dependencies — it does not run them. It resolves a pack into a
target-agnostic intermediate representation and emits target-specific artifacts
(Claude Code and Codex today; other targets later).

The value is the manifest: native formats like `SKILL.md` can't express
cross-capability dependencies (a skill has no way to say *"I need the `github`
MCP"*). crewspec records those edges — agent→skill, skill→skill, skill→MCP —
then resolves them transitively, deduplicates, detects conflicts, and emits each
target's native wiring.

> **Status:** pre-1.0, under active development. `install` and `validate` are
> the first end-to-end commands; composition and remote packs are still to come
> (see [Not in V1](#not-in-v1)).

## Install

```bash
pip install crewspec
```

## Quickstart

Install a crewfile (or a directory containing one) for a target. The default
target is `claude`, which emits a self-contained Claude Code plugin under
`<out>/.claude/skills/crewspec/`. The repo also supports `--target codex`, which
emits a repo-local Codex plugin under `<out>/plugins/crewspec/` and a local
marketplace entry at `<out>/.agents/plugins/marketplace.json`. The repo ships a
runnable example — a one-agent code-review crew whose two skills share a
`github` MCP:

```bash
crewspec install ./examples/code-review-crew --target claude --out .
crewspec install ./examples/code-review-crew --target codex --out .
```

See [`examples/code-review-crew`](examples/code-review-crew) for the full pack:
a `.crewfile`, an agent persona, and skills whose dependencies are declared in
their `SKILL.md` frontmatter.

Use `--dry-run` to parse and resolve without writing any artifacts, or
`validate` to print the resolved plan:

```bash
crewspec install ./examples/code-review-crew --dry-run
crewspec validate ./examples/code-review-crew
```

## The file model

crewspec has **one schema and one parser**, used in two conventional files:

| File           | Role                                                                                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `*.crewfile`   | The portable **unit** — one agent or pack. A thin manifest: persona and skill *pointers*, dependency edges, and inline MCP launch configs. No content lives here.           |
| `crewspec.yaml`| The **composition** superset — the same vocabulary plus `include:` and `crew:` to templatise a whole multi-agent setup. *(0.2 — see [Not in V1](#not-in-v1))*               |

crewspec is a *thin manifest* that rides open standards: skill bodies live in
their own `SKILL.md` directories (the Agent Skills standard), personas in plain
markdown, and MCP servers as launch configs. The manifest only **references and
wires** them. A skill declares its own dependencies in its
`SKILL.md` frontmatter under an `x-crewspec:` key that other tools ignore, so the
skill stays portable across crews:

```yaml
# skills/code-review/SKILL.md frontmatter
x-crewspec:
  skills: [test-analysis]
  mcps: [github]
```

## CLI

| Command                    | What it does                                                                                                                                     |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `crewspec install SOURCE`  | Parse, resolve, and emit artifacts for `--target` (default `claude`) into `--out` (default `.`). `--dry-run` stops before writing.                |
| `crewspec validate SOURCE` | Parse and resolve only; print the resolved plan — per-agent capabilities, the deduplicated MCP set, and any warnings. `--json` for scripts. Writes nothing. |
| `crewspec list`            | List installed packs. *(not yet implemented)*                                                                                                    |

`SOURCE` is a local `.crewfile` / `crewspec.yaml` (or a directory containing
one) or a `github:` shorthand (below). Run `crewspec --help` or
`crewspec <command> --help` for the full option list.

## Install from GitHub

`SOURCE` may also be a GitHub shorthand instead of a local path:

```bash
crewspec install github:owner/repo[@ref][/subdir]
# e.g. install the example pack straight from this repo:
crewspec install github:J-kenned/crewspec@main/examples/code-review-crew
```

The pack is fetched over HTTPS as a [codeload](https://codeload.github.com)
`tar.gz` and run through the same parse → resolve → emit pipeline as a local
install — there is **no `git` dependency** and no clone. `@ref` pins a branch,
tag, or commit SHA (default: the repo's default branch); an optional trailing
`/subdir` selects a pack nested inside the repo.

Notes and current limits:

- The ref is a single path segment, so branch names containing `/` (e.g.
  `feature/x`) cannot be addressed via the shorthand yet. Commit-SHA / lockfile
  pinning is planned for 0.2.
- Only **public** repositories are supported; private-repo authentication is
  future work.

## Secrets

String fields may carry `${...}` references. Plain `${VAR}` references are
**inlined** from the environment at resolve time — fine for non-sensitive
config. Two reference kinds keep sensitive values *out* of the spec and the
generated artifact entirely:

| Reference        | Backed by                     | Delivery at runtime                     |
| ---------------- | ----------------------------- | --------------------------------------- |
| `${file:PATH}`   | a readable, owner-only file   | launcher **mounts** the file at `PATH`  |
| `${secret:NAME}` | a dedicated secret env, `NAME`| launcher **injects** `NAME` as an env var |

Resolution validates the backing secret (missing, unreadable, or
group/other-readable files are errors) and substitutes a runtime *pointer* —
the value itself is never written into the resolved spec or artifact. Networked
secret stores (Vault, cloud managers) are a later affordance.

## Design & rationale

Two companion design docs back this repo:

- **[crewspec — Spec & Design](https://linear.app/kenned/document/crewspec-spec-and-design-959937905db9)**
  — the format and CLI spec.
- **[crewspec — Product Use Cases & Design Decisions](https://linear.app/kenned/document/crewspec-product-use-cases-and-design-decisions-a766eb8b8d0d)**
  — the use cases (UC1–UC3) and the locked design decisions and rationale behind
  V1.

## Not in V1

V1 is the single-agent install to Claude Code (UC1). Deliberately out of scope
for now, so contributors know what's intentional rather than missing:

- **Composition** — the `crewspec.yaml` `include:` / `crew:` superset that
  composes multiple crewfiles into one deduplicated setup *(0.2)*.
- **`crewspec add`** — pull a remote pack into the manifest; distinct from
  `install`, which emits artifacts *(0.2)*.
- **Kiro emitter** — another `--target`; the emitter interface is already
  pluggable so it slots in without touching the pipeline *(0.2)*.
- **`--resolve-ai`** — natural-language, AI-assisted install; a separate feature
  from `--target`, not the overloaded `--claude` of earlier drafts *(0.2.x)*.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the local dev setup, pre-commit
hooks, and how to run the test/type/lint gates.

## License

MIT
