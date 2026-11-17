#!/usr/bin/env bash
# ctx-guard installer: copies files into a shared ~/.ctx-guard install dir
# and wires hooks into the agents you choose -- Claude Code
# (~/.claude/settings.json), GitHub Copilot CLI (~/.copilot/hooks/ctx-guard.json),
# Antigravity CLI and Codex CLI (see hooks/lib/agent_hooks.py) -- each tagged
# with CTX_GUARD_AGENT so `ctx-guard-stats` can break down savings per tool.
set -euo pipefail

usage() {
  cat << 'EOF'
Usage: install.sh [--agents LIST] [--yes] [--statusline]
       install.sh --statusline-preset NAME | --statusline-format TEMPLATE

  --agents LIST   set up these agents only, comma-separated, or "all":
                  claude-code, copilot-cli, antigravity-cli, codex-cli
  --yes, -y       don't ask; set up the agents detected on this machine
  --statusline    also add ctx-guard's savings to each agent's status line
  --statusline-preset NAME
                  shape the status line segment with a bundled preset:
                  default, minimal, security, full, powerline
  --statusline-format TEMPLATE
                  ... or with your own template, e.g. '⬇ {today_all}'
                  (both write ~/.config/ctx-guard/statusline.json; on their
                  own they change nothing else and skip the install; preview
                  the presets with: ctx-guard-statusline --preview)

Without --agents or --yes, a checklist lets you pick agents with the arrow
keys and space. Re-running with fewer agents removes ctx-guard from the
ones you left out. Set CTX_GUARD_PYTHON to choose the Python 3.9+ to use.
EOF
}

STATUSLINE=0
AGENTS_ARG=""
ASSUME_YES=0
SL_PRESET=""
SL_FORMAT=""
SL_CONFIG=0
INSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --statusline) STATUSLINE=1; INSTALL=1 ;;
    --statusline-preset) [ $# -ge 2 ] || { echo "--statusline-preset needs a value" >&2; exit 2; }; SL_PRESET="$2"; SL_CONFIG=1; shift ;;
    --statusline-preset=*) SL_PRESET="${1#--statusline-preset=}"; SL_CONFIG=1 ;;
    --statusline-format) [ $# -ge 2 ] || { echo "--statusline-format needs a value" >&2; exit 2; }; SL_FORMAT="$2"; SL_CONFIG=1; shift ;;
    --statusline-format=*) SL_FORMAT="${1#--statusline-format=}"; SL_CONFIG=1 ;;
    --agents) [ $# -ge 2 ] || { echo "--agents needs a value" >&2; exit 2; }; AGENTS_ARG="$2"; INSTALL=1; shift ;;
    --agents=*) AGENTS_ARG="${1#--agents=}"; INSTALL=1 ;;
    --yes|-y) ASSUME_YES=1; INSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$SL_PRESET" ] && [ -n "$SL_FORMAT" ]; then
  echo "Use --statusline-preset or --statusline-format, not both" >&2; exit 2
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.ctx-guard"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
COPILOT_HOOKS_DIR="$HOME/.copilot/hooks"
LOCAL_BIN="$HOME/.local/bin"
RUN_DIR="/tmp/ctx-guard-$(id -u)"
MANIFEST="$DEST/install-manifest.json"

# Python 3.9+: CTX_GUARD_PYTHON, else the first python3 on PATH that is new
# enough (an older python3 can sit in front of a newer python3.x on PATH).
PYTHON_BIN=""
for candidate in "${CTX_GUARD_PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
  [ -n "$candidate" ] || continue
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PYTHON_BIN="$("$candidate" -c 'import sys; print(sys.executable)')"
    break
  fi
  if [ "$candidate" = "${CTX_GUARD_PYTHON:-}" ]; then
    echo "CTX_GUARD_PYTHON=$CTX_GUARD_PYTHON is not a Python 3.9 or newer." >&2
    exit 1
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "ctx-guard needs Python 3.9 or newer (found: $(command python3 --version 2>&1 || echo none))." >&2
  echo "Install one (e.g. brew install python@3.13) or set CTX_GUARD_PYTHON." >&2
  exit 1
fi
# Every `python3` below runs the interpreter chosen above.
python3() { "$PYTHON_BIN" "$@"; }

# --- Status line config (--statusline-preset / --statusline-format) ---
# Writes ~/.config/ctx-guard/statusline.json, read by ctx-guard-statusline on
# every refresh. The manifest (when ctx-guard is installed) records whether
# this created the file, so uninstall removes only a file it made. With no
# other option, that is all this run does.
write_statusline_config() {
python3 - "$SRC" "$MANIFEST" "$SL_PRESET" "$SL_FORMAT" << 'PYEOF'
import json, os, sys

src, manifest_path, preset, template = sys.argv[1:5]
sys.path.insert(0, os.path.join(src, "hooks", "lib"))
import statusline_format

manifest = None
if os.path.isfile(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
try:
    path = statusline_format.write_config(manifest, preset=preset or None,
                                          template=template or None)
except (OSError, ValueError) as e:
    raise SystemExit(f"Status line config not written: {e}")
if manifest is not None:
    temporary = manifest_path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, manifest_path)
what = f"preset {preset}" if preset else "your template"
print(f"Status line config written to {path} ({what})")
PYEOF
}
if [ "$SL_CONFIG" = 1 ]; then
  # Check the preset or template before installing anything.
  python3 - "$SRC" "$SL_PRESET" "$SL_FORMAT" << 'PYEOF' || exit 2
import os, sys

src, preset, template = sys.argv[1:4]
sys.path.insert(0, os.path.join(src, "hooks", "lib"))
import statusline_format

if preset and preset not in statusline_format.PRESETS:
    raise SystemExit(f"Unknown status line preset {preset!r}; "
                     f"choose from {', '.join(statusline_format.PRESETS)}")
try:
    statusline_format.parse(template)
except ValueError as e:
    raise SystemExit(f"Invalid status line template: {e}")
PYEOF
  if [ "$INSTALL" = 0 ]; then
    write_statusline_config
    echo "Preview the presets with: ctx-guard-statusline --preview"
    exit 0
  fi
fi

# --- Which agents ------------------------------------------------------------
# Previously installed agents (from the manifest) are pre-ticked in the
# checklist along with the detected ones; any of them left out now is removed.
PREVIOUS=""
if [ -f "$MANIFEST" ]; then
  PREVIOUS="$(python3 - "$MANIFEST" << 'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    m = json.load(f)
if "installed_agents" in m:
    found = m["installed_agents"]
else:  # manifests from before agent selection
    found = (["claude-code"] if m.get("claude_settings") else []) + \
            (["copilot-cli"] if m.get("copilot_hooks_file") else []) + list(m.get("hook_files", {}))
print(",".join(found))
PYEOF
)"
fi
DETECTED="$(python3 "$SRC/hooks/lib/agents.py" detect)"

if [ -n "$AGENTS_ARG" ]; then
  AGENTS="$(python3 "$SRC/hooks/lib/agents.py" parse "$AGENTS_ARG")"
elif [ "$ASSUME_YES" = 0 ] && [ -t 1 ] && { : < /dev/tty; } 2>/dev/null; then
  AGENTS="$(python3 "$SRC/hooks/lib/agent_picker.py" --preselect "$PREVIOUS,$DETECTED")"
else
  AGENTS="$DETECTED"
  if [ -z "$AGENTS" ]; then
    echo "No supported agent found on this machine. Choose with --agents, e.g. --agents claude-code" >&2
    exit 1
  fi
fi
has_agent() { case ",$AGENTS," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
echo "Setting up ctx-guard for: $AGENTS"

REMOVED=""
for agent in ${PREVIOUS//,/ }; do
  has_agent "$agent" || REMOVED="${REMOVED:+$REMOVED,}$agent"
done
if [ -n "$REMOVED" ]; then
  echo "Removing ctx-guard from: $REMOVED"
  python3 "$SRC/bin/ctx-guard-uninstall" --agents "$REMOVED" --keep-shared
fi

echo "Installing ctx-guard to $DEST ..."
mkdir -p "$DEST/hooks/lib" "$DEST/hooks/lib/popular_packages" "$DEST/bin" "$RUN_DIR"/{logs,scripts,state}
chmod 700 "$RUN_DIR" "$RUN_DIR"/logs "$RUN_DIR"/scripts "$RUN_DIR"/state

# Migrate off the old shared (non-per-user) tmp dir: it was world-readable by
# default, so remove it rather than leaving stale logs exposed. `rm -rf` on a
# path that happens to be a symlink only unlinks the symlink, it does not
# follow it, so this is safe even if that path was ever tampered with.
rm -rf /tmp/ctx-guard 2>/dev/null || true

# Migrate off the old Claude-only install location from earlier ctx-guard
# versions, now superseded by the shared $DEST above.
rm -rf "$HOME/.claude/ctx-guard" 2>/dev/null || true

cp "$SRC/bin/ctx-guard-run" "$SRC/bin/ctx-guard-recall" "$SRC/bin/ctx-guard-stats" "$SRC/bin/ctx-guard-statusline" "$SRC/bin/ctx-guard-try" "$SRC/bin/ctx-guard-uninstall" "$DEST/bin/"
cp "$SRC"/hooks/*.py                   "$DEST/hooks/"
cp "$SRC"/hooks/lib/*.py               "$DEST/hooks/lib/"
cp "$SRC"/hooks/lib/popular_packages/*.txt "$DEST/hooks/lib/popular_packages/"
chmod +x "$DEST/bin/ctx-guard-run" "$DEST/bin/ctx-guard-recall" "$DEST/bin/ctx-guard-stats" "$DEST/bin/ctx-guard-statusline" "$DEST/bin/ctx-guard-try" "$DEST/bin/ctx-guard-uninstall" "$DEST"/hooks/*.py

python3 - "$MANIFEST" <<'PYEOF'
import json
import os
import sys

manifest_path = sys.argv[1]
manifest = {"version": 1}
if os.path.isfile(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise SystemExit(f"Unsupported ctx-guard manifest: {manifest_path}")
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
os.chmod(manifest_path, 0o600)
PYEOF

# --- Claude Code: subagents, settings.json hooks, CLAUDE.md rules ---
if has_agent claude-code; then
python3 - "$SRC" "$DEST" "$HOME/.claude/agents" "$MANIFEST" <<'PYEOF'
import glob
import hashlib
import json
import os
import shutil
import sys

src, dest, agents_dir, manifest_path = sys.argv[1:5]
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)
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
fi

# Symlink recall/stats/statusline/try/uninstall into ~/.local/bin so they're callable as
# plain commands (`ctx-guard-recall`, `ctx-guard-stats`, ...) if
# that dir is on PATH.
# ctx-guard-run is intentionally not symlinked -- it's an internal wrapper
# invoked by the hooks themselves, not something a user runs directly.
mkdir -p "$LOCAL_BIN"
ln -sf "$DEST/bin/ctx-guard-recall" "$LOCAL_BIN/ctx-guard-recall"
ln -sf "$DEST/bin/ctx-guard-stats" "$LOCAL_BIN/ctx-guard-stats"
ln -sf "$DEST/bin/ctx-guard-statusline" "$LOCAL_BIN/ctx-guard-statusline"
ln -sf "$DEST/bin/ctx-guard-try" "$LOCAL_BIN/ctx-guard-try"
ln -sf "$DEST/bin/ctx-guard-uninstall" "$LOCAL_BIN/ctx-guard-uninstall"
case ":$PATH:" in
  *":$LOCAL_BIN:"*) ;;
  *) echo "Note: $LOCAL_BIN is not on your PATH -- add it, or call the symlinked commands by full path." ;;
esac

# --- Claude Code: merge hooks into settings.json (backs up the original first) ---
if has_agent claude-code; then
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
    # Match on the hook script path, not the full command: a different python
    # interpreter path would otherwise register the same hook twice and double
    # every stats event. Stale copies are dropped and the current one appended.
    script = shlex.split(cmd)[-1]
    entries = hooks.setdefault(event, [])
    kept = []
    for e in entries:
        e["hooks"] = [h for h in e.get("hooks", []) if script not in h.get("command", "")]
        if e["hooks"]:
            kept.append(e)
    kept.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]})
    hooks[event] = kept

# Claude Code hook entries don't have a separate "env" field, so the agent
# tag is set inline in the shell command string.
def hook_command(agent, script):
    path = os.path.join(dest, "hooks", script)
    return f"CTX_GUARD_AGENT={agent} {shlex.quote(python_bin)} {shlex.quote(path)}"

add("PreToolUse",       "Bash", hook_command("claude-code", "pre_bash_rewrite.py"))
add("PreToolUse",       "Read|Grep", hook_command("claude-code", "pre_tool_bound.py"))
add("PostToolUse",      "*",    hook_command("claude-code", "context_monitor.py"))
add("UserPromptSubmit", "",     hook_command("claude-code", "auto_delegate.py"))

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
print("Claude Code hooks merged into", settings_path)
PYEOF
fi

# --- Copilot CLI: write a dedicated user-level hooks file ---
# PascalCase event names (PreToolUse/PostToolUse) get Copilot to deliver
# Claude-shaped payloads (tool_name/tool_input) and apply Claude's matcher
# semantics, so the SAME hook scripts run unmodified under both tools.
# auto_delegate.py is intentionally NOT wired here: Copilot CLI parses but
# never injects a userPromptSubmitted hook's additionalContext, and Copilot
# already auto-delegates to its own built-in subagents.
if has_agent copilot-cli; then
mkdir -p "$COPILOT_HOOKS_DIR"
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
            },
            {
                # Claude names: matches Copilot's view, grep and rg tools.
                "type": "command",
                "matcher": "Read|Grep",
                "bash": hook_command("pre_tool_bound.py"),
                "env": {"CTX_GUARD_AGENT": "copilot-cli"},
                "timeoutSec": 15,
            },
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
fi

# Append guard rules to global CLAUDE.md (idempotent)
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
RULES_FILE="$DEST/claude-rules.txt"
if has_agent claude-code; then
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
fi

python3 - "$MANIFEST" "$DEST" "$CLAUDE_SETTINGS" "$COPILOT_HOOKS_DIR/ctx-guard.json" "$CLAUDE_MD" "$RULES_FILE" "$PYTHON_BIN" "$AGENTS" << 'PYEOF'
import hashlib
import json
import os
import shlex
import sys

manifest_path, dest, claude_settings, copilot_hooks, claude_md, rules_file, python_bin, agents = sys.argv[1:9]
selected = agents.split(",")
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

if "claude-code" in selected:
    manifest.update({
        "claude_settings": claude_settings,
        "claude_hook_commands": [
            hook_command("claude-code", "pre_bash_rewrite.py"),
            hook_command("claude-code", "pre_tool_bound.py"),
            hook_command("claude-code", "context_monitor.py"),
            hook_command("claude-code", "auto_delegate.py"),
        ],
        "claude_md": claude_md,
        "claude_rules_file": rules_file,
        "claude_rules_sha256": sha256(rules_file),
    })
if "copilot-cli" in selected:
    manifest.update({
        "copilot_hooks_file": copilot_hooks,
        "copilot_hooks_sha256": sha256(copilot_hooks),
    })
manifest.update({
    "installed_agents": selected,
    "local_bin_links": {
        os.path.join(os.path.expanduser("~"), ".local", "bin", "ctx-guard-recall"):
            os.path.join(dest, "bin", "ctx-guard-recall"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "ctx-guard-stats"):
            os.path.join(dest, "bin", "ctx-guard-stats"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "ctx-guard-statusline"):
            os.path.join(dest, "bin", "ctx-guard-statusline"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "ctx-guard-try"):
            os.path.join(dest, "bin", "ctx-guard-try"),
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

# --- Antigravity CLI and Codex CLI: add ctx-guard to their shared hooks files ---
# Only for the chosen agents. Each file's prior text goes in the manifest so
# uninstall can put it back exactly; see hooks/lib/agent_hooks.py.
python3 - "$MANIFEST" "$DEST" "$HOME" "$PYTHON_BIN" "$AGENTS" << 'PYEOF'
import json, os, sys

manifest_path, dest, home, python_bin, agents = sys.argv[1:6]
sys.path.insert(0, os.path.join(dest, "hooks", "lib"))
import agent_hooks

with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)
agent_hooks.install(manifest, home, dest, python_bin, agents.split(","))
temporary = manifest_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, manifest_path)
PYEOF

# --- Opt-in status line segment (--statusline) ---
# Each agent's existing statusLine is recorded in the manifest and chained by
# ctx-guard-statusline, so the user's own status line keeps showing; uninstall
# puts it back. The agents and their config files are listed in
# hooks/lib/statusline_config.py (TARGETS).
if [ "$STATUSLINE" = 1 ]; then
python3 - "$MANIFEST" "$DEST" "$HOME" "$PYTHON_BIN" "$AGENTS" << 'PYEOF'
import json, os, sys

manifest_path, dest, home, python_bin, agents = sys.argv[1:6]
sys.path.insert(0, os.path.join(dest, "hooks", "lib"))
import statusline_config

with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)
statusline_config.install(manifest, home, dest, python_bin, agents.split(","))
temporary = manifest_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, manifest_path)
PYEOF
fi
if [ "$SL_CONFIG" = 1 ]; then write_statusline_config; fi

echo ""
echo "Done."
if has_agent claude-code; then echo "Claude Code: restart it, then verify with: /hooks and /agents"; fi
if has_agent copilot-cli; then echo "Copilot CLI: restart it, then verify with: /env (look for ctx-guard.json under hooks)"; fi
if has_agent antigravity-cli; then echo "Antigravity CLI: restart it, then verify with: /hooks"; fi
if has_agent codex-cli; then echo "Codex CLI: restart it, then open /hooks and trust the ctx-guard hook (Codex skips it until you do)"; fi
echo ""
echo "See it work right now with:   ctx-guard-try"
echo "Check savings any time with: ctx-guard-stats"
echo "Uninstall any time with:     ctx-guard-uninstall"
[ "$STATUSLINE" = 1 ] || echo "Optional live savings in the status line: rerun the install with --statusline"
echo "(or the full paths: $DEST/bin/ctx-guard-stats, $DEST/bin/ctx-guard-uninstall)"
echo "Tune via env vars: CTX_GUARD_MAX_LINES (default 150), CTX_GUARD_WINDOW (default 200000),"
echo "CTX_GUARD_LOG_RETENTION_DAYS (default 7). Runtime state lives under $RUN_DIR."
