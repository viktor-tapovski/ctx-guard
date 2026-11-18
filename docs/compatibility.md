# Compatibility

ctx-guard is an early public release. The compatibility table records tested
support, not theoretical support.

| Integration | Hook capabilities used | Status |
|---|---|---|
| Claude Code | `PreToolUse`, `PostToolUse`, `UserPromptSubmit` | Tested |
| GitHub Copilot CLI | `PreToolUse`, `PostToolUse` | Tested |
| Antigravity CLI (`agy`) | `PreToolUse` | Fixtures from the docs; not yet run against a live agy |
| Other terminal agents | Agent-specific hook adapter required | Not supported yet |

## Features by agent

| Feature | Claude Code | GitHub Copilot CLI | Antigravity CLI |
|---|---|---|---|
| Bash rewrite and compression | Yes | Yes | Yes (`run_command`) |
| Package-install guard | Yes | Yes | Yes |
| Redacted output archive and `ctx-guard-recall` | Yes | Yes | Yes |
| Stats | Yes | Yes | Rewrites and blocks only (no tool output in `PostToolUse`) |
| Read and Grep bounds (`pre_tool_bound.py`) | Yes | Yes (`view` and `grep`; no prompt-named-file exception) | No |
| Subagent delegation | Yes | No (prompt-hook context never reaches the model; see below) | No |
| Context threshold warnings | Yes | Estimated (from tool output; no transcript or context size in the payload) | No (`PostToolUse` can't add context) |
| Status line segment | Yes | Yes | No (no status line setting documented) |

The Read and Grep bounds use PreToolUse `updatedInput`, which replaces the
whole tool input, and return no permission decision. Checked against
https://code.claude.com/docs/en/hooks on 2026-11-16.

Under Copilot CLI the hook is registered with the PascalCase `PreToolUse`
event and the matcher `Read|Grep`, which Copilot maps to its `view`, `grep`
and `rg` tools (https://docs.github.com/en/copilot/reference/hooks-configuration,
checked 2026-11-16). The payload carries Claude tool names but Copilot's own
arguments: `view` takes `path` and `view_range: [start, end]`, and `grep`
takes `pattern`, `paths`, `output_mode` and `head_limit`. The docs don't list
tool arguments, and don't say whether `modifiedArgs` applies without a
permission decision, so both were read from the Copilot CLI 1.0.62 source:
it applies `hookSpecificOutput.updatedInput` (or flat `modifiedArgs`)
whenever the decision is not deny or ask, and passes `additionalContext` to
the model. The payload has no `transcript_path` or `tool_use_id`, so the
prompt-named-file exception is skipped and stats pair the PreToolUse and
PostToolUse calls by session and file path.

Delegation and threshold warnings were checked in the Copilot CLI 1.0.62
source on 2026-11-16. A `userPromptSubmitted` hook's `additionalContext` is
parsed but never added to the conversation; only `modifiedPrompt` and the
block or handled fields are acted on. ctx-guard doesn't rewrite your prompt,
so `auto_delegate.py` stays unregistered for Copilot. A `PostToolUse` hook's
`additionalContext` is appended to the tool result the model sees, so the
threshold warnings do reach it. No hook payload carries the context size, so
`context_monitor.py` estimates it from the session's total tool output
against `CTX_GUARD_COPILOT_WINDOW_TOKENS` (default 128000) and labels each
warning as estimated.

Antigravity CLI (`agy`, which replaced Gemini CLI for individual accounts on
2026-06-18) is wired from https://antigravity.google/docs/hooks/, checked
2026-11-17. ctx-guard adds a hook named `ctx-guard` to
`~/.gemini/config/hooks.json`, only when `~/.gemini/antigravity-cli` exists
or `agy` is on the PATH. Uninstall writes the file's previous text back byte
for byte, or removes only the `ctx-guard` key if you edited the file after
install. The hook matches `run_command`; its `args.CommandLine` is read as
the command, and a rewrite is returned as `overwrite: {"CommandLine": ...}`,
which agy merges into the tool arguments. The decision is `allow` (which
defers to your agy permission policy, so a rewrite is never auto-approved),
`ask` for a package warning, or `deny` for a blocked install.

Two agy differences change what ctx-guard can do there:

- agy blocks the tool call when a `PreToolUse` hook crashes, times out or
  prints no decision (reported at
  https://agenticcontrolplane.com/blog/antigravity-hooks-reference; the
  official page doesn't say). So the hook always prints a decision, and on
  any internal error it prints `{"decision": "allow"}` and the original
  command runs.
- agy's `PostToolUse` payload has no tool output and its response has no
  context field, so the threshold warnings and the Read and Grep bounds
  aren't wired. `agy --dangerously-skip-permissions` reportedly skips
  `PreToolUse` hooks entirely, so ctx-guard does nothing in that mode.

The payload fixtures in `tests/fixtures/antigravity/` are built from the
documented sample, not captured from a live session. Replace them with a
capture once one is available.

## Runtime requirements

- Bash with standard Unix command-line tools.
- Python 3.10 or newer.
- A Claude Code, GitHub Copilot CLI or Antigravity CLI version that supports the hook events
  used by the installation script.
- macOS or Linux. Windows is not currently tested.

## Compatibility reports

When reporting a problem, include the operating system, shell, Python version,
agent version, hook event, and a redacted payload example. Do not include
secrets or unredacted command output.
