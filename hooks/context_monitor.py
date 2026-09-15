#!/usr/bin/env python3
"""
ctx-guard PostToolUse hook. Two independent responsibilities, both best-effort
and non-blocking:

1. Claude Code only: estimates context usage from the session transcript
   file (bytes/4) and injects escalating guidance once per session at
   50% / 70% / 85% thresholds. Silently skipped when no transcript_path is
   present (Copilot CLI's postToolUse payload doesn't include one -- and
   Copilot CLI already surfaces context-window usage natively via /context
   and the status line, so this would be redundant there anyway).

2. Both runtimes: records the size of the tool result actually delivered to
   the model, tagged by tool name and agent (claude-code / copilot-cli), so
   `ctx-guard-stats` can report real token throughput per tool over time.

Never raises: on any error, logs to stderr and exits 0 with no stdout, which
both runtimes treat as "no action, continue normally".
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ctx_guard_common import (  # noqa: E402
    build_posttooluse_context,
    normalize_tool_call,
    normalize_tool_result_text,
    record_stats_event,
    safe_session_id,
)

WINDOW_TOKENS = int(os.environ.get("CTX_GUARD_WINDOW", "200000"))
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


def check_context_window(payload: dict) -> dict | None:
    transcript = payload.get("transcript_path", "")
    if not transcript or not os.path.isfile(transcript):
        return None
    session = safe_session_id(payload)

    est_tokens = os.path.getsize(transcript) // 4
    pct = int(est_tokens * 100 / WINDOW_TOKENS)

    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)  # makedirs' mode is masked by umask; enforce explicitly
    state_file = os.path.join(STATE_DIR, f"{session}.fired")
    fired = set()
    if os.path.isfile(state_file):
        with open(state_file) as f:
            fired = set(f.read().split())

    msg = None
    for t in THRESHOLDS:
        if pct >= t and str(t) not in fired:
            msg = f"[ctx-guard ~{pct}% of window] {ADVICE[t]}"
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


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        record_tool_output_stats(payload)
        out = check_context_window(payload)
        if out:
            print(json.dumps(out))
    except Exception as e:
        print(f"[ctx-guard] context_monitor failed: {e}", file=sys.stderr)
        return


if __name__ == "__main__":
    main()
