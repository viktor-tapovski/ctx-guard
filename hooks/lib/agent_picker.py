"""
Arrow-key checklist for choosing which agents install.sh sets up.

Keys come from /dev/tty rather than stdin, so it works under
`curl ... | bash`, where stdin is the script. The checklist is drawn on
/dev/tty too, and only the chosen ids go to stdout, comma-separated:

  agent_picker.py [--preselect id,id]     exit 0: chosen ids on stdout
                                          exit 130: cancelled
                                          exit 2: no terminal to ask on

step() and render() are pure, so the key handling is tested without a
terminal; run() is the only part that touches one.
"""
from __future__ import annotations

import os
import select
import sys
from typing import NamedTuple

import agents

UP = {"\x1b[A", "\x1bOA", "k"}
DOWN = {"\x1b[B", "\x1bOB", "j"}
TOGGLE = {" "}
ALL = {"a"}
CONFIRM = {"\r", "\n"}
CANCEL = {"q", "\x1b", "\x03"}


class Row(NamedTuple):
    id: str
    label: str
    found: bool


class State(NamedTuple):
    cursor: int = 0
    selected: frozenset = frozenset()
    done: bool = False
    cancelled: bool = False
    message: str = ""


def step(state: State, key: str, count: int) -> State:
    """Apply one keypress to the checklist state."""
    state = state._replace(message="")
    if key in UP:
        return state._replace(cursor=(state.cursor - 1) % count)
    if key in DOWN:
        return state._replace(cursor=(state.cursor + 1) % count)
    if key in TOGGLE:
        return state._replace(selected=state.selected ^ {state.cursor})
    if key in ALL:
        everything = frozenset(range(count))
        return state._replace(selected=frozenset() if state.selected == everything else everything)
    if key in CONFIRM:
        if not state.selected:
            return state._replace(message="Select at least one agent (space), or press q to cancel.")
        return state._replace(done=True)
    if key in CANCEL:
        return state._replace(cancelled=True)
    return state


def render(state: State, rows: list[Row]) -> list[str]:
    """The checklist as lines of text; the cursor row is marked with '>'."""
    width = max(len(row.label) for row in rows)
    lines = [
        "Set up ctx-guard for which agents?",
        "  up/down move   space select   a all   enter continue   q cancel",
        "",
    ]
    for index, row in enumerate(rows):
        pointer = ">" if index == state.cursor else " "
        box = "[x]" if index in state.selected else "[ ]"
        status = "detected" if row.found else "not found"
        lines.append(f"{pointer} {box} {row.label.ljust(width)}   {status}")
    lines.append(state.message)
    return lines


def initial_state(rows: list[Row], preselect: list[str]) -> State:
    chosen = {i for i, row in enumerate(rows) if row.id in preselect}
    return State(selected=frozenset(chosen))


def _read_key(fd: int) -> str:
    data = os.read(fd, 1)
    if data == b"\x1b":
        # An arrow key arrives as ESC [ A in one burst; a lone ESC is cancel.
        if select.select([fd], [], [], 0.05)[0]:
            data += os.read(fd, 2)
    return data.decode("utf-8", errors="replace")


def run(rows: list[Row], preselect: list[str]) -> list[str] | None:
    """Show the checklist on /dev/tty; return chosen ids, or None if cancelled."""
    import termios
    import tty

    state = initial_state(rows, preselect)
    with open("/dev/tty", "r+b", buffering=0) as term:
        fd = term.fileno()
        saved = termios.tcgetattr(fd)
        drawn = 0

        def draw() -> None:
            nonlocal drawn
            out = ""
            if drawn:
                out += f"\x1b[{drawn}A"  # back to the top of the last drawing
            lines = render(state, rows)
            out += "".join(f"\r\x1b[2K{line}\r\n" for line in lines)
            term.write(out.encode("utf-8"))
            drawn = len(lines)

        try:
            tty.setcbreak(fd)
            term.write(b"\x1b[?25l")  # hide the cursor while drawing
            draw()
            while not (state.done or state.cancelled):
                try:
                    key = _read_key(fd)
                except KeyboardInterrupt:
                    key = "\x03"
                state = step(state, key, len(rows))
                draw()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            term.write(b"\x1b[?25h")
    if state.cancelled:
        return None
    return [row.id for i, row in enumerate(rows) if i in state.selected]


def main(argv: list[str]) -> int:
    preselect: list[str] = []
    if argv[:1] == ["--preselect"] and len(argv) == 2:
        preselect = [a for a in argv[1].split(",") if a]
    elif argv:
        print("usage: agent_picker.py [--preselect id,id]", file=sys.stderr)
        return 2
    home = os.path.expanduser("~")
    rows = [Row(agent.id, agent.label, agent.detect(home)) for agent in agents.AGENTS]
    try:
        chosen = run(rows, preselect)
    except OSError:
        print("ctx-guard: no terminal to ask on; pass --agents", file=sys.stderr)
        return 2
    if chosen is None:
        print("Cancelled.", file=sys.stderr)
        return 130
    print(",".join(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
