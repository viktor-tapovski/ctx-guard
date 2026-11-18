"""
Where each agent keeps its status line, and how ctx-guard sets and restores
it. Used by `install.sh --statusline` and by ctx-guard-uninstall.

To support another agent (Gemini CLI, Codex CLI, ...):
  1. Add a Target to TARGETS with its config file and format.
  2. If its config isn't a JSON file with a Claude-style
     `"statusLine": {"type": "command", "command": ...}` object, write a
     class with the same methods as JsonStatusLine and register it in
     FORMATS. Codex's config.toml, for example, would need one.
  3. Set `only_if_installed` to the agent's config dir, so the installer
     never creates config for an agent the user doesn't have.
ctx-guard-statusline takes the agent name as its argument and needs no change.

The manifest keeps, per agent: the config file, the format, the command we
set, and the status line that was there before (chained by
ctx-guard-statusline and put back on uninstall).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
from typing import Any, NamedTuple


class Target(NamedTuple):
    agent: str               # CTX_GUARD_AGENT value, also the stats tag
    config: str              # config file, relative to $HOME
    format: str = "json"     # key into FORMATS
    backup: bool = False     # copy the config to <config>.bak before editing
    only_if_installed: str | None = None  # skip unless this dir (relative to $HOME) exists


TARGETS = [
    Target("claude-code", ".claude/settings.json"),
    # Claude's settings.json.bak is written by the hook install step already.
    Target("copilot-cli", ".copilot/settings.json", backup=True),
]


class JsonStatusLine:
    """A JSON config with a top-level `statusLine` command object, as used by
    Claude Code and Copilot CLI."""

    def load(self, path: str) -> dict:
        """Raises ValueError when the file isn't a plain JSON object."""
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
        return doc

    def current(self, doc: dict) -> Any:
        return doc.get("statusLine")

    def command_of(self, line: Any) -> str:
        return str(line.get("command", "")) if isinstance(line, dict) else ""

    def set(self, doc: dict, command: str, previous: Any) -> None:
        line = {"type": "command", "command": command}
        if isinstance(previous, dict) and "padding" in previous:
            line["padding"] = previous["padding"]
        doc["statusLine"] = line

    def restore(self, doc: dict, previous: Any) -> None:
        if previous:
            doc["statusLine"] = previous
        else:
            doc.pop("statusLine", None)

    def save(self, path: str, doc: dict) -> None:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        temporary = path + ".ctx-guard.tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(temporary, path)


FORMATS = {"json": JsonStatusLine()}


def install(manifest: dict, home: str, dest: str, python_bin: str,
            selected: list[str] | None = None) -> None:
    """Point every installed agent's status line at ctx-guard-statusline,
    recording what was there before. Re-running never records our own
    command as the user's. selected limits it to the agents the user chose."""
    entries = manifest.setdefault("statusline", {})
    script = os.path.join(dest, "bin", "ctx-guard-statusline")
    for target in TARGETS:
        if selected is not None and target.agent not in selected:
            continue
        if target.only_if_installed and not os.path.isdir(
                os.path.join(home, target.only_if_installed)):
            continue
        fmt = FORMATS[target.format]
        path = os.path.join(home, target.config)
        try:
            doc = fmt.load(path)
        except ValueError:
            print(f"Skipping status line for {target.agent}: {path} could not be parsed")
            continue
        if target.backup and os.path.isfile(path):
            shutil.copy(path, path + ".bak")
        command = f"{shlex.quote(python_bin)} {shlex.quote(script)} {target.agent}"
        entry = entries.get(target.agent, {})
        current = fmt.current(doc)
        if script not in fmt.command_of(current):
            entry["previous"] = current
        fmt.set(doc, command, entry.get("previous"))
        fmt.save(path, doc)
        entry.update(settings_file=path, format=target.format, command=command)
        entries[target.agent] = entry
        print(f"{target.agent} status line set in {path}")


def restore(manifest: dict, selected: list[str] | None = None) -> None:
    """Put back each agent's original status line, unless the user has
    changed it since install. selected limits it to those agents."""
    for agent, entry in list(manifest.get("statusline", {}).items()):
        if selected is not None and agent not in selected:
            continue
        manifest["statusline"].pop(agent)
        fmt = FORMATS.get(entry.get("format", "json"))
        path = entry["settings_file"]
        if fmt is None or not os.path.isfile(path):
            continue
        try:
            doc = fmt.load(path)
        except ValueError:
            print(f"Preserving unreadable {agent} settings: {path}")
            continue
        if fmt.command_of(fmt.current(doc)) != entry.get("command"):
            print(f"Preserving changed {agent} status line in {path}")
            continue
        fmt.restore(doc, entry.get("previous"))
        fmt.save(path, doc)
        print(f"Restored {agent} status line in {path}")


def _calls_segment(command: str) -> bool:
    """Whether command runs ctx-guard-statusline itself or through a script it
    names (a statusline.sh calling `ctx-guard-statusline <agent> --segment`)."""
    if "ctx-guard-statusline" in command:
        return True
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for word in words:
        path = os.path.expanduser(word)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                if b"ctx-guard-statusline" in f.read(1024 * 1024):
                    return True
        except OSError:
            continue
    return False


def drifted(manifest: dict) -> list[tuple[str, str]]:
    """(agent, settings file) for each status line ctx-guard set that another
    tool has since replaced with one that doesn't call ctx-guard-statusline.
    Empty unless the user opted in with --statusline."""
    found = []
    for agent, entry in (manifest.get("statusline") or {}).items():
        fmt = FORMATS.get(entry.get("format", "json"))
        path = entry.get("settings_file", "")
        if fmt is None or not os.path.isfile(path):
            continue
        try:
            command = fmt.command_of(fmt.current(fmt.load(path)))
        except (OSError, ValueError):
            continue
        if not _calls_segment(command):
            found.append((agent, path))
    return found
