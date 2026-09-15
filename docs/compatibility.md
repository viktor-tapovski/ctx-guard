# Compatibility

ctx-guard is an early public release. The compatibility table records tested
support, not theoretical support.

| Integration | Hook capabilities used | Status |
|---|---|---|
| Claude Code | `PreToolUse`, `PostToolUse`, `UserPromptSubmit` | Tested |
| GitHub Copilot CLI | `PreToolUse`, `PostToolUse` | Tested |
| Other terminal agents | Agent-specific hook adapter required | Not supported yet |

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
