# crewspec

> Declarative spec and runtime for composable AI agents and teams.

`crewspec` lets you describe an agent crew declaratively and install and manage
its definitions from a single CLI. It resolves agent packs, builds an
intermediate representation, and emits target-specific artifacts.

> **Status:** pre-1.0, under active development. `install` is the first
> end-to-end command; the remaining subcommands are still in progress.

## Install

```bash
pip install crewspec
```

## Quickstart

Install a crewfile (or a directory containing one) into a target runtime. The
default target is `claude`, which emits a self-contained Claude Code plugin under
`<out>/.claude/skills/crewspec/`. The repo ships a runnable example — a
one-agent code-review crew whose two skills share a `github` MCP:

```bash
crewspec install ./examples/code-review-crew --target claude --out .
```

See [`examples/code-review-crew`](examples/code-review-crew) for the full pack:
a `.crewfile`, an agent persona, and skills whose dependencies are declared in
their `SKILL.md` frontmatter.

Use `--dry-run` to parse and resolve without writing any artifacts:

```bash
crewspec install ./examples/code-review-crew --dry-run
```

Run `crewspec --help` or `crewspec install --help` for the full option list.

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

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the local dev setup, pre-commit
hooks, and how to run the test/type/lint gates.

## License

MIT
