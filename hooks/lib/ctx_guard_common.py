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

import contextlib
import glob
import hashlib
import io
import json
import os
import re
import sys
import time
from typing import Any, Callable

AGENT = os.environ.get("CTX_GUARD_AGENT", "unknown")
ANTIGRAVITY = "antigravity-cli"

# Antigravity CLI sends {"toolCall": {"name", "args"}} with its own tool and
# argument names; map them to the Claude names the hooks match on.
ANTIGRAVITY_TOOLS = {"run_command": "Bash"}
ANTIGRAVITY_ARGS = {"CommandLine": "command"}

STATE_DIR = os.environ.get(
    "CTX_GUARD_STATE_DIR", f"/tmp/ctx-guard-{os.getuid()}/state"
)

# Stats location, same rule in ctx-guard-run and ctx-guard-stats:
# CTX_GUARD_STATS_FILE > $CTX_GUARD_STATE_DIR/stats.jsonl >
# ${XDG_STATE_HOME:-$HOME/.local/state}/ctx-guard/stats.jsonl (persistent).
# Only the persistent default falls back to the legacy runtime root.
LEGACY_STATS_FILE = os.path.join(
    os.environ.get("CTX_GUARD_RUNTIME_ROOT") or f"/tmp/ctx-guard-{os.getuid()}",
    "state",
    "stats.jsonl",
)


def _default_stats_file() -> str:
    if os.environ.get("CTX_GUARD_STATS_FILE"):
        return os.environ["CTX_GUARD_STATS_FILE"]
    if os.environ.get("CTX_GUARD_STATE_DIR"):
        return os.path.join(os.environ["CTX_GUARD_STATE_DIR"], "stats.jsonl")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "ctx-guard", "stats.jsonl")


STATS_FILE = _default_stats_file()
STATS_FILE_IS_DEFAULT = not (
    os.environ.get("CTX_GUARD_STATS_FILE") or os.environ.get("CTX_GUARD_STATE_DIR")
)


def _prepare_dir(path: str) -> bool:
    d = os.path.dirname(path)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        return os.access(d, os.W_OK | os.X_OK)
    except OSError:
        return False


def stats_write_path() -> str:
    """Return the stats file to append to, creating its dir (mode 700)."""
    if _prepare_dir(STATS_FILE) or not STATS_FILE_IS_DEFAULT:
        return STATS_FILE
    _prepare_dir(LEGACY_STATS_FILE)
    return LEGACY_STATS_FILE


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def get_field(payload: dict, *names: str, default: Any = None) -> Any:
    """Return the first present field among `names` (handles casing differences)."""
    for name in names:
        if name in payload:
            return payload[name]
    return default


def normalize_tool_call(payload: dict) -> tuple[str, dict]:
    """Return (tool_name, tool_args) regardless of which runtime sent the payload."""
    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        tool_name = str(tool_call.get("name") or "")
        tool_args = tool_call.get("args")
        if isinstance(tool_args, dict):
            tool_args = {ANTIGRAVITY_ARGS.get(k, k): v for k, v in tool_args.items()}
        return ANTIGRAVITY_TOOLS.get(tool_name, tool_name), tool_args if isinstance(tool_args, dict) else {}
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
    session = get_field(payload, "session_id", "sessionId", "conversationId", default="unknown") or "unknown"
    if not SESSION_ID_RE.match(str(session)):
        session = "unknown"
    return str(session)


def build_pretooluse_decision(
    *,
    reason: str,
    new_args: dict | None = None,
    decision: str | None = "allow",
    additional_context: str | None = None,
) -> dict:
    """Build a preToolUse decision object understood by Claude Code and Copilot CLI.
    decision=None returns no permission decision, so the runtime's normal
    permission rules still apply to the (possibly modified) call."""
    if AGENT == ANTIGRAVITY:
        return _antigravity_decision(reason, new_args, decision, additional_context)
    out: dict = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision is not None:
        out["permissionDecision"] = decision
        out["permissionDecisionReason"] = reason
        out["hookSpecificOutput"]["permissionDecision"] = decision
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if new_args is not None:
        out["modifiedArgs"] = new_args
        out["hookSpecificOutput"]["updatedInput"] = new_args
    if additional_context is not None:
        out["additionalContext"] = additional_context
        out["hookSpecificOutput"]["additionalContext"] = additional_context
    return out


def _antigravity_decision(
    reason: str, new_args: dict | None, decision: str | None, additional_context: str | None
) -> dict:
    """Antigravity CLI's preToolUse output: `decision` is required, "allow"
    defers to the user's permission policy (so a rewrite is never
    auto-approved), and `overwrite` is merged into toolCall.args. There is
    no context field, so additional_context rides along in the reason."""
    out: dict = {"decision": decision or "allow"}
    if additional_context:
        reason = f"{reason} | {additional_context}" if reason else additional_context
    if reason:
        out["reason"] = reason
    if new_args is not None:
        names = {v: k for k, v in ANTIGRAVITY_ARGS.items()}
        out["overwrite"] = {names[k]: v for k, v in new_args.items() if k in names}
    return out


def run_pretooluse_hook(main: Callable[[], None]) -> None:
    """Run a preToolUse hook's main(). Antigravity CLI blocks the tool call
    when a preToolUse hook prints no decision or crashes, so a silent
    pass-through prints an explicit allow there; other agents see main()'s
    output unchanged."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main()
    except Exception as e:  # main() catches its own errors; this is the backstop
        print(f"[ctx-guard] hook failed: {e}", file=sys.stderr)
    output = buffer.getvalue()
    if AGENT == ANTIGRAVITY:
        try:
            json.loads(output)
        except ValueError:
            output = json.dumps({"decision": "allow"}) + "\n"
    sys.stdout.write(output)


def build_posttooluse_context(message: str) -> dict:
    """Build a postToolUse additionalContext object understood by both runtimes."""
    return {
        "additionalContext": message,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        },
    }


def spool_files(dest: str | None = None) -> list[str]:
    """Stats files written by ctx-guard-run inside a sandbox (Claude Code's Bash
    sandbox can't write the canonical file, so ctx-guard-run falls back to
    $TMPDIR/ctx-guard-<uid>/state). Only files owned by us, never `dest`.
    CTX_GUARD_SPOOL_DIRS (colon-separated TMPDIR roots) replaces the defaults."""
    uid = os.getuid()
    if "CTX_GUARD_SPOOL_DIRS" in os.environ:
        roots = os.environ["CTX_GUARD_SPOOL_DIRS"].split(":")
    else:
        roots = [os.environ.get("TMPDIR", "")] + glob.glob("/tmp/claude*")
    skip = {os.path.realpath(p) for p in (dest or stats_write_path(), LEGACY_STATS_FILE)}
    found: list[str] = []
    for root in roots:
        if not root:
            continue
        path = os.path.realpath(os.path.join(root, f"ctx-guard-{uid}", "state", "stats.jsonl"))
        try:
            if path in skip or path in found or os.stat(path).st_uid != uid:
                continue
        except OSError:
            continue
        found.append(path)
    return found


def drain_spools(dest: str | None = None) -> None:
    """Move spooled events into `dest`. Never raises; on any failure the spool is
    left (or put back) in place so readers still see it -- events are never lost."""
    try:
        dest = dest or stats_write_path()
        if not _prepare_dir(dest) or (os.path.exists(dest) and not os.access(dest, os.W_OK)):
            return
        for spool in spool_files(dest):
            claimed = f"{spool}.drain-{os.getpid()}"
            try:
                os.rename(spool, claimed)  # atomic: concurrent drainers can't both win
            except OSError:
                continue
            try:
                with open(claimed, "rb") as f:
                    data = f.read()
                if data and not data.endswith(b"\n"):
                    data += b"\n"
                fd = os.open(dest, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
            except OSError:
                try:
                    os.rename(claimed, spool)
                except OSError:
                    pass
                continue
            try:
                os.remove(claimed)  # appended already: never put it back
            except OSError:
                pass
    except Exception:
        pass


# ESTIMATED savings for rewrites whose unbounded form is never run: assumed
# fraction of output removed by each rule. Assumptions, not measurements;
# override per rule with a JSON object in $CTX_GUARD_ESTIMATE_RATIOS.
ESTIMATE_RATIOS = {
    "git-status": 0.5, "git-log": 0.85, "git-log-args": 0.85, "git-diff": 0.9,
    "git-show": 0.9, "git-branch-a": 0.3, "grep-unbounded": 0.5,
    "find-unbounded": 0.5, "cat-large": 0.6, "docker-logs": 0.8,
    "kubectl-get": 0.3, "kubectl-describe": 0.5, "terraform-plan": 0.6,
    "pkg-list": 0.6,
    # Bounded built-in tool calls (pre_tool_bound.py). read-bound is only the
    # fallback: each bounded Read records its own ratio from the line count.
    "read-bound": 0.5, "grep-bound": 0.5,
}


def estimate_ratio(rule: str) -> float | None:
    ratios = dict(ESTIMATE_RATIOS)
    path = os.environ.get("CTX_GUARD_ESTIMATE_RATIOS")
    if path:
        try:
            with open(path) as f:
                ratios.update({k: float(v) for k, v in json.load(f).items()})
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    r = ratios.get(rule)
    return r if r is not None and 0 <= r < 1 else None


PENDING_DIR = os.path.join(STATE_DIR, "pending")
PENDING_MAX_AGE_S = 600


def pending_key(payload: dict, command: str) -> str:
    """Correlates a PreToolUse rewrite with its PostToolUse result: tool_use_id
    when the runtime sends one, else session + original-command hash."""
    tool_use_id = str(get_field(payload, "tool_use_id", "toolUseId", default="") or "")
    if SESSION_ID_RE.match(tool_use_id):
        return tool_use_id
    digest = hashlib.sha256(command.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{safe_session_id(payload)}-{digest}"


def pending_subject(tool_args: dict) -> str:
    """What pending_key hashes when there is no tool_use_id (Copilot CLI):
    the same field in PreToolUse and PostToolUse for every tool."""
    for key in ("command", "file_path", "path", "pattern"):
        value = tool_args.get(key)
        if value:
            return str(value)
    return ""


def prune_old(directory: str, max_age_s: float) -> None:
    try:
        cutoff = time.time() - max_age_s
        for entry in os.scandir(directory):
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
            except OSError:
                pass
    except OSError:
        pass


def record_stats_event(kind: str, **fields: Any) -> None:
    """Append one JSONL event to the stats file. Never raises."""
    try:
        path = stats_write_path()
        event = {"ts": time.time(), "kind": kind, "agent": AGENT, **fields}
        line = json.dumps(event, separators=(",", ":"))
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        # Stats are best-effort; never let logging break a hook.
        pass
