---
name: test-analysis
description: Assess whether a change is adequately tested and surface coverage gaps.
x-crewspec:
  mcps: [github]
---

# Test Analysis

Given a diff:

1. List the behaviours the change introduces or alters.
2. Map each behaviour to a test that exercises it; flag any with no covering test.
3. Call out missing edge-case and error-path coverage.

Use the `github` MCP to read the PR's changed files and its existing test suite.
