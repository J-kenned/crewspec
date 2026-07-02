---
name: code-review
description: Review a pull request for correctness, style, and risk. Use when reviewing a diff or PR.
x-crewspec:
  skills: [test-analysis]
  mcps: [github]
---

# Code Review

Work the diff in priority order:

1. **Correctness** — logic errors, off-by-one, unhandled errors, edge cases.
2. **Tests** — hand off to the `test-analysis` skill to confirm the change is covered.
3. **Style** — naming, dead code, and consistency with the surrounding file.

See `references/checklist.md` for the full pass. Fetch the diff and post comments
via the `github` MCP.
