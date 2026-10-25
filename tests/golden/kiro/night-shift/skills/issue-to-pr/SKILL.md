---
name: issue-to-pr
description: Convert a single GitHub issue into a minimal, test-first draft pull request. Use when an agent has claimed an issue and must produce a PR with a result package.
x-crewspec:
  mcps: [github]
---

# Issue to PR

1. Read the issue and every linked issue or PR. Restate the acceptance criteria
   in one sentence before writing code.
2. Locate the code path. Trace it end to end; do not patch the first file you find.
3. Write the failing test first. Run it. Paste the failure into your notes.
4. Make the smallest change that passes. Run the full relevant test file.
5. Open the PR as a **draft** via the `github` MCP with the result-package body:
   done, verified (with output), **not claimed**, risks, first thing to review.
6. If anything in steps 1–5 cannot be completed, stop and report rather than guess.

Never widen scope beyond the issue. Never touch CI, secrets, or dependency pins
unless the issue is about them.
