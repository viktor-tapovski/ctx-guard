# T05: Capture real `tool_response` payloads

**Depends on:** none · **Phase:** 2 · **Size:** S · **Needs a live agent session; a sandbox-only subagent cannot complete it. Hand it to a human, or to a subagent running on a machine with the agents installed and logged in.**

## Why

Claude Code silently ignores an `updatedToolOutput` that doesn't match the
built-in tool's output schema. Only Bash's shape is documented
(`{stdout, stderr, interrupted, isImage}`). Before T06 can rewrite Read, Grep,
WebFetch, WebSearch, Agent and `mcp__*` output, we need the exact
`tool_response` shapes that agents send. The same is true for Copilot's
`postToolUse`, in both camelCase and PascalCase (ctx-guard uses PascalCase).

## Steps

1. Install a temporary logging hook (not committed) that writes the raw stdin
   payload to a file. Register it for PostToolUse with matcher `*` on Claude
   Code, and with `postToolUse` / `PostToolUse` on Copilot CLI.
2. In a scratch repo, trigger each tool once: Read (text file, image, PDF,
   with `offset`/`limit`), Grep (each `output_mode`), Glob, WebFetch,
   WebSearch, an Agent/Task call, one MCP tool, and Bash. On Copilot: `view`,
   `grep`, `bash`, a web fetch if one is available.
3. Scrub the payloads: replace paths, usernames, session ids and content with
   placeholders, but **preserve every key and type**. Save them as
   `tests/fixtures/claude/post_tool_use_<tool>.json` and
   `tests/fixtures/copilot/post_tool_use_<tool>_<casing>.json`.
4. Live test: for each Claude tool, return an `updatedToolOutput` built by
   copying `tool_response` and changing only the text fields. Confirm the
   model sees the change. Ask it to quote the first line. Record which tools
   honour it and the agent version.
5. Do the same for Copilot `modifiedResult` in both casings.
6. Write the findings to `tests/fixtures/claude/README.md` and
   `tests/fixtures/copilot/README.md`. Include the agent versions, which
   fields hold the model-visible text for each tool, and whether replacement
   worked.

## Acceptance criteria

- At least one scrubbed fixture per tool listed above. Missing tools are noted with the reason.
- A table of which tools honour output replacement for each agent, with versions.
- No personal data in the fixtures.
