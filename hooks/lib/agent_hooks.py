"""
How ctx-guard adds its hooks to agents whose hook file it shares with the
user's own hooks, and takes them out again. Used by install.sh and by
ctx-guard-uninstall. (Claude Code and Copilot CLI are wired in install.sh.)

To support another agent, add a Target to TARGETS: where its hooks file
lives, how to tell it's installed, and how to add and remove ctx-guard's
entries in the parsed JSON. Everything else in the file is left as it was.

The manifest keeps, per agent, the file's exact text from before install.
Uninstall writes it back byte for byte when the file still holds what
install wrote, and otherwise removes only ctx-guard's entries, keeping the
user's later edits.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
from typing import Callable, NamedTuple


def hook_command(agent: str, dest: str, python_bin: str, script: str) -> str:
    path = os.path.join(dest, "hooks", script)
    return f"CTX_GUARD_AGENT={agent} {shlex.quote(python_bin)} {shlex.quote(path)}"


class Target(NamedTuple):
    agent: str                                  # CTX_GUARD_AGENT value, also the manifest key
    config: Callable[[str], str]                # home -> hooks file path
    is_installed: Callable[[str], bool]         # home -> agent present on this machine
    add: Callable[[dict, str, str], None]       # (config, dest, python_bin), in place
    remove: Callable[[dict], bool]              # config, in place; True if anything was removed


# --- Antigravity CLI (agy) ---------------------------------------------------
# ~/.gemini/config/hooks.json is an object of named hooks, each mapping event
# names to matcher groups. agy's PostToolUse has no tool output and no
# context field, so only the preToolUse rewrite and package guard run.

ANTIGRAVITY = "antigravity-cli"
ANTIGRAVITY_HOOK_NAME = "ctx-guard"


def _antigravity_add(config: dict, dest: str, python_bin: str) -> None:
    command = hook_command(ANTIGRAVITY, dest, python_bin, "pre_bash_rewrite.py")
    config[ANTIGRAVITY_HOOK_NAME] = {
        "PreToolUse": [
            {
                "matcher": "run_command",
                "hooks": [{"type": "command", "command": command, "timeout": 15}],
            }
        ]
    }


def _antigravity_remove(config: dict) -> bool:
    return config.pop(ANTIGRAVITY_HOOK_NAME, None) is not None


# --- Codex CLI ------------------------------------------------------------------
# $CODEX_HOME/hooks.json (default ~/.codex) holds {"hooks": {event: [groups]}},
# Claude's shape. Codex's PreToolUse accepts no "ask" and its PostToolUse
# output shape for Bash is undocumented, so only the preToolUse hook runs.

CODEX = "codex-cli"


def _codex_home(home: str) -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")


def _codex_command_is_ours(command: object) -> bool:
    return isinstance(command, str) and command.startswith(f"CTX_GUARD_AGENT={CODEX} ")


def _codex_add(config: dict, dest: str, python_bin: str) -> None:
    _codex_remove(config)  # replace any earlier ctx-guard entry, e.g. from another dest
    hooks = config.setdefault("hooks", {})
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "^Bash$",
        "hooks": [{
            "type": "command",
            "command": hook_command(CODEX, dest, python_bin, "pre_bash_rewrite.py"),
            "timeout": 15,
            "statusMessage": "ctx-guard: checking command",
        }],
    })


def _codex_remove(config: dict) -> bool:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False
    removed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if not (
            isinstance(g, dict)
            and any(_codex_command_is_ours(h.get("command")) for h in g.get("hooks", []) if isinstance(h, dict))
        )]
        if len(kept) != len(groups):
            removed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    return removed


TARGETS = [
    Target(
        ANTIGRAVITY,
        config=lambda home: os.path.join(home, ".gemini", "config", "hooks.json"),
        # ~/.gemini/antigravity-cli appears once agy has run; Gemini CLI alone doesn't create it.
        is_installed=lambda home: os.path.isdir(os.path.join(home, ".gemini", "antigravity-cli"))
        or shutil.which("agy") is not None,
        add=_antigravity_add,
        remove=_antigravity_remove,
    ),
    Target(
        CODEX,
        config=lambda home: os.path.join(_codex_home(home), "hooks.json"),
        is_installed=lambda home: os.path.isdir(_codex_home(home)) or shutil.which("codex") is not None,
        add=_codex_add,
        remove=_codex_remove,
    ),
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path: str, text: str) -> None:
    temporary = path + ".ctx-guard.tmp"
    with open(temporary, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(temporary, path)


def install_target(target: Target, manifest: dict, home: str, dest: str, python_bin: str) -> None:
    if not target.is_installed(home):
        return
    hook_files = manifest.setdefault("hook_files", {})
    path = target.config(home)
    current = _read(path)
    previous = hook_files.get(target.agent) or {}

    if current is not None and previous.get("installed_sha256") == _sha256(current):
        # Reinstall over our own unchanged write: keep the true original.
        original = previous.get("original")
        restorable = previous.get("restorable", False)
    elif previous:
        # The user edited the file since the last install, so the saved
        # original no longer applies; uninstall will only remove our entries.
        original, restorable = None, False
    else:
        try:
            original = current.decode("utf-8") if current is not None else None
        except UnicodeDecodeError:
            print(f"{target.agent}: {path} is not UTF-8; leaving it unchanged.")
            return
        restorable = True

    try:
        config = json.loads(current) if current else {}
    except ValueError:
        print(f"{target.agent}: {path} is not valid JSON; leaving it unchanged.")
        return
    if not isinstance(config, dict):
        print(f"{target.agent}: {path} is not a JSON object; leaving it unchanged.")
        return

    config_dir = os.path.dirname(path)
    created_dir = None if os.path.isdir(config_dir) else config_dir
    os.makedirs(config_dir, exist_ok=True)
    target.add(config, dest, python_bin)
    text = json.dumps(config, indent=2) + "\n"
    _write(path, text)

    hook_files[target.agent] = {
        "path": path,
        "original": original,
        "restorable": restorable,
        "created_dir": created_dir or previous.get("created_dir"),
        "installed_sha256": _sha256(text.encode("utf-8")),
    }
    print(f"{target.agent} hooks written to {path}")


def restore_target(target: Target, manifest: dict) -> None:
    entry = manifest.get("hook_files", {}).get(target.agent)
    if not entry:
        return
    path = entry["path"]
    current = _read(path)
    if current is None:
        print(f"No {target.agent} hooks file found, skipping.")
        return

    if entry.get("restorable") and _sha256(current) == entry.get("installed_sha256"):
        if entry.get("original") is None:
            os.remove(path)
            print(f"Removed {path}")
        else:
            _write(path, entry["original"])
            print(f"Restored {path}")
    else:
        try:
            config = json.loads(current)
        except ValueError:
            print(f"Preserving unreadable {target.agent} hooks file: {path}")
            return
        if not isinstance(config, dict) or not target.remove(config):
            print(f"No ctx-guard hooks found in {path}")
            return
        _write(path, json.dumps(config, indent=2) + "\n")
        print(f"Removed the ctx-guard hooks from {path}")

    created_dir = entry.get("created_dir")
    if created_dir:
        try:
            os.rmdir(created_dir)  # only if still empty
        except OSError:
            pass


def install(manifest: dict, home: str, dest: str, python_bin: str) -> None:
    for target in TARGETS:
        install_target(target, manifest, home, dest, python_bin)


def restore(manifest: dict) -> None:
    for target in TARGETS:
        restore_target(target, manifest)
