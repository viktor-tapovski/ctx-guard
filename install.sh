#!/usr/bin/env bash
# ctx-guard installer: copies files into a shared ~/.ctx-guard install dir
# and wires hooks into both Claude Code (~/.claude/settings.json) and
# GitHub Copilot CLI (~/.copilot/hooks/ctx-guard.json), each tagged with
# CTX_GUARD_AGENT so `ctx-guard-stats` can break down savings per tool.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.ctx-guard"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
COPILOT_HOOKS_DIR="$HOME/.copilot/hooks"
LOCAL_BIN="$HOME/.local/bin"
RUN_DIR="/tmp/ctx-guard-$(id -u)"
MANIFEST="$DEST/install-manifest.json"
PYTHON_BIN="$(python3 -c 'import sys; print(sys.executable)')"

echo "Installing ctx-guard to $DEST ..."
mkdir -p "$DEST/hooks/lib" "$DEST/bin" "$HOME/.claude/agents" "$COPILOT_HOOKS_DIR" \
         "$RUN_DIR"/{logs,scripts,state}
chmod 700 "$RUN_DIR" "$RUN_DIR"/logs "$RUN_DIR"/scripts "$RUN_DIR"/state

# Migrate off the old shared (non-per-user) tmp dir: it was world-readable by
# default, so remove it rather than leaving stale logs exposed. `rm -rf` on a
# path that happens to be a symlink only unlinks the symlink, it does not
# follow it, so this is safe even if that path was ever tampered with.
rm -rf /tmp/ctx-guard 2>/dev/null || true

# Migrate off the old Claude-only install location from earlier ctx-guard
# versions, now superseded by the shared $DEST above.
rm -rf "$HOME/.claude/ctx-guard" 2>/dev/null || true

cp "$SRC/bin/ctx-guard-run" "$SRC/bin/ctx-guard-stats" "$SRC/bin/ctx-guard-uninstall" "$DEST/bin/"
cp "$SRC"/hooks/*.py                   "$DEST/hooks/"
cp "$SRC"/hooks/lib/*.py               "$DEST/hooks/lib/"
chmod +x "$DEST/bin/ctx-guard-run" "$DEST/bin/ctx-guard-stats" "$DEST/bin/ctx-guard-uninstall" "$DEST"/hooks/*.py

python3 - "$SRC" "$DEST" "$HOME/.claude/agents" "$MANIFEST" <<'PYEOF'
import glob
import hashlib
import json
import os
import shutil
import sys

src, dest, agents_dir, manifest_path = sys.argv[1:5]
manifest = {"version": 1, "agents": {}}
if os.path.isfile(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise SystemExit(f"Unsupported ctx-guard manifest: {manifest_path}")
    manifest.setdefault("agents", {})

def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

os.makedirs(agents_dir, mode=0o700, exist_ok=True)
for source in sorted(glob.glob(os.path.join(src, "agents", "*.md"))):
    name = os.path.basename(source)
    target = os.path.join(agents_dir, name)
    entry = manifest["agents"].get(name, {})

    if os.path.exists(target):
        installed_hash = entry.get("installed_sha256")
        if installed_hash and sha256(target) != installed_hash:
            raise SystemExit(
                f"Refusing to overwrite modified agent file: {target}. "
                "Restore or remove it manually before reinstalling."
            )
        if not installed_hash:
            backup = os.path.join(dest, "backups", "agents", name)
            os.makedirs(os.path.dirname(backup), mode=0o700, exist_ok=True)
            shutil.copy2(target, backup)
            entry["backup"] = backup

    shutil.copy2(source, target)
    entry["path"] = target
    entry["installed_sha256"] = sha256(target)
    manifest["agents"][name] = entry

with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
os.chmod(manifest_path, 0o600)
PYEOF

# Symlink stats/uninstall into ~/.local/bin so they're callable as plain
# commands (`ctx-guard-stats`, `ctx-guard-uninstall`) if that dir is on PATH.
# ctx-guard-run is intentionally not symlinked -- it's an internal wrapper
# invoked by the hooks themselves, not something a user runs directly.
mkdir -p "$LOCAL_BIN"
ln -sf "$DEST/bin/ctx-guard-stats" "$LOCAL_BIN/ctx-guard-stats"
ln -sf "$DEST/bin/ctx-guard-uninstall" "$LOCAL_BIN/ctx-guard-uninstall"
case ":$PATH:" in
  *":$LOCAL_BIN:"*) ;;
  *) echo "Note: $LOCAL_BIN is not on your PATH -- add it, or call the symlinked commands by full path." ;;
esac

# --- Claude Code: merge hooks into settings.json (backs up the original first) ---
python3 - "$CLAUDE_SETTINGS" "$DEST" "$PYTHON_BIN" << 'PYEOF'
import json, shutil, sys, os, shlex
settings_path, dest, python_bin = sys.argv[1], sys.argv[2], sys.argv[3]

settings = {}
if os.path.isfile(settings_path):
    shutil.copy(settings_path, settings_path + ".bak")
    with open(settings_path) as f:
        settings = json.load(f)

hooks = settings.setdefault("hooks", {})

def add(event, matcher, cmd):
    entries = hooks.setdefault(event, [])
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == cmd:
                return  # already installed
    entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]})

# Claude Code hook entries don't have a separate "env" field, so the agent
# tag is set inline in the shell command string.
def hook_command(agent, script):
    path = os.path.join(dest, "hooks", script)
    return f"CTX_GUARD_AGENT={agent} {shlex.quote(python_bin)} {shlex.quote(path)}"

add("PreToolUse",       "Bash", hook_command("claude-code", "pre_bash_rewrite.py"))
add("PostToolUse",      "*",    hook_command("claude-code", "context_monitor.py"))
add("UserPromptSubmit", "",     hook_command("claude-code", "auto_delegate.py"))

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
print("Claude Code hooks merged into", settings_path)
PYEOF

# --- Copilot CLI: write a dedicated user-level hooks file ---
# PascalCase event names (PreToolUse/PostToolUse) get Copilot to deliver
# Claude-shaped payloads (tool_name/tool_input) and apply Claude's matcher
# semantics, so the SAME hook scripts run unmodified under both tools.
# auto_delegate.py is intentionally NOT wired here: Copilot CLI drops
# command-hook output (including additionalContext) for userPromptSubmitted,
# and Copilot already auto-delegates to its own built-in subagents.
python3 - "$COPILOT_HOOKS_DIR/ctx-guard.json" "$DEST" "$PYTHON_BIN" << 'PYEOF'
import json, os, shlex, sys

path, dest, python_bin = sys.argv[1], sys.argv[2], sys.argv[3]
def hook_command(script):
    return f"{shlex.quote(python_bin)} {shlex.quote(os.path.join(dest, 'hooks', script))}"

config = {
    "version": 1,
    "hooks": {
        "PreToolUse": [
            {
                "type": "command",
                "matcher": "Bash",
                "bash": hook_command("pre_bash_rewrite.py"),
                "env": {"CTX_GUARD_AGENT": "copilot-cli"},
                "timeoutSec": 15,
            }
        ],
        "PostToolUse": [
            {
                "type": "command",
                "matcher": "*",
                "bash": hook_command("context_monitor.py"),
                "env": {"CTX_GUARD_AGENT": "copilot-cli"},
                "timeoutSec": 15,
            }
        ],
    },
}
with open(path, "w") as f:
    json.dump(config, f, indent=2)
print("Copilot CLI hooks written to", path)
PYEOF

# Append guard rules to global CLAUDE.md (idempotent)
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
RULES_FILE="$DEST/claude-rules.txt"
cat > "$RULES_FILE" << 'EOF'
## ctx-guard rules
- Cap every bash command: pipe searches through `| head -50`, use `git log --oneline -n 20`, `git diff --stat`, `git status --porcelain`.
- Test/build output: filter with `2>&1 | grep -A5 -iE "fail|error" | head -120`; full logs are archived in /tmp/ctx-guard-<uid>/logs/ — grep those instead of re-running.
- Delegate exploration to subagents: researcher (understand code/libs), log-triager (failures/logs), code-searcher (find definitions/usages). Do not read piles of files in the main context.
- When ctx-guard reports the window past 70%, stop new file reads and suggest /compact focused on the active task.
EOF
chmod 600 "$RULES_FILE"
python3 - "$CLAUDE_MD" "$RULES_FILE" << 'PYEOF'
import os, sys

claude_md, rules_file = sys.argv[1:3]
with open(rules_file, encoding="utf-8") as f:
    block = f.read()
os.makedirs(os.path.dirname(claude_md), mode=0o700, exist_ok=True)
content = ""
if os.path.isfile(claude_md):
    with open(claude_md, encoding="utf-8") as f:
        content = f.read()
if block not in content:
    with open(claude_md, "a", encoding="utf-8") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write("\n" + block)
    print("rules appended to", claude_md)
PYEOF

python3 - "$MANIFEST" "$DEST" "$CLAUDE_SETTINGS" "$COPILOT_HOOKS_DIR/ctx-guard.json" "$CLAUDE_MD" "$RULES_FILE" "$PYTHON_BIN" << 'PYEOF'
import hashlib
import json
import os
import shlex
import sys

manifest_path, dest, claude_settings, copilot_hooks, claude_md, rules_file, python_bin = sys.argv[1:8]
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)

def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def hook_command(agent, script):
    return f"CTX_GUARD_AGENT={agent} {shlex.quote(python_bin)} {shlex.quote(os.path.join(dest, 'hooks', script))}"

manifest.update({
    "claude_settings": claude_settings,
    "claude_hook_commands": [
        hook_command("claude-code", "pre_bash_rewrite.py"),
        hook_command("claude-code", "context_monitor.py"),
        hook_command("claude-code", "auto_delegate.py"),
    ],
    "copilot_hooks_file": copilot_hooks,
    "copilot_hooks_sha256": sha256(copilot_hooks),
    "claude_md": claude_md,
    "claude_rules_file": rules_file,
    "claude_rules_sha256": sha256(rules_file),
    "local_bin_links": {
        os.path.join(os.path.expanduser("~"), ".local", "bin", "ctx-guard-stats"):
            os.path.join(dest, "bin", "ctx-guard-stats"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "ctx-guard-uninstall"):
            os.path.join(dest, "bin", "ctx-guard-uninstall"),
    },
})

temporary = manifest_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, manifest_path)
PYEOF

echo ""
echo "Done."
echo "Claude Code: restart it, then verify with: /hooks and /agents"
echo "Copilot CLI: restart it, then verify with: /env (look for ctx-guard.json under hooks)"
echo ""
echo "Check savings any time with: ctx-guard-stats"
echo "Uninstall any time with:     ctx-guard-uninstall"
echo "(or the full paths: $DEST/bin/ctx-guard-stats, $DEST/bin/ctx-guard-uninstall)"
echo "Tune via env vars: CTX_GUARD_MAX_LINES (default 150), CTX_GUARD_WINDOW (default 200000),"
echo "CTX_GUARD_LOG_RETENTION_DAYS (default 7). Runtime state lives under $RUN_DIR."
