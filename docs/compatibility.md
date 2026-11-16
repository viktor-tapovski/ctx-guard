# Compatibility

ctx-guard is an early public release. The compatibility table records tested
support, not theoretical support.

| Integration | Hook capabilities used | Status |
|---|---|---|
| Claude Code | `PreToolUse`, `PostToolUse`, `UserPromptSubmit` | Tested |
| GitHub Copilot CLI | `PreToolUse`, `PostToolUse` | Tested |
| Other terminal agents | Agent-specific hook adapter required | Not supported yet |

## Features by agent

| Feature | Claude Code | GitHub Copilot CLI |
|---|---|---|
| Bash rewrite and compression | Yes | Yes |
| Package-install guard | Yes | Yes |
| Redacted output archive and `ctx-guard-recall` | Yes | Yes |
| Stats | Yes | Yes |
| Read and Grep bounds (`pre_tool_bound.py`) | Yes | Yes (`view` and `grep`; no prompt-named-file exception) |
| Subagent delegation | Yes | No |
| Context threshold warnings | Yes | No |

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

## Runtime requirements

- Bash with standard Unix command-line tools.
- Python 3.10 or newer.
- A Claude Code or GitHub Copilot CLI version that supports the hook events
  used by the installation script.
- macOS or Linux. Windows is not currently tested.

## Compatibility reports

When reporting a problem, include the operating system, shell, Python version,
agent version, hook event, and a redacted payload example. Do not include
secrets or unredacted command output.
