#!/usr/bin/env bash
# ctx-guard smoke tests: exercises the three hooks and ctx-guard-run against
# fixture payloads/commands, under BOTH Claude Code and Copilot CLI payload
# shapes. Not a full test suite -- fast sanity checks that catch regressions
# in rewrite rules, permissions, retention, sanitization, cross-tool payload
# compatibility, and stats logging.
#
# Runs fully isolated from any real ctx-guard install: everything happens
# under a throwaway scratch dir, never under the user's real /tmp/ctx-guard-<uid>/.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

export CTX_GUARD_RUN="$DIR/bin/ctx-guard-run"
export CTX_GUARD_SCRIPT_DIR="$SCRATCH/scripts"
export CTX_GUARD_LOG_DIR="$SCRATCH/logs"
export CTX_GUARD_STATE_DIR="$SCRATCH/state"
export CTX_GUARD_STATS_FILE="$SCRATCH/state/stats.jsonl"
export CTX_GUARD_LOG_RETENTION_DAYS=7

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ok   - $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL - $1"; }
mode_of() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

# --- pre_bash_rewrite.py (Claude Code payload shape) -----------------------

rewrite_claude() {
  # $1 = command, echoes the rewritten command (or the original if untouched)
  local cmd="$1" payload out
  payload=$(python3 - "$cmd" <<'PY'
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
PY
  )
  out=$(echo "$payload" | CTX_GUARD_AGENT=claude-code python3 "$DIR/hooks/pre_bash_rewrite.py")
  if [ -z "$out" ]; then
    echo "$cmd"
  else
    echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["updatedInput"]["command"])'
  fi
}

echo "== pre_bash_rewrite.py (Claude Code payload) =="

got=$(rewrite_claude "git status")
[ "$got" = "git status --porcelain=v1 -b" ] && ok "git status rewrite" || bad "git status rewrite -> $got"

got=$(rewrite_claude "docker logs mycontainer")
[ "$got" = "docker logs mycontainer | tail -100" ] && ok "docker logs rewrite" || bad "docker logs rewrite -> $got"

got=$(rewrite_claude "docker logs -f mycontainer")
[[ "$got" == "$CTX_GUARD_RUN "* ]] && ok "docker logs -f falls back to generic wrap, not naive tail" || bad "docker logs -f -> $got"

got=$(rewrite_claude "kubectl get pods -A")
[ "$got" = "kubectl get pods -A | head -50" ] && ok "kubectl get rewrite" || bad "kubectl get rewrite -> $got"

got=$(rewrite_claude "pip list")
[ "$got" = "pip list | head -50" ] && ok "pip list rewrite" || bad "pip list rewrite -> $got"

got=$(rewrite_claude "npm ls --depth=0")
[ "$got" = "npm ls --depth=0 | head -50" ] && ok "npm ls rewrite" || bad "npm ls rewrite -> $got"

got=$(rewrite_claude "terraform plan -var foo=bar")
[ "$got" = "terraform plan -var foo=bar -no-color | tail -100" ] && ok "terraform plan rewrite" || bad "terraform plan rewrite -> $got"

got=$(rewrite_claude "grep foo file1 && grep bar file2")
[[ "$got" == "$CTX_GUARD_RUN "* ]] && ok "compound command wrapped via generic wrapper" || bad "compound command not wrapped -> $got"

[ -d "$CTX_GUARD_SCRIPT_DIR" ] && ok "SCRIPT_DIR created" || bad "SCRIPT_DIR missing"
if [ -d "$CTX_GUARD_SCRIPT_DIR" ]; then
  perm=$(mode_of "$CTX_GUARD_SCRIPT_DIR")
  [ "$perm" = "700" ] && ok "SCRIPT_DIR mode 700" || bad "SCRIPT_DIR mode $perm (want 700)"
fi

# Non-bash tools must pass through untouched (no stdout at all).
out=$(echo '{"tool_name": "view", "tool_input": {"path": "/tmp/x"}}' | python3 "$DIR/hooks/pre_bash_rewrite.py")
[ -z "$out" ] && ok "non-Bash tool produces no output" || bad "non-Bash tool produced output -> $out"

# --- pre_bash_rewrite.py (Copilot CLI camelCase payload shape) -------------

echo "== pre_bash_rewrite.py (Copilot CLI payload) =="

rewrite_copilot() {
  local cmd="$1" payload out
  payload=$(python3 - "$cmd" <<'PY'
import json, sys
print(json.dumps({"toolName": "bash", "toolArgs": {"command": sys.argv[1]}}))
PY
  )
  out=$(echo "$payload" | CTX_GUARD_AGENT=copilot-cli python3 "$DIR/hooks/pre_bash_rewrite.py")
  if [ -z "$out" ]; then
    echo "$cmd"
  else
    echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["modifiedArgs"]["command"])'
  fi
}

got=$(rewrite_copilot "git status")
[ "$got" = "git status --porcelain=v1 -b" ] && ok "git status rewrite (modifiedArgs)" || bad "git status rewrite -> $got"

# permissionDecision must be present and "allow" for a rewrite (Copilot reads this flat field).
payload='{"toolName": "bash", "toolArgs": {"command": "git status"}}'
out=$(echo "$payload" | CTX_GUARD_AGENT=copilot-cli python3 "$DIR/hooks/pre_bash_rewrite.py")
decision=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["permissionDecision"])')
[ "$decision" = "allow" ] && ok "permissionDecision is 'allow' on rewrite" || bad "permissionDecision -> $decision"

# Non-command args (e.g. missing "command" key) must not crash the hook.
out=$(echo '{"toolName": "bash", "toolArgs": {}}' | python3 "$DIR/hooks/pre_bash_rewrite.py"; echo "exit:$?")
[[ "$out" == *"exit:0"* ]] && ok "missing command key does not crash (exit 0)" || bad "missing command key -> $out"

# Malformed JSON on stdin must never cause non-zero exit (Copilot preToolUse is fail-closed on crash).
out=$(echo 'not json at all' | python3 "$DIR/hooks/pre_bash_rewrite.py"; echo "EXIT:$?")
[[ "$out" == *"EXIT:0"* ]] && ok "malformed JSON input still exits 0 (fail-closed safety)" || bad "malformed input -> $out"

# Environment enumeration must be denied rather than returned to the model.
for sensitive_cmd in env printenv set export; do
  payload=$(python3 - "$sensitive_cmd" <<'PY'
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
PY
  )
  out=$(echo "$payload" | python3 "$DIR/hooks/pre_bash_rewrite.py")
  decision=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["permissionDecision"])')
  [ "$decision" = "deny" ] && ok "$sensitive_cmd is denied" || bad "$sensitive_cmd decision -> $decision"
done

# Generated wrapper commands must remain safe when the runtime path contains
# spaces or shell metacharacters.
QUOTED_RUN="$SCRATCH/ctx guard;run"
cp "$DIR/bin/ctx-guard-run" "$QUOTED_RUN"
chmod +x "$QUOTED_RUN"
quoted=$(CTX_GUARD_RUN="$QUOTED_RUN" rewrite_claude "npm test")
case "$quoted" in
  "'$QUOTED_RUN' "*)
    ok "wrapper path is shell-quoted"
    ;;
  *)
    bad "wrapper path is not shell-quoted -> $quoted"
    ;;
esac

# --- ctx-guard-run ----------------------------------------------------------

echo "== ctx-guard-run =="

SMALL="$SCRATCH/small.sh"
echo 'echo hello' > "$SMALL"
out=$(CTX_GUARD_AGENT=claude-code "$CTX_GUARD_RUN" "$SMALL")
[ "$out" = "hello" ] && ok "small output passed through untouched" || bad "small output -> $out"

SECRET="$SCRATCH/secret.sh"
{
  echo '#!/usr/bin/env bash'
  echo 'echo "Authorization: Bearer super-secret-bearer-token"'
  echo 'echo "API_KEY=super-secret-api-key"'
} > "$SECRET"
secret_out=$(CTX_GUARD_AGENT=claude-code "$CTX_GUARD_RUN" "$SECRET")
if [[ "$secret_out" == *"super-secret-bearer-token"* || "$secret_out" == *"super-secret-api-key"* ]]; then
  bad "common credential patterns leaked to tool output"
else
  ok "common credential patterns redacted from tool output"
fi

BIG="$SCRATCH/big.sh"
{
  echo '#!/usr/bin/env bash'
  for i in $(seq 1 300); do echo "echo line$i"; done
  echo 'echo FAIL: assertion boom'
} > "$BIG"
out=$(CTX_GUARD_AGENT=claude-code "$CTX_GUARD_RUN" "$BIG")
echo "$out" | grep -q "output compressed" && ok "large output compressed" || bad "large output not compressed"
echo "$out" | grep -q "FAIL: assertion boom" && ok "error line surfaced in compressed output" || bad "error line not surfaced"

logfile=$(find "$CTX_GUARD_LOG_DIR" -name 'run-*' | head -1)
if [ -n "$logfile" ]; then
  ok "log archived"
  perm=$(mode_of "$logfile")
  [ "$perm" = "600" ] && ok "log file mode 600" || bad "log file mode $perm (want 600)"
else
  bad "no log archived"
fi

perm=$(mode_of "$CTX_GUARD_LOG_DIR")
[ "$perm" = "700" ] && ok "LOG_DIR mode 700" || bad "LOG_DIR mode $perm (want 700)"

# retention: a stale log should be pruned on the next run
touch -t 202001010000 "$CTX_GUARD_LOG_DIR/run-stale"
SMALL2="$SCRATCH/small2.sh"; echo 'echo hi' > "$SMALL2"
"$CTX_GUARD_RUN" "$SMALL2" >/dev/null
if [ -f "$CTX_GUARD_LOG_DIR/run-stale" ]; then
  bad "stale log not pruned by retention"
else
  ok "stale log pruned by retention"
fi

# --- stats.jsonl (written by ctx-guard-run above) --------------------------

echo "== stats logging =="

[ -f "$CTX_GUARD_STATS_FILE" ] && ok "stats.jsonl created" || bad "stats.jsonl missing"
if [ -f "$CTX_GUARD_STATS_FILE" ]; then
  n_events=$(wc -l < "$CTX_GUARD_STATS_FILE" | tr -d ' ')
  [ "$n_events" -ge 2 ] && ok "stats.jsonl has events from both ctx-guard-run calls ($n_events)" \
                        || bad "expected >=2 stats events, got $n_events"

  compressed_line=$(grep -m1 '"compressed":true' "$CTX_GUARD_STATS_FILE" || true)
  [ -n "$compressed_line" ] && ok "a 'compressed:true' event was recorded" || bad "no compressed event found"

  orig=$(echo "$compressed_line" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["original_bytes"])' 2>/dev/null || echo "")
  ret=$(echo "$compressed_line" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["returned_bytes"])' 2>/dev/null || echo "")
  if [ -n "$orig" ] && [ -n "$ret" ] && [ "$orig" -gt "$ret" ]; then
    ok "measured original_bytes ($orig) > returned_bytes ($ret)"
  else
    bad "expected original_bytes > returned_bytes, got orig=$orig ret=$ret"
  fi
fi

# --- ctx-guard-stats CLI -----------------------------------------------------

echo "== ctx-guard-stats =="

out=$(python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE" --json)
echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["measured"]["commands_wrapped"] >= 2' \
  && ok "ctx-guard-stats --json reports measured commands_wrapped" \
  || bad "ctx-guard-stats --json output invalid -> $out"

saved=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["measured"]["saved_bytes"])')
[ "$saved" -gt 0 ] && ok "ctx-guard-stats reports saved_bytes > 0 ($saved)" || bad "saved_bytes not > 0 -> $saved"

text_out=$(python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE" --verbose)
echo "$text_out" | grep -q "MEASURED" && ok "--verbose stats output present" || bad "--verbose output missing MEASURED section"
echo "$text_out" | grep -q "OBSERVED" && ok "--verbose output has OBSERVED section" || bad "--verbose output missing OBSERVED section"

# Default view is the one-line gauge, not the full report.
gauge_out=$(python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE")
gauge_lines=$(printf '%s\n' "$gauge_out" | wc -l | tr -d ' ')
[ "$gauge_lines" = "1" ] && ok "default stats output is a single line" || bad "default output was $gauge_lines lines -> $gauge_out"
echo "$gauge_out" | grep -q "saved" && ok "gauge reports saved bytes" || bad "gauge missing savings -> $gauge_out"
echo "$gauge_out" | grep -q "MEASURED" && bad "default output should not print the full report" || ok "default output omits the full report"

# Bars are decoration only: captured output is not a TTY, so it must stay plain.
echo "$gauge_out" | grep -q "█" && bad "bars leaked into non-TTY output" || ok "no bars in piped stats output"
printf '%s' "$gauge_out" | grep -q "$(printf '\033')" && bad "ANSI escapes leaked into piped output" || ok "no ANSI escapes in piped output"

bar_out=$(CTX_GUARD_BARS=always python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE")
echo "$bar_out" | grep -q "█" && ok "CTX_GUARD_BARS=always renders the gauge bar" || bad "CTX_GUARD_BARS=always produced no bars -> $bar_out"
bar_lines=$(printf '%s\n' "$bar_out" | wc -l | tr -d ' ')
[ "$bar_lines" = "1" ] && ok "gauge with bars is still a single line" || bad "gauge with bars was $bar_lines lines -> $bar_out"

verbose_bars=$(CTX_GUARD_BARS=always python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE" --verbose)
echo "$verbose_bars" | grep -q "█" && ok "--verbose renders bars too" || bad "--verbose produced no bars"
echo "$verbose_bars" | grep -q "MEASURED" && ok "--verbose with bars keeps MEASURED section" || bad "--verbose with bars missing MEASURED section"

never_out=$(CTX_GUARD_BARS=never python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE")
echo "$never_out" | grep -q "█" && bad "CTX_GUARD_BARS=never still drew bars" || ok "CTX_GUARD_BARS=never suppresses bars"

# An ASCII-only stdout must degrade to '#'/'-', never raise UnicodeEncodeError.
ascii_out=$(CTX_GUARD_BARS=always LC_ALL=C PYTHONIOENCODING=ascii python3 "$DIR/bin/ctx-guard-stats" --stats-file "$CTX_GUARD_STATS_FILE" 2>&1)
echo "$ascii_out" | grep -q "UnicodeEncodeError" && bad "ASCII stdout crashed -> $ascii_out" || ok "ASCII stdout does not crash"
echo "$ascii_out" | grep -q "#" && ok "ASCII stdout falls back to '#' bars" || bad "no ASCII bar fallback -> $ascii_out"

# --- context_monitor.py: Claude Code context-window thresholds ------------

echo "== context_monitor.py (Claude Code transcript-based window) =="

TRANSCRIPT="$SCRATCH/transcript.jsonl"
python3 -c "print('x' * (int(0.55 * 200000 * 4)))" > "$TRANSCRIPT"

monitor_claude() {
  # $1 = session id
  python3 - "$TRANSCRIPT" "$1" <<'PY' | CTX_GUARD_AGENT=claude-code python3 "$DIR/hooks/context_monitor.py"
import json, sys
print(json.dumps({"transcript_path": sys.argv[1], "session_id": sys.argv[2], "tool_name": "bash", "tool_response": {"stdout": "ok"}}))
PY
}

out=$(monitor_claude "sess-1")
echo "$out" | grep -q "Context is ~50% full" && ok "50% threshold fires" || bad "50% threshold did not fire -> $out"

out=$(monitor_claude "sess-1")
[ -z "$out" ] && ok "threshold fires once per session" || bad "threshold re-fired -> $out"

out=$(monitor_claude "../../etc/passwd")
[ -f "$CTX_GUARD_STATE_DIR/unknown.fired" ] && ok "malicious session_id sanitized to 'unknown'" || bad "session_id not sanitized"
[ ! -e "$CTX_GUARD_STATE_DIR/../../etc/passwd.fired" ] && ok "no path traversal outside STATE_DIR" || bad "path traversal occurred"

perm=$(mode_of "$CTX_GUARD_STATE_DIR")
[ "$perm" = "700" ] && ok "STATE_DIR mode 700" || bad "STATE_DIR mode $perm (want 700)"

# --- context_monitor.py: Copilot CLI payload (no transcript, has toolResult) --

echo "== context_monitor.py (Copilot CLI payload) =="

payload=$(python3 -c 'import json; print(json.dumps({"sessionId": "copi-1", "toolName": "bash", "toolResult": {"textResultForLlm": "x" * 400}}))')
out=$(echo "$payload" | CTX_GUARD_AGENT=copilot-cli python3 "$DIR/hooks/context_monitor.py"; echo "EXIT:$?")
[[ "$out" == *"EXIT:0"* ]] && ok "Copilot payload without transcript_path exits 0" || bad "Copilot payload crashed -> $out"

tool_output_line=$(grep '"kind":"tool_output"' "$CTX_GUARD_STATS_FILE" | grep '"agent":"copilot-cli"' | tail -1 || true)
[ -n "$tool_output_line" ] && ok "tool_output stats event recorded for Copilot payload" || bad "no tool_output stats event found"
bytes_recorded=$(echo "$tool_output_line" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["result_bytes"])' 2>/dev/null || echo "")
[ "$bytes_recorded" = "400" ] && ok "tool_output result_bytes measured correctly (400)" || bad "result_bytes -> $bytes_recorded (want 400)"

# --- install/uninstall ownership safety -------------------------------------

echo "== install/uninstall ownership safety =="

FAKE_HOME="$SCRATCH/fake-home"
mkdir -p "$FAKE_HOME/.claude/agents"
echo "user-maintained researcher agent" > "$FAKE_HOME/.claude/agents/researcher.md"
HOME="$FAKE_HOME" bash "$DIR/install.sh" >/dev/null

python3 - "$FAKE_HOME/.claude/settings.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    settings = json.load(f)
settings["hooks"].setdefault("PreToolUse", []).append({
    "matcher": "Bash",
    "hooks": [{"type": "command", "command": "python3 /opt/ctx-guard-policy.py"}],
})
with open(path, "w") as f:
    json.dump(settings, f)
PY

HOME="$FAKE_HOME" "$DIR/bin/ctx-guard-uninstall" >/dev/null

if grep -q "ctx-guard-policy.py" "$FAKE_HOME/.claude/settings.json"; then
  ok "uninstall preserves unrelated ctx-guard-named hook"
else
  bad "uninstall removed unrelated ctx-guard-named hook"
fi

if [ "$(cat "$FAKE_HOME/.claude/agents/researcher.md")" = "user-maintained researcher agent" ]; then
  ok "uninstall restores pre-existing agent file"
else
  bad "uninstall did not restore pre-existing agent file"
fi

echo
echo "== summary: $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
