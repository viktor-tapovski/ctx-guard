# T10: Codex PostToolUse registration

**Depends on:** T03, T07 · **Phase:** 3 · **Size:** S

## Background

Codex CLI currently gets only a PreToolUse `^Bash$` hook from ctx-guard. Its
PostToolUse now receives `tool_response` and supports `additionalContext` and
`decision:"block"`, which replaces the whole result. It does **not** support
`updatedMCPToolOutput` or "ask". Unknown output fields mark the hook as
failed, so emit only documented keys. Hosted WebSearch fires no hooks. Docs:
https://learn.chatgpt.com/docs/hooks. Re-verify the current field names before
coding.

## Steps

1. Register a PostToolUse `^Bash$` hook for Codex in `install.sh`, pointing at
   `context_monitor.py`. The uninstall manifest must remove it exactly
   (`ctx-guard-uninstall` removes only manifest-owned entries).
2. In `context_monitor.py`, the Codex path:
   - scans `tool_response` text with T03;
   - marks taint per T07, including provenance for `curl`/`gh` commands;
   - emits `additionalContext` with the summary line;
   - uses `decision:"block"` only for decoded Tag-unicode hits, with a reason
     pointing to recall.
3. Add a fixture `tests/fixtures/codex/post_tool_use_bash.json`, modelled on
   the existing `pre_tool_use_bash.json`, and extend `tests/test_codex.py`.

## Acceptance criteria

- Install and uninstall round trip leaves the Codex config byte-identical (extend the existing tests).
- The emitted JSON contains only documented keys.
- CI passes.
- Update the Codex row in `docs/compatibility.md`.
