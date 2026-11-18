#!/usr/bin/env python3
"""
ctx-guard PostToolUse hook. Independent responsibilities, both best-effort
and non-blocking:

1. Estimates context usage and injects escalating guidance once per
   session at 50% / 70% / 85% thresholds. Claude Code: from the session
   transcript file (bytes/4). Copilot CLI sends no transcript_path, so the
   estimate there is the session's cumulative tool output (bytes/4) against
   CTX_GUARD_COPILOT_WINDOW_TOKENS, and the warning says it is estimated.
   Tool output is only part of the context, so this undercounts; the
   default window is kept small to compensate.

2. Both runtimes: records the size of the tool result actually delivered to
   the model, tagged by tool name and agent (claude-code / copilot-cli), so
   `ctx-guard-stats` can report real token throughput per tool over time.

3. Stats plumbing that needs to run outside the agent sandbox: drains
   ctx-guard-run's sandbox spool into the stats file, records ESTIMATED
   savings for rewrites pre_bash_rewrite.py couldn't measure, and prunes
   leftover wrap scripts.

Never raises: on any error, logs to stderr and exits 0 with no stdout, which
both runtimes treat as "no action, continue normally".
"""
from __future__ import annotations

import json
import os
import sys


def _exit_quietly(exc_type, exc, tb) -> None:
    """Backstop for a crash before main()'s own handler, such as a failed
    import: Copilot CLI denies the tool call when a hook exits non-zero."""
    print(f"[ctx-guard] hook failed: {exc}", file=sys.stderr, flush=True)
    os._exit(0)


sys.excepthook = _exit_quietly

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ctx_guard_common import (  # noqa: E402
    AGENT,
    PENDING_DIR,
    build_posttooluse_context,
    drain_spools,
    env_int,
    estimate_ratio,
    normalize_tool_call,
    normalize_tool_result_text,
    pending_key,
    pending_subject,
    prune_old,
    record_stats_event,
    safe_session_id,
)

# Leftover wrap scripts from older ctx-guard versions (the hook now pipes
# through ctx-guard-run --filter instead) and manual script-mode runs.
SCRIPT_DIR = os.environ.get(
    "CTX_GUARD_SCRIPT_DIR", f"/tmp/ctx-guard-{os.getuid()}/scripts"
)
SCRIPT_MAX_AGE_S = 6 * 3600

WINDOW_TOKENS = env_int("CTX_GUARD_WINDOW", 200000)
COPILOT_WINDOW_TOKENS = env_int("CTX_GUARD_COPILOT_WINDOW_TOKENS", 128000)
THRESHOLDS = [50, 70, 85]
STATE_DIR = os.environ.get(
    "CTX_GUARD_STATE_DIR", f"/tmp/ctx-guard-{os.getuid()}/state"
)

ADVICE = {
    50: ("Context is ~50% full. Prefer subagents (researcher / log-triager / "
         "code-searcher) for any further exploration; read files by exact path only."),
    70: ("Context is ~70% full. Stop reading new files directly. Delegate all "
         "investigation to subagents and suggest the user run /compact at the "
         "next natural checkpoint, focused on the current task."),
    85: ("Context is ~85% full. Finish the current step, summarize state "
         "(files touched, decisions, next actions), and recommend the user "
         "run /compact or start a fresh session now."),
}

# Copilot CLI has no researcher / log-triager / code-searcher agents; it
# delegates to its own built-in ones.
COPILOT_ADVICE = {
    50: ("Context is ~50% full. Prefer the built-in explore or task agents for "
         "any further exploration; view files by exact path and line range only."),
    70: ("Context is ~70% full. Stop viewing new files directly. Delegate all "
         "investigation to the explore or task agents and suggest the user run "
         "/compact at the next natural checkpoint, focused on the current task."),
    85: ADVICE[85],
}


def session_tool_bytes(session: str, payload: dict) -> int:
    """Running total of tool output bytes delivered this session (Copilot
    CLI's stand-in for transcript size). Best-effort read-modify-write."""
    text = normalize_tool_result_text(payload) or ""
    path = os.path.join(STATE_DIR, f"{session}.tool-bytes")
    total = 0
    try:
        with open(path) as f:
            total = int(f.read().strip() or 0)
    except (OSError, ValueError):
        pass
    total += len(text.encode("utf-8", errors="ignore"))
    with open(path, "w") as f:
        f.write(str(total))
    return total


def check_context_window(payload: dict) -> dict | None:
    transcript = payload.get("transcript_path", "")
    copilot = AGENT == "copilot-cli" and not transcript
    if not copilot and (not transcript or not os.path.isfile(transcript)):
        return None
    session = safe_session_id(payload)
    if copilot and session == "unknown":
        return None  # a shared counter would mix sessions

    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)  # makedirs' mode is masked by umask; enforce explicitly
    if copilot:
        pct = int(session_tool_bytes(session, payload) // 4 * 100 / COPILOT_WINDOW_TOKENS)
        advice, label = COPILOT_ADVICE, "estimated from tool output"
    else:
        pct = int(os.path.getsize(transcript) // 4 * 100 / WINDOW_TOKENS)
        advice, label = ADVICE, "of window"

    state_file = os.path.join(STATE_DIR, f"{session}.fired")
    fired = set()
    if os.path.isfile(state_file):
        with open(state_file) as f:
            fired = set(f.read().split())

    msg = None
    for t in THRESHOLDS:
        if pct >= t and str(t) not in fired:
            msg = f"[ctx-guard ~{pct}% {label}] {advice[t]}"
            fired.add(str(t))
    if not msg:
        return None

    with open(state_file, "w") as f:
        f.write(" ".join(sorted(fired)))
    return build_posttooluse_context(msg)


def record_tool_output_stats(payload: dict) -> None:
    tool_name, _ = normalize_tool_call(payload)
    text = normalize_tool_result_text(payload)
    if not tool_name or text is None:
        return
    record_stats_event(
        "tool_output",
        tool_name=tool_name,
        result_bytes=len(text.encode("utf-8", errors="ignore")),
    )


def record_rewrite_estimate(payload: dict) -> None:
    """ESTIMATED saving for a rewrite flagged by pre_bash_rewrite.py: the
    returned output is known, the unbounded output is assumed from the rule's
    ratio (returned = full * (1 - ratio))."""
    tool_name, tool_args = normalize_tool_call(payload)
    text = normalize_tool_result_text(payload)
    if text is None:
        return
    pending = os.path.join(PENDING_DIR, pending_key(payload, pending_subject(tool_args)))
    claimed = f"{pending}.{os.getpid()}"
    try:
        os.rename(pending, claimed)  # duplicate hook registrations record it once
        with open(claimed) as f:
            fields = f.read().split()
        os.remove(claimed)
    except OSError:
        return
    if not fields:
        return
    rule = fields[0]
    # pre_tool_bound.py may append a per-call ratio after the rule name.
    ratio = None
    if len(fields) > 1:
        try:
            ratio = float(fields[1])
        except ValueError:
            pass
        if ratio is not None and not 0 <= ratio < 1:
            ratio = None
    if ratio is None:
        ratio = estimate_ratio(rule)
    if ratio is None:
        return
    returned = len(text.encode("utf-8", errors="ignore"))
    record_stats_event(
        "rewrite_estimate",
        rule=rule,
        tool_name=tool_name,
        returned_bytes=returned,
        est_saved_bytes=round(returned * ratio / (1 - ratio)),
        ratio=ratio,
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        drain_spools()
        prune_old(SCRIPT_DIR, SCRIPT_MAX_AGE_S)
        record_rewrite_estimate(payload)
        record_tool_output_stats(payload)
        out = check_context_window(payload)
        if out:
            print(json.dumps(out))
    except Exception as e:
        print(f"[ctx-guard] context_monitor failed: {e}", file=sys.stderr)
        return


if __name__ == "__main__":
    main()
