# Compatibility

ctx-guard is an early public release. The compatibility table records tested
support, not theoretical support.

| Integration | Hook capabilities used | Status |
|---|---|---|
| Claude Code | `PreToolUse`, `PostToolUse`, `UserPromptSubmit` | Tested |
| GitHub Copilot CLI | `PreToolUse`, `PostToolUse` | Tested |
| Antigravity CLI (`agy`) | `PreToolUse` | Fixtures from the docs; not yet run against a live agy |
| Codex CLI | `PreToolUse` | Fixtures from the docs; not yet run against a live Codex |
| Other terminal agents | Agent-specific hook adapter required | Not supported yet |

## Features by agent

| Feature | Claude Code | GitHub Copilot CLI | Antigravity CLI | Codex CLI |
|---|---|---|---|---|
| Bash rewrite and compression | Yes | Yes | Yes (`run_command`) | Read-only rewrites only (`Bash`, including `exec_command`); test runs and compound commands aren't wrapped, see below |
| Package-install guard | Yes | Yes | Yes | Yes (a warning is shown, not asked; see below) |
| Redacted output archive and `ctx-guard-recall` | Yes | Yes | Yes | Yes |
| Stats | Yes | Yes | Rewrites and blocks only (no tool output in `PostToolUse`) | Rewrites and blocks only |
| Read and Grep bounds (`pre_tool_bound.py`) | Yes | Yes (`view` and `grep`; no prompt-named-file exception) | No | No (Codex reads files through `Bash`) |
| Subagent delegation | Yes | No (prompt-hook context never reaches the model; see below) | No | No |
| Context threshold warnings | Yes | Estimated (from tool output; no transcript or context size in the payload) | No (`PostToolUse` can't add context) | No (not wired yet) |
| Status line segment | Yes | Yes | No (no status line setting documented) | No (not wired yet) |

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
  command runs. The hook command is wrapped in a shell check that prints the
  same allow when Python can't start at all (a removed interpreter or a
  failed import), and the hook allows the call itself after 10 seconds, under
  the 15-second timeout it's registered with, in case registry lookups are
  slow.
- agy's `PostToolUse` payload has no tool output and its response has no
  context field, so the threshold warnings and the Read and Grep bounds
  aren't wired. `agy --dangerously-skip-permissions` reportedly skips
  `PreToolUse` hooks entirely, so ctx-guard does nothing in that mode.

The payload fixtures in `tests/fixtures/antigravity/` are built from the
documented sample, not captured from a live session. Replace them with a
capture once one is available.

Codex CLI is wired from https://developers.openai.com/codex/hooks, checked
2026-11-17. ctx-guard adds one `PreToolUse` group with the matcher `^Bash$`
to `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`), only when that
directory exists or `codex` is on the PATH. Uninstall restores the file the
same way as for agy. Codex runs a user-level hook only after you review and
trust it, so after install open `/hooks` in Codex and trust the ctx-guard
hook; Codex asks again whenever the hook's definition changes, for example
after reinstalling to a different directory.

Codex takes Claude's `hookSpecificOutput` with four differences:

- It applies a rewrite (`updatedInput`) only together with `allow`, and the
  docs don't say whether that `allow` also skips Codex's approval policy. So
  ctx-guard sends it only for the read-only rewrites (`git status`, a capped
  `grep`, and so on). Test runs and compound commands, which it would wrap in
  `ctx-guard-run`, can run anything, so they pass through unchanged and your
  approval policy decides.

- It has no `ask`. A hook that returns one is marked failed and the command
  runs anyway. So a package warning goes out as a `systemMessage` for you
  and `additionalContext` for the model, with no permission decision, and
  Codex's own approval policy decides. The command isn't rewritten in that
  case, and no rewrite is counted in the stats.
- It reports output fields it doesn't know as a hook error, so ctx-guard
  sends only the documented ones.
- A hook that exits 0 with no output counts as success, so on any internal
  error the hook prints nothing and the original command runs.

The `PostToolUse` output shape for `Bash` isn't documented, so the threshold
warnings aren't wired for Codex yet. The payload fixture in
`tests/fixtures/codex/` is assembled from the documented field tables, not
captured from a live session.

## Runtime requirements

- Bash with standard Unix command-line tools.
- Python 3.9 or newer (macOS's own `/usr/bin/python3` is 3.9). `install.sh` uses the
  first new-enough `python3` on your PATH, or `CTX_GUARD_PYTHON` if set.
- A Claude Code, GitHub Copilot CLI, Antigravity CLI or Codex CLI version that supports the hook events
  used by the installation script.
- macOS or Linux. Windows is not currently tested.

## Compatibility reports

When reporting a problem, include the operating system, shell, Python version,
agent version, hook event, and a redacted payload example. Do not include
secrets or unredacted command output.
