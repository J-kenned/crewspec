# crewspec

> Declarative spec and runtime for composable AI agents and teams.

`crewspec` lets you describe an agent crew declaratively and install and manage
its definitions from a single CLI. It resolves agent packs, builds an
intermediate representation, and emits target-specific artifacts.

> **Status:** pre-1.0, under active development. The CLI surface below is the
> intended shape; most subcommands are not done.

## Install

```bash
pip install crewspec
```

## Quickstart

```bash
crewspec --help
```

```
Usage:
  crewspec install <pack>              Install an agent pack
  crewspec list                        List installed packs
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the local dev setup, pre-commit
hooks, and how to run the test/type/lint gates.

## License

MIT
