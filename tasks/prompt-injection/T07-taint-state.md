# T07: Session taint state library and marking

**Depends on:** T03 · **Phase:** 3 · **Size:** M

## Background

This is the core of the design: whole-session taint, a coarse version of
Microsoft FIDES. A coding agent always holds private data, so ctx-guard tracks
when **untrusted content entered** the session and gates consequential actions
afterwards (T08). Rationale: Willison's lethal trifecta and Meta's Agents Rule
of Two.

## Deliverable: `hooks/lib/taint.py` (stdlib)

```python
def taint_path(session_id: str) -> str            # STATE_DIR/<safe_session_id>.taint
def mark_taint(session_id, *, tool, source, rule, severity) -> None
def mark_confidential(session_id, *, source) -> None   # secret read AFTER taint
def read_taint(session_id) -> TaintState | None   # None on ANY error (fail open: untainted)
def clear_taint(session_id) -> None
```

- Store files in the existing mode-700 `STATE_DIR` (`ctx_guard_common.py`
  around line 50), keyed by `safe_session_id` (around line 162), alongside the
  existing `.fired` and `.tool-bytes` files.
- Write atomically: temp file + `os.replace`, mode 600.
- Content is JSON: `{"events": [{ts, tool, source, rule, severity}], "confidentiality_touched": bool}`.
  Cap it at 50 events, and **never store matched text**.
- `read_taint` must never raise. Copilot fails a crashed PreToolUse hook
  closed.

## Taint sources (who calls `mark_taint`)

1. **Provenance (no detector needed).** In PostToolUse (`context_monitor.py`)
   and in PreToolUse when the command is known:
   - WebFetch, WebSearch and `mcp__*` results;
   - Bash commands whose program is `curl`, `wget`, `gh issue view`,
     `gh pr view`, `gh api`, or `git clone` of a non-origin remote;
   - Read of files under `node_modules/`, `vendor/`, `site-packages/`, or a
     directory cloned this session.

   Mark these with `severity="provenance"`.
2. **High-severity scanner findings.** `taints(findings)` from T03, or T04's
   `ctx-guard-taint:` marker line seen in Bash output in PostToolUse.
3. **Tier-4 phrase hits alone never taint.**

## Clearing rules (implemented here; the CLI is T09)

- Cleared on SessionStart with source `startup` or `clear`.
- **Not** cleared on `compact`, because injected text can survive into the summary.
- An optional TTL from `CTX_GUARD_TAINT_TTL_HOURS` (default: none).

Wire SessionStart clearing in `install.sh` only if a SessionStart hook already
exists. Otherwise add the clear call to T12's hook, and note the dependency.

## Tests: `tests/test_taint.py`

- Round trip; atomic write (simulate a crash between write and replace).
- An unreadable or corrupt file → `None`, no exception.
- Path traversal in the session id is rejected (reuse the `safe_session_id` tests).
- No payload text is ever stored (feed a corpus attack, inspect the file).
- Each provenance source marks taint; each benign tool call doesn't.

## Acceptance criteria

All tests pass, and CI passes. A behaviour table goes in `docs/how-it-works.md`
(sources, clearing, and the fail-open decision, contrasted with parry-guard's
fail-closed choice).
