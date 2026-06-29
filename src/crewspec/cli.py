"""crewspec CLI for managing agent definitions."""

import sys

COMMANDS = ["install", "list"]

HELP = """crewspec — declarative AI agent crews

Usage:
  crewspec install <pack>              Install an agent pack
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
