"""crewspec CLI install, run and manage agent teams."""

import sys


COMMANDS = ["install", "run", "up", "down", "status", "list"]

HELP = """crewspec — declarative AI agent crews

Usage:
  crewspec install <pack>              Install an agent pack
  crewspec run <agent>                 Run a single agent
  crewspec up                          Start all agents in crewspec.yaml
  crewspec down                        Stop all running agents
  crewspec status                      Show running agents and their state
  crewspec list                        List installed packs

Options:
  --claude    Resolve and install the pack via Claude
  --help      Show this message
"""


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print(HELP)
        return

    command = args[0]

    if command not in COMMANDS:
        print(f"crewspec: unknown command '{command}'\n")
        print(HELP)
        sys.exit(1)

    print(f"crewspec {command}: not yet implemented")
    sys.exit(1)
