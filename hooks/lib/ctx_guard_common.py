"""
Shared helpers for ctx-guard hooks so the same script works, unmodified,
under both Claude Code's native hook payloads and GitHub Copilot CLI's hook
payloads (which use different field casing and a different output contract).

Payload shapes handled:
  - Claude Code (native):          tool_name / tool_input / tool_response
  - Copilot CLI (camelCase config): toolName / toolArgs / toolResult
  - Copilot CLI (PascalCase config, "Claude-compatible" payload):
                                    tool_name / tool_input / tool_result
                                    (same field names as Claude, but Copilot
                                    still expects the *output* contract below)

Output contract: this module builds a single JSON object containing BOTH
Claude Code's nested `hookSpecificOutput` shape and Copilot CLI's flat
`permissionDecision` / `modifiedArgs` / `additionalContext` fields. Each
runtime reads only the keys it understands and ignores the rest, so one
object satisfies both.

IMPORTANT (Copilot CLI): command `preToolUse` hooks are fail-closed -- a
crash or non-zero exit denies the tool call even if stdout has
`permissionDecision: "allow"`. Every hook using this module MUST catch all
exceptions and exit 0. See pre_bash_rewrite.py's top-level try/except.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

AGENT = os.environ.get("CTX_GUARD_AGENT", "unknown")

STATE_DIR = os.environ.get(
    "CTX_GUARD_STATE_DIR", f"/tmp/ctx-guard-{os.getuid()}/state"
)
STATS_FILE = os.environ.get(
    "CTX_GUARD_STATS_FILE", os.path.join(STATE_DIR, "stats.jsonl")
)

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def get_field(payload: dict, *names: str, default: Any = None) -> Any:
    """Return the first present field among `names` (handles casing differences)."""
    for name in names:
        if name in payload:
            return payload[name]
    return default


def normalize_tool_call(payload: dict) -> tuple[str, dict]:
    """Return (tool_name, tool_args) regardless of which runtime sent the payload."""
    tool_name = get_field(payload, "tool_name", "toolName", default="") or ""
    tool_args = get_field(payload, "tool_input", "toolArgs", default={})
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except (json.JSONDecodeError, ValueError):
            tool_args = {}
    if not isinstance(tool_args, dict):
        tool_args = {}
    return tool_name, tool_args


def normalize_tool_result_text(payload: dict) -> str | None:
    """Best-effort extraction of the tool's delivered output text, across runtimes."""
    result = get_field(payload, "tool_response", "toolResult", "tool_result")
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("textResultForLlm", "text_result_for_llm", "output", "stdout", "content"):
            val = result.get(key)
            if isinstance(val, str):
                return val
        try:
            return json.dumps(result)
        except (TypeError, ValueError):
            return str(result)
    return str(result)


def safe_session_id(payload: dict) -> str:
    session = get_field(payload, "session_id", "sessionId", default="unknown") or "unknown"
    if not SESSION_ID_RE.match(str(session)):
        session = "unknown"
    return str(session)


def build_pretooluse_decision(
    *,
    reason: str,
    new_args: dict | None = None,
    decision: str = "allow",
) -> dict:
    """Build a preToolUse decision object understood by Claude Code and Copilot CLI."""
    out: dict = {
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        },
    }
    if new_args is not None:
        out["modifiedArgs"] = new_args
        out["hookSpecificOutput"]["updatedInput"] = new_args
    return out


def build_posttooluse_context(message: str) -> dict:
    """Build a postToolUse additionalContext object understood by both runtimes."""
    return {
        "additionalContext": message,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        },
    }


def record_stats_event(kind: str, **fields: Any) -> None:
    """Append one JSONL event to the stats file. Never raises."""
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        event = {"ts": time.time(), "kind": kind, "agent": AGENT, **fields}
        line = json.dumps(event, separators=(",", ":"))
        fd = os.open(STATS_FILE, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        # Stats are best-effort; never let logging break a hook.
        pass
