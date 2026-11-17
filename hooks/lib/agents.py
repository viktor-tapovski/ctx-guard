"""
The agents ctx-guard can set itself up for, and how to tell which are on
this machine. Read by install.sh, the agent picker, agent_hooks.py and
ctx-guard-uninstall, so adding an agent starts here.

As a script, for install.sh:
  agents.py detect          comma-separated ids of the agents found
  agents.py parse <list>    validate a comma-separated list ("all" for every
                            agent) and print it normalized; exit 2 if invalid
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import Callable, NamedTuple


class Agent(NamedTuple):
    id: str                          # CTX_GUARD_AGENT value, also the stats tag
    label: str                       # shown in the picker and in messages
    detect: Callable[[str], bool]    # home -> installed on this machine


def _dir_or_command(rel_dir: str, command: str) -> Callable[[str], bool]:
    return lambda home: os.path.isdir(os.path.join(home, rel_dir)) or shutil.which(command) is not None


def codex_home(home: str) -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")


AGENTS = [
    Agent("claude-code", "Claude Code", _dir_or_command(".claude", "claude")),
    Agent("copilot-cli", "GitHub Copilot CLI", _dir_or_command(".copilot", "copilot")),
    # ~/.gemini/antigravity-cli appears once agy has run; Gemini CLI alone doesn't create it.
    Agent("antigravity-cli", "Antigravity CLI", _dir_or_command(os.path.join(".gemini", "antigravity-cli"), "agy")),
    Agent("codex-cli", "Codex CLI",
          lambda home: os.path.isdir(codex_home(home)) or shutil.which("codex") is not None),
]
IDS = [agent.id for agent in AGENTS]


def get(agent_id: str) -> Agent:
    for agent in AGENTS:
        if agent.id == agent_id:
            return agent
    raise KeyError(agent_id)


def detected(home: str) -> list[str]:
    return [agent.id for agent in AGENTS if agent.detect(home)]


def parse(text: str) -> list[str]:
    """Comma-separated ids to a de-duplicated list in registry order."""
    names = {part.strip() for part in text.split(",") if part.strip()}
    if names == {"all"}:
        return list(IDS)
    unknown = sorted(names - set(IDS))
    if unknown:
        raise ValueError(f"unknown agent {', '.join(unknown)}; choose from {', '.join(IDS)} or all")
    if not names:
        raise ValueError(f"no agents given; choose from {', '.join(IDS)} or all")
    return [agent_id for agent_id in IDS if agent_id in names]


def main(argv: list[str]) -> int:
    if argv[:1] == ["detect"] and len(argv) == 1:
        print(",".join(detected(os.path.expanduser("~"))))
        return 0
    if argv[:1] == ["parse"] and len(argv) == 2:
        try:
            print(",".join(parse(argv[1])))
        except ValueError as e:
            print(f"ctx-guard: {e}", file=sys.stderr)
            return 2
        return 0
    print("usage: agents.py detect | agents.py parse <id,id,...>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
