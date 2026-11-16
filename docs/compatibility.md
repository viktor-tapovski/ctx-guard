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
| Read and Grep bounds (`pre_tool_bound.py`) | Yes | No (Copilot's file tools have different names; not registered) |
| Subagent delegation | Yes | No |
| Context threshold warnings | Yes | No |

The Read and Grep bounds use PreToolUse `updatedInput`, which replaces the
whole tool input, and return no permission decision. Checked against
https://code.claude.com/docs/en/hooks on 2026-11-16.

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
