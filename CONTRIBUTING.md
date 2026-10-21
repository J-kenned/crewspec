# Contributing to crewspec

## Prerequisites

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
git clone https://github.com/J-kenned/crewspec
cd crewspec
uv sync --dev              # venv plus runtime and dev dependencies
uv run pre-commit install  # git hooks
```

## Quality gates

CI runs these on Python 3.12 and 3.13. All must pass before merge:

```bash
uv run ruff check .      # lint and import order
uv run black --check .   # formatting
uv run mypy src          # types
uv run pytest            # tests
```

To run every pre-commit hook on the whole tree as CI does:

```bash
uv run pre-commit run --all-files
```

## Layout

`src/crewspec` is a three-stage pipeline (`pipeline.py`):

1. `parser/` loads a crewfile and each skill's `SKILL.md` frontmatter into a
   node registry.
2. `resolver/` turns the registry into the `ir` module's `ResolvedIR`. The IR
   is the resolved plan of agents, skills, and MCPs that every emitter reads.
   It carries no target-specific detail.
3. `emitters/` write target files from the `ResolvedIR`. `claude.py` and
   `codex.py` are the two targets. To add one, subclass `Emitter` in `base.py`
   and register it in `EMITTERS` in `emitters/__init__.py`.

`sources/` fetches `github:` packs. `plan.py` renders `validate` output.

## Golden fixtures

`tests/test_golden_e2e.py` installs `examples/code-review-crew` and compares the
emitted Claude plugin byte for byte with `tests/golden/code-review-crew`. After
an intentional change to the emitted format, regenerate the golden and review
the diff before committing:

```bash
UPDATE_GOLDEN=1 uv run pytest tests/test_golden_e2e.py
```

Without `UPDATE_GOLDEN` the test only compares, so any diff is a failure.

## Conventions

- Line length is 88 (black and ruff).
- The ruff, black, and mypy hooks in `.pre-commit-config.yaml` run through
  `uv run`, so they use the versions in `uv.lock`. To bump them, edit the `dev`
  group in `pyproject.toml` and run `uv sync --dev`. `end-of-file-fixer` and
  `trailing-whitespace` come from `pre-commit-hooks`. Refresh those with
  `uv run pre-commit autoupdate`.
- Code under `src/crewspec` must be fully type annotated
  (`disallow_untyped_defs`).
