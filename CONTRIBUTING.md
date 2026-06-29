# Contributing to crewspec

## Prerequisites

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/) for dependency and environment management

## Setup

```bash
git clone https://github.com/J-kenned/crewspec
cd crewspec

# Create the venv and install runtime + dev dependencies.
uv sync --dev

# Install the git pre-commit hooks
uv run pre-commit install
```

## Quality gates

All of these run in CI (Python 3.12 and 3.13) and must pass before merge:

```bash
uv run ruff check .      # lint + import sort
uv run black --check .   # formatting
uv run mypy src          # static types (strict-leaning on src/crewspec)
uv run pytest            # tests
```

To run every pre-commit hook against the whole tree exactly as CI does:

```bash
uv run pre-commit run --all-files
```

## Conventions

- **Line length is 88** (black + ruff).
- `black`, `ruff`, and `mypy` run through `uv run` in `.pre-commit-config.yaml`,
  so they always use the versions pinned in `uv.lock`. Bump them by editing the
  `dev` dependency group in `pyproject.toml` and re-running `uv sync --dev` —
  there is no separate hook revision to update. (`end-of-file-fixer` and
  `trailing-whitespace` come from `pre-commit-hooks`; refresh those with
  `uv run pre-commit autoupdate`.)
- New code under `src/crewspec` must be fully type-annotated
  (`disallow_untyped_defs`).
