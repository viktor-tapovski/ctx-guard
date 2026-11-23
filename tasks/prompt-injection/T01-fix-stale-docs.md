# T01: Fix stale docs about PostToolUse output

**Depends on:** none · **Phase:** 1 · **Size:** S

## Background

Two agents can now replace tool output from a PostToolUse hook:

- **Claude Code** (v2.1.121+): `hookSpecificOutput.updatedToolOutput` replaces
  the output of any tool. See anthropics/claude-code#18653, closed as shipped,
  and https://code.claude.com/docs/en/hooks#posttooluse-decision-control.
- **Copilot CLI**: `postToolUse` can return `modifiedResult`.
- **Codex CLI**: PostToolUse now has a documented `tool_response` input and
  `decision:"block"`, which replaces the whole result
  (https://learn.chatgpt.com/docs/hooks).

Several ctx-guard docs still say the opposite.

## Files

- `docs/how-it-works.md` around line 107 ("Built-in tool output can't be shrunk after the tool runs…")
- `hooks/pre_tool_bound.py` module docstring, line 6 ("PostToolUse cannot shrink a built-in tool's output…")
- `docs/compatibility.md` around line 117 ("The `PostToolUse` output shape for `Bash` isn't documented…")

## Steps

1. Re-fetch the three hook doc pages above and confirm each claim before you
   write it. If a page contradicts the report, follow the page and note it.
2. Reword each passage. Pre-bounding inputs is still useful: it avoids the tool
   doing the work at all, and it works on older versions and on agents that
   can't rewrite output. The new wording should say that, for example: "Claude
   Code ≥2.1.121 and Copilot CLI can replace tool output in PostToolUse, but
   ctx-guard bounds input up front because it is cheaper and works on every
   supported version."
3. Do not change any behaviour. This task is docs only.

## Acceptance criteria

- No remaining claim in `docs/` or hook docstrings that PostToolUse cannot
  modify output (check with `grep -rn "can't be shrunk\|cannot shrink\|isn't documented" docs hooks`).
- The new wording names the minimum Claude Code version and cites the doc URL.
- CI commands pass.
