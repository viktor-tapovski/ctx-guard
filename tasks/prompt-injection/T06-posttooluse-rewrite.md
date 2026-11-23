# T06: PostToolUse output rewrite in `context_monitor.py`

**Depends on:** T03, T05 · **Phase:** 2 · **Size:** M

## Background

`hooks/context_monitor.py` already runs on every PostToolUse on Claude Code
and Copilot. It reads tool output via `normalize_tool_result_text`
(`hooks/lib/ctx_guard_common.py`, around line 143). That makes it the natural
host for scanning built-in and MCP tool output, which today reaches the model
unfiltered (see `SECURITY.md`). `build_pretooluse_decision` (around line 169)
already shows how ctx-guard emits Claude and Copilot shapes side by side.

## Steps

1. Scope: Read, Grep, Glob, WebFetch, WebSearch, Agent/Task and `mcp__*` on
   Claude, plus the Copilot equivalents (`view`, `grep`, …). Skip Bash output
   that came through `ctx-guard-run`, because T04 already sanitized it. Detect
   this by the summary or compression header so it isn't scanned twice.
2. Run `injection_scan.scan` on the normalized text.
3. If findings exist:
   - **Claude Code ≥ 2.1.121**: build `updatedToolOutput` by deep-copying the
     received `tool_response` and replacing **only** the text fields T05
     identified for that tool. If the tool is not in T05's verified table,
     don't rewrite; fall back to `additionalContext`.
   - **Copilot**: emit `modifiedResult: {resultType: "success",
     textResultForLlm}`, plus the nested Claude-shaped field, following T05's
     findings on PascalCase vs camelCase.
   - **Always** emit `additionalContext` with `summary_line(findings)` as a
     fallback. Keep it within Copilot's 10 KB limit.
   - Reserve `decision: "block"` for very high-confidence hits (decoded Tag
     text). The reason must name the rule and say the full output is
     available via recall, if there is a run id.
4. Version gate: get the Claude Code version from the payload if present.
   Otherwise use a `CTX_GUARD_CLAUDE_VERSION` env override, otherwise assume
   it is unsupported and only annotate. Don't spawn `claude --version` in the
   hook.
5. Pick up T04's `ctx-guard-taint:` marker lines and T03's `taints(findings)`,
   and call T07's `mark_taint` (if T07 has landed; otherwise leave a clearly
   marked call site).
6. Existing context-monitor behaviour (the threshold warnings) must keep
   working. Merge the outputs into one JSON response.

## Tests

- Use T05 fixtures: for each tool, an attack text in the response produces a
  rewritten object with the **same key set and types** as the input, and only
  the text fields change.
- Unknown tool → no rewrite, `additionalContext` only.
- Old version → annotate only.
- Benign fixtures → output identical to today.
- Hook raises internally → exits 0 with no output change (fail open).
- `CTX_GUARD_INJECTION=off` → no change.

## Acceptance criteria

All of the above tests pass, and CI passes. `docs/how-it-works.md` gains a
short subsection describing the rewrite and its version gate.
