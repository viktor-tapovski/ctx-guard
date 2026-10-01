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
# Explicit template: BSD mktemp ignores $TMPDIR for bare `-d`, which breaks
# sandboxed runs where only $TMPDIR is writable.
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/ctx-guard-smoke-XXXXXXXX")" || exit 1
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

echo "== package install checks =="

# These cases parse the raw hook JSON directly: rewrite_claude() only reads
# updatedInput, which warn/deny responses don't carry. All inputs are
# network-independent (curl|bash and apt are structural checks only).
#
# No "blocks nonexistent npm package" case here: that needs a live registry
# 404, which would be flaky/CI-breaking. The mocked unit test
# tests/test_pkg_install_check.py::test_nonexistent_npm_package_blocks covers it.
pkg_hook_field() {
  # $1 = command, $2 = hookSpecificOutput field; echoes "" if no output
  local cmd="$1" field="$2" payload out
  payload=$(python3 - "$cmd" <<'PY'
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
PY
  )
  out=$(echo "$payload" | CTX_GUARD_AGENT=claude-code python3 "$DIR/hooks/pre_bash_rewrite.py")
  [ -z "$out" ] && return 0
  echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("hookSpecificOutput",{}).get(sys.argv[1],""))' "$field"
}

PIPE_CMD="curl -sSL https://get.example.com/install.sh | bash"
decision=$(pkg_hook_field "$PIPE_CMD" permissionDecision)
reason=$(pkg_hook_field "$PIPE_CMD" permissionDecisionReason)
[ "$decision" = "ask" ] && ok "pkg-check warning asks (not auto-allow) on curl|bash" || bad "pkg-check curl|bash decision -> $decision"
case "$reason" in
  *ctx-guard-pkg*) ok "pkg-check warns on curl|bash" ;;
  *) bad "pkg-check warns on curl|bash -> $reason" ;;
esac

WRAP_CMD="apt-get install -y curl && true"
decision=$(pkg_hook_field "$WRAP_CMD" permissionDecision)
reason=$(pkg_hook_field "$WRAP_CMD" permissionDecisionReason)
[ "$decision" = "ask" ] && ok "pkg-check warning asks on rewrite+warn (compound wrap)" || bad "pkg-check rewrite+warn decision -> $decision"
case "$reason" in
  *ctx-guard-pkg*) ok "pkg-check reason present on rewrite+warn" ;;
  *) bad "pkg-check rewrite+warn reason -> $reason" ;;
esac

reason=$(CTX_GUARD_PKG_CHECK=0 pkg_hook_field "$PIPE_CMD" permissionDecisionReason)
case "$reason" in
  *ctx-guard-pkg*) bad "pkg-check disabled via CTX_GUARD_PKG_CHECK=0 -> $reason" ;;
  *) ok "pkg-check disabled via CTX_GUARD_PKG_CHECK=0" ;;
esac

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

wrapped=$(rewrite_claude "printf 'a\n'; printf 'b\n'")
: > "$CTX_GUARD_STATS_FILE"
(unset CTX_GUARD_AGENT; eval "$wrapped" >/dev/null)
logged_agent=$(python3 -c '
import json, sys
events = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print([e["agent"] for e in events if e["kind"] == "ctx_guard_run"][-1])
' "$CTX_GUARD_STATS_FILE")
[ "$logged_agent" = "claude-code" ] && ok "wrapped command logs the hook's agent, not unknown" || bad "wrapped command logged agent '$logged_agent'"

SAVINGS_REPO="$SCRATCH/savings-repo"
git init -q "$SAVINGS_REPO"
for i in $(seq 1 40); do
  git -C "$SAVINGS_REPO" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "commit number $i with a reasonably long message body"
done
: > "$CTX_GUARD_STATS_FILE"
(cd "$SAVINGS_REPO" && rewrite_claude "git log" >/dev/null)
rewrite_saved=$(python3 -c '
import json, sys
events = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print([e.get("saved_bytes", 0) for e in events if e["kind"] == "rewrite"][-1])
' "$CTX_GUARD_STATS_FILE")
[ "$rewrite_saved" -gt 0 ] 2>/dev/null && ok "read-only rewrite records measured saved_bytes ($rewrite_saved)" || bad "rewrite saved_bytes -> '$rewrite_saved'"

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

# sandboxed agents (e.g. Claude Code) cannot write /tmp/ctx-guard-<uid>: the
# wrapper must degrade to $TMPDIR instead of failing the wrapped command.
RO="$SCRATCH/readonly"
mkdir -p "$RO"
chmod 500 "$RO"
FB_TMP="$SCRATCH/fallback-tmp"
mkdir -p "$FB_TMP"
SMALL3="$SCRATCH/small3.sh"; echo 'echo fallback-ok' > "$SMALL3"
out=$(CTX_GUARD_LOG_DIR="$RO/logs" CTX_GUARD_STATE_DIR="$RO/state" \
      TMPDIR="$FB_TMP" "$CTX_GUARD_RUN" "$SMALL3" 2>/dev/null)
[ "$out" = "fallback-ok" ] && ok "unwritable log dir falls back instead of failing" \
  || bad "unwritable log dir -> $out"
if find "$FB_TMP/ctx-guard-$(id -u)/logs" -name 'run-*' 2>/dev/null | grep -q .; then
  ok "fallback log archived under \$TMPDIR"
else
  bad "no fallback log under \$TMPDIR"
fi

# last resort: nowhere writable at all -- still run the command, just uncapped.
SMALL4="$SCRATCH/small4.sh"; echo 'echo passthrough-ok' > "$SMALL4"
out=$(CTX_GUARD_LOG_DIR="$RO/logs" CTX_GUARD_STATE_DIR="$RO/state" \
      TMPDIR="$RO/tmp" "$CTX_GUARD_RUN" "$SMALL4" 2>/dev/null)
[ "$out" = "passthrough-ok" ] && ok "no writable dir anywhere: command still runs" \
  || bad "no writable dir anywhere -> $out"
chmod 700 "$RO"

# --- default stats location (persistent XDG state dir) ---------------------
# HOME/XDG_STATE_HOME/TMPDIR always point into $SCRATCH: never the real ~/.local/state.
SL="$SCRATCH/sl"; mkdir -p "$SL/tmp"
SLS="$SL/run.sh"; echo 'echo sl-ok' > "$SLS"
sl_run() { # sl_run <extra env assignments...>; clears all stats overrides
  echo 'echo sl-ok' > "$SLS"  # ctx-guard-run deletes its script after each run
  env -u CTX_GUARD_STATE_DIR -u CTX_GUARD_STATS_FILE -u XDG_STATE_HOME \
      CTX_GUARD_LOG_DIR="$SL/logs" TMPDIR="$SL/tmp" HOME="$SL/home" "$@" \
      "$CTX_GUARD_RUN" "$SLS" 2>/dev/null
}
sl_py() { # sl_py <extra env...> -- print STATS_FILE and write one event via hooks lib
  env -u CTX_GUARD_STATE_DIR -u CTX_GUARD_STATS_FILE -u XDG_STATE_HOME \
      TMPDIR="$SL/tmp" HOME="$SL/home" "$@" python3 -c '
import sys; sys.path.insert(0, sys.argv[1] + "/hooks/lib")
import ctx_guard_common as c
c.record_stats_event("t"); print(c.stats_write_path())' "$DIR"
}

mkdir -p "$SL/home"
out=$(sl_run); [ "$out" = "sl-ok" ] && ok "default stats: command output intact" || bad "default stats -> $out"
[ -f "$SL/home/.local/state/ctx-guard/stats.jsonl" ] && ok "default stats under \$HOME/.local/state/ctx-guard" \
  || bad "default stats not under \$HOME/.local/state/ctx-guard"
[ "$(mode_of "$SL/home/.local/state/ctx-guard")" = "700" ] && ok "persistent stats dir mode 700" || bad "persistent stats dir mode"
[ "$(mode_of "$SL/home/.local/state/ctx-guard/stats.jsonl")" = "600" ] && ok "persistent stats file mode 600" || bad "persistent stats file mode"

sl_run XDG_STATE_HOME="$SL/xdg" >/dev/null
[ -f "$SL/xdg/ctx-guard/stats.jsonl" ] && ok "XDG_STATE_HOME honored" || bad "XDG_STATE_HOME ignored"

sl_run XDG_STATE_HOME="$SL/xdg" CTX_GUARD_STATE_DIR="$SL/sd" >/dev/null
[ -f "$SL/sd/stats.jsonl" ] && ok "CTX_GUARD_STATE_DIR beats XDG default" || bad "STATE_DIR precedence"
sl_run XDG_STATE_HOME="$SL/xdg" CTX_GUARD_STATE_DIR="$SL/sd2" CTX_GUARD_STATS_FILE="$SL/explicit/s.jsonl" >/dev/null
[ -f "$SL/explicit/s.jsonl" ] && [ ! -e "$SL/sd2/stats.jsonl" ] && ok "CTX_GUARD_STATS_FILE beats STATE_DIR" || bad "STATS_FILE precedence"

# hooks lib: same rules
p=$(sl_py)
[ "$p" = "$SL/home/.local/state/ctx-guard/stats.jsonl" ] && ok "hooks lib default matches run" || bad "hooks lib default -> $p"
p=$(sl_py XDG_STATE_HOME="$SL/xdg2")
[ "$p" = "$SL/xdg2/ctx-guard/stats.jsonl" ] && [ -f "$p" ] && ok "hooks lib honors XDG_STATE_HOME" || bad "hooks lib XDG -> $p"
p=$(sl_py CTX_GUARD_STATE_DIR="$SL/sd3")
[ "$p" = "$SL/sd3/stats.jsonl" ] && ok "hooks lib STATE_DIR override" || bad "hooks lib STATE_DIR -> $p"
p=$(sl_py CTX_GUARD_STATE_DIR="$SL/sd3" CTX_GUARD_STATS_FILE="$SL/e2/s.jsonl")
[ "$p" = "$SL/e2/s.jsonl" ] && [ -f "$p" ] && ok "hooks lib STATS_FILE override" || bad "hooks lib STATS_FILE -> $p"

# ctx-guard-stats reads the same default
out=$(env -u CTX_GUARD_STATE_DIR -u CTX_GUARD_STATS_FILE -u XDG_STATE_HOME HOME="$SL/home" \
      python3 "$DIR/bin/ctx-guard-stats" --json)
echo "$out" | grep -q '"events": [1-9]' && ok "ctx-guard-stats reads default location" || bad "stats default read"

# fallback: unwritable persistent dir -> legacy runtime root (/tmp/ctx-guard-<uid>;
# CTX_GUARD_RUNTIME_ROOT redirects it so tests never touch the real one).
RO2="$SCRATCH/ro2"; mkdir -p "$RO2"; chmod 500 "$RO2"
out=$(sl_run HOME="$RO2/home" CTX_GUARD_RUNTIME_ROOT="$SL/rt1"); [ "$out" = "sl-ok" ] && ok "unwritable persistent stats dir: command still runs" || bad "persistent fallback -> $out"
[ -f "$SL/rt1/state/stats.jsonl" ] && ok "run falls back to legacy runtime state dir" || bad "run legacy fallback missing"
p=$(sl_py HOME="$RO2/home" CTX_GUARD_RUNTIME_ROOT="$SL/rt2")
[ "$p" = "$SL/rt2/state/stats.jsonl" ] && [ -f "$p" ] && ok "hooks lib falls back to legacy runtime state dir" || bad "hooks lib fallback -> $p"
chmod 700 "$RO2"

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

# release-please bumps version.txt and hooks/lib/version.py independently; if
# one updater ever stops matching, this is where it shows up.
file_version=$(tr -d '[:space:]' < "$DIR/version.txt")
reported=$(python3 "$DIR/bin/ctx-guard-stats" --version)
[ "$reported" = "ctx-guard $file_version" ] \
  && ok "ctx-guard-stats --version matches version.txt ($file_version)" \
  || bad "version mismatch: version.txt=$file_version, --version=$reported"

uninstall_version=$(python3 "$DIR/bin/ctx-guard-uninstall" --version)
[ "$uninstall_version" = "ctx-guard $file_version" ] \
  && ok "ctx-guard-uninstall --version matches version.txt" \
  || bad "uninstall version mismatch -> $uninstall_version"

echo "$file_version" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' \
  && ok "version.txt holds a bare semver" \
  || bad "version.txt is not semver -> $file_version"

python3 -c "
import json, sys
m = json.load(open('$DIR/.release-please-manifest.json'))
sys.exit(0 if m.get('.') == '$file_version' else 1)
" && ok "release-please manifest agrees with version.txt" \
  || bad "manifest and version.txt disagree"

# Observed-only log: nothing was wrapped, so there is no ratio -- but the
# gauge should still report the volume it does know about.
obs_file="$SCRATCH/observed-only.jsonl"
printf '%s\n' \
  '{"ts":1,"agent":"claude-code","kind":"tool_output","tool_name":"Bash","result_bytes":2048}' \
  '{"ts":2,"agent":"claude-code","kind":"rewrite","rule":"git-status"}' > "$obs_file"
obs_out=$(python3 "$DIR/bin/ctx-guard-stats" --stats-file "$obs_file")
echo "$obs_out" | grep -q "no measured savings yet" \
  && ok "gauge reports no measured savings without wrapped commands" \
  || bad "gauge missing empty-state text -> $obs_out"
echo "$obs_out" | grep -q "2.0KB tool output" \
  && ok "gauge reports observed tool output volume" \
  || bad "gauge missing observed volume -> $obs_out"
echo "$obs_out" | grep -q "1 rewrite  " \
  && ok "gauge singularizes a lone rewrite" \
  || bad "gauge rewrite count wrong -> $obs_out"
echo "$obs_out" | grep -qE "[0-9]%" \
  && bad "gauge invented a percentage with no measured data -> $obs_out" \
  || ok "gauge shows no percentage without measured data"

# Per-agent savings: rewrite saved_bytes count toward the headline and each
# agent gets its own saved bytes/tokens.
agent_file="$SCRATCH/per-agent.jsonl"
printf '%s\n' \
  '{"ts":1,"agent":"claude-code","kind":"ctx_guard_run","original_bytes":10000,"returned_bytes":2000,"compressed":true}' \
  '{"ts":2,"agent":"claude-code","kind":"rewrite","rule":"git-log","saved_bytes":4000}' \
  '{"ts":3,"agent":"copilot-cli","kind":"rewrite","rule":"git-diff","saved_bytes":8000}' > "$agent_file"
agent_json=$(python3 "$DIR/bin/ctx-guard-stats" --stats-file "$agent_file" --json)
echo "$agent_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["measured"]["saved_bytes"] == 20000, d["measured"]
a = d["by_agent"]
assert a["claude-code"] == {"saved_bytes": 12000, "saved_tokens_est": 3000}, a
assert a["copilot-cli"] == {"saved_bytes": 8000, "saved_tokens_est": 2000}, a
' && ok "stats --json attributes rewrite+run savings per agent" \
  || bad "by_agent savings wrong -> $agent_json"
agent_gauge=$(python3 "$DIR/bin/ctx-guard-stats" --stats-file "$agent_file")
echo "$agent_gauge" | grep -q "claude-code" && echo "$agent_gauge" | grep -q "copilot-cli" \
  && ok "gauge lists each agent's savings" \
  || bad "gauge missing per-agent savings -> $agent_gauge"

# --breakdown: fixture spans 2 days, 2 ISO weeks (01-26 -> 02-01, 02-02 -> 02-08)
# and 2 months. ts values are 12:00 UTC on 2026-01-30 and 2026-02-02.
bd_file="$SCRATCH/breakdown.jsonl"
printf '%s\n' \
  '{"ts":1769774400,"agent":"claude-code","kind":"ctx_guard_run","original_bytes":40000,"returned_bytes":4000,"compressed":true}' \
  '{"ts":1769774401,"agent":"claude-code","kind":"ctx_guard_run","original_bytes":8000,"returned_bytes":8000,"compressed":false}' \
  '{"ts":1770033600,"agent":"claude-code","kind":"ctx_guard_run","original_bytes":8000,"returned_bytes":2000,"compressed":true}' \
  '{"ts":1770033601,"agent":"claude-code","kind":"rewrite","rule":"git-log","saved_bytes":4000}' \
  '{"ts":1770033602,"agent":"claude-code","kind":"tool_output","tool_name":"Bash","result_bytes":999}' > "$bd_file"
bd() { python3 "$DIR/bin/ctx-guard-stats" --stats-file "$bd_file" "$@"; }
bd_all=$(bd --breakdown)
for hdr in "Daily Breakdown" "Weekly Breakdown" "Monthly Breakdown"; do
  echo "$bd_all" | grep -q "$hdr" && ok "--breakdown prints $hdr" || bad "--breakdown missing $hdr -> $bd_all"
done
echo "$bd_all" | grep -q "01-26 → 02-01" && echo "$bd_all" | grep -q "02-02 → 02-08" \
  && ok "--breakdown weekly rows show Mon→Sun ranges" || bad "weekly ranges wrong -> $bd_all"
echo "$bd_all" | grep -qE "^(2026-01-30|2026-02-02) " \
  && ok "--breakdown daily rows present" || bad "daily rows missing -> $bd_all"
[ "$(echo "$bd_all" | grep -c '^TOTAL')" = "3" ] \
  && ok "--breakdown all prints a TOTAL row per section" || bad "expected 3 TOTAL rows -> $bd_all"
bd_daily=$(bd --breakdown daily)
[ "$(echo "$bd_daily" | grep -cE '^2026-')" = "2" ] && ! echo "$bd_daily" | grep -q "Weekly" \
  && ok "--breakdown daily shows 2 day rows only" || bad "daily breakdown wrong -> $bd_daily"
echo "$bd_daily" | grep '^TOTAL' | grep -qE "3 +14\.0K +3\.5K +11\.5K +[0-9.]+%" \
  && ok "--breakdown TOTAL cmds/input/output/saved in tokens" || bad "TOTAL row wrong -> $bd_daily"
echo "$bd_daily" | grep -q $'\033' && bad "colour leaked into piped breakdown" || ok "no colour in piped breakdown"
CTX_GUARD_BARS=always bd --breakdown daily | grep -q $'\033' \
  && ok "Save% coloured when CTX_GUARD_BARS=always" || bad "no colour with CTX_GUARD_BARS=always"
bd_json=$(bd --breakdown --json)
echo "$bd_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert len(d["daily"]) == 2 and len(d["weekly"]) == 2 and len(d["monthly"]) == 2, d
assert d["totals"]["commands"] == 3, d["totals"]
assert d["totals"]["original_bytes"] == 56000 and d["totals"]["returned_bytes"] == 14000, d["totals"]
assert d["totals"]["saved_bytes"] == 46000, d["totals"]
assert sum(g["saved_bytes"] for g in d["daily"]) == 46000, d["daily"]
' && ok "--breakdown --json valid with groups and totals" || bad "breakdown json wrong -> $bd_json"
bd_since=$(bd --breakdown --since 1d)
echo "$bd_since" | grep -q "2026-01-30" \
  && bad "--since did not filter breakdown -> $bd_since" || ok "--since filters breakdown data"

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
