"""
How ctx-guard adds its hook to Antigravity CLI (`agy`) and takes it out
again. Used by install.sh and by ctx-guard-uninstall.

agy reads global hooks from ~/.gemini/config/hooks.json, a JSON object of
named hooks, each mapping event names to matcher groups. Other tools keep
their own named hooks in the same file, so ctx-guard only ever adds or
removes its own key and leaves everything else as it was.

The manifest keeps the file's exact text from before install. Uninstall
writes it back byte for byte when the file still holds what install wrote,
and otherwise removes only the ctx-guard key, keeping the user's later edits.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil

AGENT = "antigravity-cli"
HOOK_NAME = "ctx-guard"
CONFIG = os.path.join(".gemini", "config", "hooks.json")
# Present once agy has run on this machine; Gemini CLI alone doesn't create it.
INSTALLED_MARKER = os.path.join(".gemini", "antigravity-cli")


def is_installed(home: str) -> bool:
    return os.path.isdir(os.path.join(home, INSTALLED_MARKER)) or shutil.which("agy") is not None


def hook_entry(dest: str, python_bin: str) -> dict:
    """agy has no PostToolUse context field and its PostToolUse payload has
    no tool output, so only the preToolUse rewrite and package guard run."""
    script = os.path.join(dest, "hooks", "pre_bash_rewrite.py")
    command = f"CTX_GUARD_AGENT={AGENT} {shlex.quote(python_bin)} {shlex.quote(script)}"
    return {
        "PreToolUse": [
            {
                "matcher": "run_command",
                "hooks": [{"type": "command", "command": command, "timeout": 15}],
            }
        ]
    }


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


def install(manifest: dict, home: str, dest: str, python_bin: str) -> None:
    if not is_installed(home):
        return
    path = os.path.join(home, CONFIG)
    current = _read(path)
    previous = manifest.get("antigravity") or {}

    if current is not None and previous.get("installed_sha256") == _sha256(current):
        # Reinstall over our own unchanged write: keep the true original.
        original = previous.get("original")
        restorable = previous.get("restorable", False)
    elif previous:
        # The user edited the file since the last install, so the saved
        # original no longer applies; uninstall will only remove our key.
        original, restorable = None, False
    else:
        try:
            original = current.decode("utf-8") if current is not None else None
        except UnicodeDecodeError:
            print(f"Antigravity CLI: {path} is not UTF-8; leaving it unchanged.")
            return
        restorable = True

    try:
        config = json.loads(current) if current else {}
    except ValueError:
        print(f"Antigravity CLI: {path} is not valid JSON; leaving it unchanged.")
        return
    if not isinstance(config, dict):
        print(f"Antigravity CLI: {path} is not a JSON object; leaving it unchanged.")
        return

    config_dir = os.path.dirname(path)
    created_dir = None if os.path.isdir(config_dir) else config_dir
    os.makedirs(config_dir, exist_ok=True)
    config[HOOK_NAME] = hook_entry(dest, python_bin)
    text = json.dumps(config, indent=2) + "\n"
    _write(path, text)

    manifest["antigravity"] = {
        "path": path,
        "original": original,
        "restorable": restorable,
        "created_dir": created_dir or previous.get("created_dir"),
        "installed_sha256": _sha256(text.encode("utf-8")),
    }
    print("Antigravity CLI hook written to", path)


def restore(manifest: dict) -> None:
    entry = manifest.get("antigravity")
    if not entry:
        return
    path = entry["path"]
    current = _read(path)
    if current is None:
        print("No Antigravity CLI hooks file found, skipping.")
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
            print(f"Preserving unreadable Antigravity CLI hooks file: {path}")
            return
        if not isinstance(config, dict) or HOOK_NAME not in config:
            print(f"No ctx-guard hook found in {path}")
            return
        del config[HOOK_NAME]
        _write(path, json.dumps(config, indent=2) + "\n")
        print(f"Removed the ctx-guard hook from {path}")

    created_dir = entry.get("created_dir")
    if created_dir:
        try:
            os.rmdir(created_dir)  # only if still empty
        except OSError:
            pass
