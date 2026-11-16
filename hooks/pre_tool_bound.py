#!/usr/bin/env python3
"""
ctx-guard PreToolUse hook for Claude Code's built-in Read and Grep tools.

PostToolUse cannot shrink a built-in tool's output, so this hook bounds the
input before the tool runs:

- Read: no `limit` and more than CTX_GUARD_READ_MAX_LINES (default 400) lines
  left to read -> set `limit`, and tell the model how to read the rest.
  Skipped for files the user named in their latest prompt.
- Grep: `output_mode: "content"` with no `head_limit` -> set `head_limit` to
  CTX_GUARD_GREP_MAX (default 100).
- Glob has no limit field, so it is left alone.

The hook never returns a permission decision: the bounded call still goes
through the user's normal permission rules. CTX_GUARD_BOUND_TOOLS=0 turns
it off. Every failure path exits 0 with no output, so the original call runs.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ctx_guard_common import (  # noqa: E402
    PENDING_DIR,
    PENDING_MAX_AGE_S,
    build_pretooluse_decision,
    get_field,
    normalize_tool_call,
    pending_key,
    prune_old,
    record_stats_event,
)

# Claude Code's Read returns at most this many lines when no limit is given.
READ_DEFAULT_LINES = 2000
# Files bigger than this are not line-counted; they are bounded on size alone.
COUNT_MAX_BYTES = 64 * 1024 * 1024
# How much of the transcript tail to scan for the latest user prompt.
TRANSCRIPT_TAIL_BYTES = 512 * 1024
# Read handles these as images/documents/notebooks, not as numbered lines.
NON_TEXT_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff",
    ".pdf", ".ipynb",
}


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def count_lines(path: str) -> int | None:
    """Line count of a text file, or None for binary or oversized files."""
    if os.path.getsize(path) > COUNT_MAX_BYTES:
        return None
    lines = 0
    last = b""
    with open(path, "rb") as f:
        first = True
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if first and b"\0" in chunk[:8192]:
                return None
            first = False
            lines += chunk.count(b"\n")
            last = chunk
    if last and not last.endswith(b"\n"):
        lines += 1
    return lines


def latest_prompt(transcript_path: str) -> str:
    """Text of the most recent user prompt in a Claude Code transcript, or ""."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return ""
    with open(transcript_path, "rb") as f:
        f.seek(max(0, os.path.getsize(transcript_path) - TRANSCRIPT_TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="ignore")
    for line in reversed(tail.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Tool results are also "user" entries; only typed text is a prompt.
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def mark_pending(payload: dict, rule: str, subject: str, ratio: float | None) -> None:
    """Leave the rule (and a per-call ratio) for context_monitor.py, which sees
    the returned bytes and records an ESTIMATED saving. Best-effort."""
    try:
        os.makedirs(PENDING_DIR, mode=0o700, exist_ok=True)
        os.chmod(PENDING_DIR, 0o700)
        prune_old(PENDING_DIR, PENDING_MAX_AGE_S)
        with open(os.path.join(PENDING_DIR, pending_key(payload, subject)), "w") as f:
            f.write(rule if ratio is None else f"{rule} {ratio:.4f}")
    except OSError:
        pass


def bound_read(payload: dict, args: dict) -> dict | None:
    if "limit" in args:
        return None
    path = args.get("file_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return None
    if os.path.splitext(path)[1].lower() in NON_TEXT_EXTENSIONS:
        return None
    max_lines = env_int("CTX_GUARD_READ_MAX_LINES", 400)
    if max_lines >= READ_DEFAULT_LINES:
        return None
    try:
        offset = max(1, int(args.get("offset") or 1))
    except (TypeError, ValueError):
        return None

    size = os.path.getsize(path)
    total = count_lines(path)
    if total is None:
        if size <= COUNT_MAX_BYTES:
            return None  # binary
        remaining = None
    else:
        remaining = total - offset + 1
        if remaining <= max_lines:
            return None

    name = os.path.basename(path)
    if name and name in latest_prompt(str(get_field(payload, "transcript_path", default="") or "")):
        return None

    end = offset + max_lines - 1
    if remaining is None:
        ratio = None
        note = (f"ctx-guard: file is {size // (1024 * 1024)} MB; showing lines "
                f"{offset}-{end}. Read more with offset={end + 1}, or grep for what you need.")
    else:
        ratio = 1 - max_lines / min(remaining, READ_DEFAULT_LINES)
        note = (f"ctx-guard: file has {total} lines; showing lines {offset}-{end}. "
                f"Read more with offset={end + 1}, or grep for what you need.")
    record_stats_event("bound", rule="read-bound", tool_name="Read",
                       file_lines=total, bounded_lines=max_lines)
    mark_pending(payload, "read-bound", path, ratio)
    return build_pretooluse_decision(
        reason="ctx-guard: bounded Read (read-bound)",
        new_args={**args, "limit": max_lines},
        decision=None,
        additional_context=note,
    )


def bound_grep(payload: dict, args: dict) -> dict | None:
    if args.get("output_mode") != "content" or "head_limit" in args:
        return None
    max_lines = env_int("CTX_GUARD_GREP_MAX", 100)
    record_stats_event("bound", rule="grep-bound", tool_name="Grep", bounded_lines=max_lines)
    mark_pending(payload, "grep-bound", str(args.get("pattern") or ""), None)
    return build_pretooluse_decision(
        reason="ctx-guard: bounded Grep (grep-bound)",
        new_args={**args, "head_limit": max_lines},
        decision=None,
        additional_context=(f"ctx-guard: Grep content output capped at {max_lines} lines. "
                            "Narrow the pattern, path or glob, or set head_limit explicitly."),
    )


HANDLERS = {"Read": bound_read, "Grep": bound_grep}


def main() -> None:
    try:
        if os.environ.get("CTX_GUARD_BOUND_TOOLS", "1") == "0":
            return
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        tool_name, tool_args = normalize_tool_call(payload)
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return
        out = handler(payload, tool_args)
        if out:
            print(json.dumps(out))
    except Exception as e:
        print(f"[ctx-guard] pre_tool_bound failed: {e}", file=sys.stderr)
        return


if __name__ == "__main__":
    main()
