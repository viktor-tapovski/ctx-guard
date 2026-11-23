# Hook integration points for a prompt-injection defense in ctx-guard

Research date: 2026-11-23. Agent docs change often (Claude Code ships near-daily). Every doc claim below is from the page as fetched on that date. Version numbers matter: re-check them before shipping. Local ctx-guard paths are relative to the repo root (/home/user/ctx-guard).

## 1. Claude Code hook lifecycle: where a defense can act and what it can do

### Takeaway
Claude Code now lets a `PostToolUse` hook **replace** the result of any tool, built-in tools included, through `hookSpecificOutput.updatedToolOutput`. This has worked since v2.1.121, and Anthropic closed the feature request as "shipped" in Aug 2026. Read, Grep, WebFetch, Bash and MCP output can therefore be sanitized before Claude sees it, not only annotated. `PreToolUse` can allow, deny, ask or defer a call and can rewrite its input. `UserPromptSubmit`, `SessionStart` and `PostToolBatch` can only add context or block. ctx-guard's own docs still say built-in output "can't be shrunk after the tool runs". That statement is out of date.

### Cited Findings
**PreToolUse**
- The decision lives in `hookSpecificOutput.permissionDecision` and has four outcomes: `"allow"` skips the permission prompt, `"deny"` prevents the call, `"ask"` prompts the user, and `"defer"` ends the turn (only in `-p` non-interactive mode). "Deny and ask rules are still evaluated regardless of what the hook returns." — [Claude Code hooks reference, PreToolUse decision control](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)
- `updatedInput` "Replaces the entire input object". Permission rules are evaluated "against the input your hook returns, not the input Claude sent". Combine it with `"allow"` to auto-approve or with `"ask"` to show the modified input to the user. — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)
- "When multiple PreToolUse hooks return different decisions, precedence is `deny` > `defer` > `ask` > `allow`." — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)
- For `"ask"`, the `permissionDecisionReason` is shown to the user in the permission prompt. For `"deny"` it is shown to Claude. The prompt labels the hook's source (`[settings]`, `[plugin:<name>]`, `[skill]`). — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)
- In auto mode, `updatedInput` returned without a `permissionDecision` can be denied with "a hook changed this call's input after auto mode reviewed it". A `command` hook in settings can change Bash/PowerShell/Monitor/Edit/Write/NotebookEdit calls without that denial in some session types. — [Claude Code hooks reference, PreToolUse updatedInput in auto mode](https://code.claude.com/docs/en/hooks#pretooluse-updatedinput-in-auto-mode)
- "The hook can deny the call, but staying silent doesn't approve it." Exit 0 with no output means the normal permission flow applies. — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)

**PostToolUse**
- Input carries both `tool_input` and `tool_response`. "The exact schema for both depends on the tool." Matches on tool name, with the same values as PreToolUse. — [Claude Code hooks reference, PostToolUse input](https://code.claude.com/docs/en/hooks#posttooluse-input)
- Output fields:
  - `decision: "block"` "adds the `reason` next to the tool result. Claude still sees the original output; to replace it, use `updatedToolOutput`".
  - `additionalContext` is added alongside the tool result.
  - `classifierContext` is a note for the auto-mode classifier (v2.1.236+).
  - `updatedToolOutput` "Replaces the tool's output with the provided value before it is sent to Claude. The value must match the tool's output shape".
  - `updatedMCPToolOutput` is MCP-only: "Prefer `updatedToolOutput`, which also works for built-in tools".
  
  — [Claude Code hooks reference, PostToolUse decision control](https://code.claude.com/docs/en/hooks#posttooluse-decision-control)
- Warning from the same page:
  - `updatedToolOutput` "only changes what Claude sees. The tool has already run". OpenTelemetry and analytics capture the original output.
  - "For built-in tools, a value that doesn't match the tool's output schema is ignored and the original output is used. MCP tool output is passed through without schema validation."
  
  The documented Bash shape is `{stdout, stderr, interrupted, isImage}`. — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks#posttooluse-decision-control)
- Feature request anthropics/claude-code#18653 ("Tool result transform hook for content sanitization", opened 2026-01-16, motivated by prompt-injection defense and DLP) was closed as completed on 2026-08-17. A collaborator wrote: "`PostToolUse` hooks can now rewrite a tool's result before it reaches Claude: return `hookSpecificOutput.updatedToolOutput` (works for every tool, including Bash/WebFetch/MCP)... Available since v2.1.121". A commenter quotes the v2.1.121 changelog: "PostToolUse hooks can now replace tool output for all tools via `hookSpecificOutput.updatedToolOutput` (previously MCP-only)". — [GitHub issue #18653](https://github.com/anthropics/claude-code/issues/18653)
- `classifierContext` is read by the classifier as "unverified, application-provided context". The docs warn: "don't copy untrusted tool output or third-party text into it". The classifier "omits read-only lookups such as file reads and searches", and a note on those calls is discarded. The notes for one tool call are capped at 2,000 characters. — [Claude Code hooks reference, Annotate a result for the auto mode classifier](https://code.claude.com/docs/en/hooks#annotate-a-result-for-the-auto-mode-classifier)
- Exit code 2 on PostToolUse cannot block: it "Shows stderr to Claude; the tool already ran." — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- `PostToolUse` fires concurrently when Claude makes parallel tool calls. The newer `PostToolBatch` event fires once per batch, before the next model request. Its `tool_response` is "the same content the model receives" (for Read: "line-number-prefixed text"). It can add `additionalContext`, and `decision: "block"` "stops the agentic loop before the next model call". — [Claude Code hooks reference, PostToolBatch](https://code.claude.com/docs/en/hooks#posttoolbatch)

**Context injection (all events)**
- `additionalContext` is wrapped in a system reminder at the point the hook fired. Any `<system-reminder>` tag inside it is escaped to `&lt;`. Values over 10,000 characters go to a file, and Claude gets a 2,000-character preview. — [Claude Code hooks reference, Add context for Claude](https://code.claude.com/docs/en/hooks#add-context-for-claude)
- "Text framed as out-of-band system commands can trigger Claude's prompt-injection defenses, which causes Claude to surface the text to you instead of treating it as context." The docs advise writing context "as factual statements rather than imperative system instructions". — [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)

**UserPromptSubmit / SessionStart**
- UserPromptSubmit can block with `decision: "block"`. Plain stdout or `additionalContext` is added alongside the prompt. Its timeout defaults to 30 s, and on timeout the context is discarded while the prompt still goes through. — [Claude Code hooks reference, UserPromptSubmit](https://code.claude.com/docs/en/hooks#userpromptsubmit)
- SessionStart adds `additionalContext` "at the start of the conversation, before the first prompt". Its matchers are `startup`, `resume`, `clear`, `compact` and `fork`, and the docs ask to "keep these hooks fast". — [Claude Code hooks reference, SessionStart](https://code.claude.com/docs/en/hooks#sessionstart)

### Inferences
- **Lifecycle map for Claude Code:**
  - **SessionStart (startup/resume/compact/clear):** reset or re-announce taint, and add a one-line note on treating fetched content as data.
  - **UserPromptSubmit:** could clear taint when the user explicitly re-authorizes. It can't see tool output.
  - **PreToolUse (Bash, WebFetch, Write/Edit, mcp__\*):** the gate. Deny, ask, or rewrite through a wrapper. When tainted, return ask for risky shell or network commands.
  - **PostToolUse (Read, Grep, WebFetch, WebSearch, Bash, Agent, mcp__\*):** scan, then either (a) `updatedToolOutput` to strip or neutralize matched spans, (b) `additionalContext` or `decision:block` to warn, or (c) set taint.
  - **PostToolBatch:** one consolidated warning per batch, sparing the model N concurrent warnings.
- Prefer one hook that returns both the input rewrite and the decision over two hooks that each return part. ctx-guard's `pre_bash_rewrite.py` can already do this: it returns `ask` together with `new_args` for package warnings. A separate taint hook would run in parallel with it, and the docs give precedence only for decisions, not for how `updatedInput` from parallel hooks merges.
- Because invalid `updatedToolOutput` is silently ignored for built-in tools, the safest construction is to copy the `tool_response` object the hook received and replace only the text field(s). That way the shape matches by construction. If the result is ignored, the hook should still have emitted `additionalContext`.
- Warnings should be phrased as facts ("The output of WebFetch https://x contains text resembling instructions to the agent: …"), not as imperative "SYSTEM:" commands, per the docs' own advice.

### Gaps
- The docs give the input schema for Read, Grep and WebFetch but not their `tool_response` output schema. Only Bash's shape is documented. The exact field to replace for Read (likely a nested file/content object) and for WebFetch is not documented. It needs a captured payload.
- No independent test was found showing `updatedToolOutput` actually replacing WebFetch or Read output in a live session. Only the changelog quote and the maintainer comment confirm it.
- It is not documented how `updatedToolOutput` from two parallel PostToolUse hooks is resolved. The docs only say a `classifierContext` note is dropped "if ... another hook's rewrite replaces it", which implies last-or-one-wins.

## 2. Copilot CLI, Codex CLI and Antigravity CLI equivalents and their limits

### Takeaway
- **Copilot CLI** can also replace tool output (`postToolUse` → `modifiedResult`) and append context.
- **Codex CLI** cannot rewrite output but can replace the whole result with feedback (`decision: "block"`) or add context. It has no `ask`, and its hosted WebSearch is invisible to hooks.
- **Antigravity CLI's** `PostToolUse` receives no tool output and can return nothing. For agy the only levers are pre-execution decisions, which are richer (`force_ask`, `deny_unless_prior_grant`), and ctx-guard's own `ctx-guard-run` wrapper.

### Cited Findings
**GitHub Copilot CLI**
- Events: `preToolUse`, `postToolUse`, `postToolUseFailure`, `userPromptSubmitted`, `userPromptTransformed`, `sessionStart`, `permissionRequest`, `agentStop`, `subagentStart/Stop`, `preCompact`, `errorOccurred` and others. — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration)
- `preToolUse` takes `permissionDecision` of `allow`/`deny`/`ask`, plus `modifiedArgs`. "In CLI autopilot mode, `ask` is denied." — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration)
- The `postToolUse` input has `toolResult: {resultType, textResultForLlm}`. In PascalCase mode it is `tool_result: {result_type, text_result_for_llm}`. Output fields:
  - `modifiedResult` (`{resultType: "success", textResultForLlm}`) replaces the result. It is "honored by both SDK programmatic hooks and command/HTTP config-file `postToolUse` hooks".
  - `additionalContext` is appended to `textResultForLlm`, joined across hooks with a blank line, and capped at 10 KB.
  
  — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration)
- Copilot maps its tool names to Claude names in PascalCase mode: `view`→Read, `grep`/`rg`→Grep, `web_fetch`→WebFetch, `web_search`→WebSearch, `bash`→Bash, `task`→Agent. — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration)
- Failure behavior:
  - "Command `preToolUse` hooks are fail-closed on errors": a crash or non-zero exit denies the call, and exit 2 always denies.
  - Command-hook timeouts are always fail-open.
  - Other events log and skip on failure.
  - `timeoutSec` defaults to 30.
  
  — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration)
- `userPromptSubmitted`'s `modifiedPrompt` "is only honored by SDK programmatic hooks". ctx-guard's own source check found that its `additionalContext` never reaches the model (Copilot 1.0.62). — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration); [docs/compatibility.md](docs/compatibility.md)

**Codex CLI** (docs moved: developers.openai.com/codex/hooks now 308-redirects to learn.chatgpt.com/docs/hooks)
- Events: `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStart/Stop`, `Stop`, `PreModelRequest`, `PostModelRequest`, `Interrupt`, `SessionStart`, `SessionEnd`. — [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- In `PreToolUse`, `"ask"` is "parsed but not supported yet", and `updatedInput` applies with `allow`. — [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- `PostToolUse`:
  - Input includes `tool_response` (for MCP tools, the MCP call result).
  - `decision: "block"` doesn't undo the command, but Codex "replaces the tool result with" the feedback. Exit 2 does the same.
  - `continue: false` also replaces the result.
  - `additionalContext` is supported.
  - `updatedMCPToolOutput` and `suppressOutput` are not supported, and the hook is then marked failed.
  
  — [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- Hooks fire for shell (`Bash`, `exec_command`), `apply_patch`, MCP tools and other local function tools. Hosted tools such as WebSearch get "No" for both PreToolUse and PostToolUse. — [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- Default timeout is 600 s. Non-managed hooks must be reviewed and trusted, and trust is tied to the hook's hash. — [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- ctx-guard records that Codex reports unknown output fields as a hook error. It also notes that Codex's PostToolUse output shape for Bash "isn't documented", but that predates the current page, which now documents block-replaces-result. — [docs/compatibility.md](docs/compatibility.md)

**Antigravity CLI (agy)**
- Events: SessionStart, PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop. — [Antigravity hooks](https://antigravity.google/docs/hooks/)
- PreToolUse `decision` takes allow, auto_approve, deny, ask, force_ask or deny_unless_prior_grant. `overwrite` is shallow-merged into the args. — [Antigravity hooks](https://antigravity.google/docs/hooks/)
- The PostToolUse input has `toolCall` and an optional `error`, with no output field. The output "Returns an empty JSON object `{}`". — [Antigravity hooks](https://antigravity.google/docs/hooks/)
- PreInvocation fires before each model call, has `lastUserInput`, and can return `injectSteps` or `terminationBehavior: "terminate"`. Timeout defaults to 30 s. — [Antigravity hooks](https://antigravity.google/docs/hooks/)
- Third-party report: agy blocks the call when a PreToolUse hook crashes, times out or prints no decision. `--dangerously-skip-permissions` reportedly skips PreToolUse hooks. — cited in [docs/compatibility.md](docs/compatibility.md), from [agenticcontrolplane.com](https://agenticcontrolplane.com/blog/antigravity-hooks-reference)

### Inferences
- **Capability matrix for an injection defense:**

  | Agent | Output replace | Output warn | Pre-gate ask | Pre-gate deny |
  |---|---|---|---|---|
  | Claude Code | yes (`updatedToolOutput`, v2.1.121+) | yes | yes | yes |
  | Copilot | yes (`modifiedResult`) | yes (`additionalContext`) | yes, but denied in autopilot | yes |
  | Codex | whole-result replace only (`decision:block`) | yes (`additionalContext`) | no (ctx-guard already downgrades to `systemMessage`+context) | yes |
  | agy | no | no | yes (`ask`/`force_ask`) | yes |

- For agy, a sanitizer has to sit in the data path: the `ctx-guard-run` wrapper, or an `overwrite` that routes `run_command` through it. Pre-invocation `injectSteps` might carry a warning, but its semantics weren't researched.
- Copilot's PascalCase mode (which ctx-guard uses) may not accept `modifiedResult` in the same flat shape. The emitter should output both the flat Copilot field and Claude's nested `hookSpecificOutput.updatedToolOutput`, the same way `build_pretooluse_decision` already emits both shapes.

### Gaps
- Copilot docs don't say whether PascalCase `PostToolUse` honors `hookSpecificOutput.updatedToolOutput`, or whether `modifiedResult` applies to MCP tools. ctx-guard checked Copilot source for PreToolUse (1.0.62) but not for this field.
- The semantics of Antigravity `injectSteps` and whether its PostToolUse will gain output were not researched.
- Whether Codex `decision:block` text replaces the result for `apply_patch` and MCP tools too, or only shell, is not stated explicitly.

## 3. What ctx-guard can intercept today vs. not, and how to close the gap

### Takeaway
Today ctx-guard **transforms** output only for Bash commands it wraps in `ctx-guard-run`: test runners, builds and compound commands. That pipeline already redacts secrets and fails closed, so it is a ready insertion point for injection scanning and neutralization on Claude, Copilot and agy. Outside the wrapper ctx-guard only **observes**:
- rewritten commands such as `git log` and `cat` → `head -c`
- all built-in Read/Grep/WebFetch output
- MCP output

`context_monitor.py` is already registered on PostToolUse with matcher `*` for Claude and Copilot and receives every tool's output text. It can host an output scanner that sets taint and, now that `updatedToolOutput` exists, rewrites the output.

### Cited Findings
- **ctx-guard wiring per agent:**
  - Claude Code: `PreToolUse` `Bash` → `pre_bash_rewrite.py`; `PreToolUse` `Read|Grep` → `pre_tool_bound.py`; `PostToolUse` `*` → `context_monitor.py`; `UserPromptSubmit` → `auto_delegate.py`.
  - Copilot: PascalCase `PreToolUse` Bash and Read|Grep, `PostToolUse` `*`, all with `timeoutSec: 15`.
  - agy: only `PreToolUse` on `run_command`, with timeout 15 s.
  - Codex: only `PreToolUse` `^Bash$`, with timeout 15 s.
  
  — [install.sh](install.sh) lines 357-410; [hooks/lib/agent_hooks.py](hooks/lib/agent_hooks.py)
- `ctx-guard-run --filter` is piped as `(set -o pipefail; { <cmd>\n} 2>&1 | ctx-guard-run --filter <agent> <program>)`. The original command stays verbatim so permission checks see it. — [hooks/pre_bash_rewrite.py](hooks/pre_bash_rewrite.py) `wrap_generic`; [docs/how-it-works.md](docs/how-it-works.md)
- `redact_file` in `ctx-guard-run` runs before anything is archived or returned. If the sanitizer fails, "the archive holds only `ctx-guard: output withheld because redaction failed` and nothing raw is kept or returned (fail closed)". — [SECURITY.md](SECURITY.md)
- "Output from commands it only rewrites (such as `git status`) and from the agent's built-in file tools reaches the agent unfiltered." — [SECURITY.md](SECURITY.md)
- `context_monitor.py` already extracts the delivered output text across runtimes with `normalize_tool_result_text`, which reads `tool_response` / `toolResult` / `textResultForLlm` / `output` / `stdout` / `content`. It returns context through `build_posttooluse_context`, which emits both flat and nested `additionalContext`. — [hooks/context_monitor.py](hooks/context_monitor.py); [hooks/lib/ctx_guard_common.py](hooks/lib/ctx_guard_common.py)
- ctx-guard's docs and docstrings say "Built-in tool output can't be shrunk after the tool runs" and "PostToolUse cannot shrink a built-in tool's output". Both statements were superseded by `updatedToolOutput` (v2.1.121+) on Claude Code. — [docs/how-it-works.md](docs/how-it-works.md) §6; [hooks/pre_tool_bound.py](hooks/pre_tool_bound.py) docstring; [Claude Code hooks reference](https://code.claude.com/docs/en/hooks#posttooluse-decision-control)
- Inside Claude Code's Bash sandbox, `ctx-guard-run` cannot write the canonical state file, so it spools to `$TMPDIR/ctx-guard-<uid>/state`, and `context_monitor.py` drains the spool. — [hooks/lib/ctx_guard_common.py](hooks/lib/ctx_guard_common.py) `spool_files`/`drain_spools`
- For WebFetch, a PostToolUse hook "sees only the extracted answer, not the raw page", because WebFetch runs a small model over the page first. Injection-sentinel recommends browser MCP tools or WebSearch for stronger detection. — [CodeCombustion/injection-sentinel](https://github.com/CodeCombustion/injection-sentinel)

### Inferences
**Options to close the gap, cheapest and most portable first:**
1. **Scan inside `ctx-guard-run`.** It already transforms output on Claude, Copilot and agy. Add a stdlib detector next to `redact_file` covering:
   - invisible Unicode (U+200B–U+200F, U+2060–U+2064, U+E0000 tag chars, bidi overrides U+202A–U+202E and U+2066–U+2069)
   - instruction-override phrases
   - markdown-image or URL exfiltration patterns
   
   Strip the invisible characters, then fence the matched lines (e.g. `[ctx-guard: possible injected instruction on line N]`). This inherits the existing fail-closed behavior. It does not cover Codex, where wraps aren't sent.
2. **Scan in `context_monitor.py` (PostToolUse `*`) on Claude and Copilot.** This covers Read, Grep, WebFetch, WebSearch, Agent results and `mcp__*`. On a hit:
   - write a taint marker
   - return `additionalContext` with a factual warning
   - on Claude Code ≥ 2.1.121, also return `updatedToolOutput`: a copy of `tool_response` with invisible characters stripped and the flagged spans wrapped or elided
   - on Copilot, also return `modifiedResult` with `{resultType: "success", textResultForLlm: <sanitized>}`
   
   Version-gate it, because older Claude Code versions ignore the field.
3. **Register Codex `PostToolUse` `^Bash$`** (currently unwired). Use `additionalContext` for warnings. For a high-confidence hit, `decision:"block"` with a reason replaces the result entirely: the reason names the run id, so the model can `ctx-guard-recall` the redacted archive deliberately.
4. **Route raw fetches through a wrapper.** In PreToolUse on Bash, the `curl`/`wget` patterns currently fall into the default path and are not wrapped. Wrap them in `ctx-guard-run --filter` (as runners are) so fetched bodies are scanned and archived.
5. **Optionally deny WebFetch** in a strict mode (`CTX_GUARD_INJECTION=strict`), with a reason telling the model to use `curl -sL <url> | ctx-guard-run --filter …`. This trades the WebFetch summarizer for full-content scanning. It is opt-in because it changes agent behavior and loses WebFetch's domain permission rules.

**Remaining risk.** Rewritten-only commands (`cat` → `head -c 8000`, `grep … | head`) are still unscanned in the data path. On Claude and Copilot the PostToolUse scan covers them. On agy it doesn't. The `cat-large` and `grep-unbounded` rewrites could pipe through `ctx-guard-run --filter` instead of `head`.

### Gaps
- The Claude Code `tool_response` schema for Read, Grep and WebFetch (needed to build a valid `updatedToolOutput`) is undocumented. A payload capture is needed.
- No measurement of how often WebFetch's summarizer passes injected instructions through intact. Some sources suggest it can either launder or preserve them, but no data was found.

## 4. Existing open-source hook-based injection guards

### Takeaway
Most existing Claude Code guards are **PostToolUse warn-only regex or ML scanners** (Lasso, injection-sentinel, dwarvesf, nova-tracer). Parry-guard is the most complete. It adds a **taint state**: a PostToolUse hit taints the project and PreToolUse enforces it. It also adds Unicode and homoglyph checks, Aho-Corasick phrase matching, and an ONNX DeBERTa classifier behind a daemon. None of the surveyed projects documents use of `updatedToolOutput` to sanitize output. Snyk (which acquired Invariant Labs, maker of mcp-scan) targets config and MCP scanning plus asynchronous hooks, not inline output sanitization.

### Cited Findings
- **Lasso Security claude-hooks:**
  - PostToolUse on `Read`, `WebFetch`, `Bash`, `Grep`, `Task` and `mcp__*`.
  - Regex in `patterns.yaml` across five categories: Instruction Override, Role-Playing/DAN, Encoding/Obfuscation, Context Manipulation, Instruction Smuggling.
  - Severity high/medium/low, with low marked "high false positive risk".
  - "The defender warns but does not block."
  - "No API calls, instant scanning", with no measurements published.
  - Python and TypeScript implementations, MIT license.
  
  — [lasso-security/claude-hooks](https://github.com/lasso-security/claude-hooks)
- **parry-guard (vaporif/parry):**
  - Registers PreToolUse (example timeout 1000 ms), PostToolUse (5000 ms) and UserPromptSubmit (2000 ms).
  - "PostToolUse scans tool output for injection and secrets. A hit taints the project." PreToolUse enforces the taint and also checks CLAUDE.md, exfiltration, destructive operations and sensitive paths.
  - Detection layers: Unicode (invisible, homoglyph, RTL); Aho-Corasick substrings; 40+ secret regexes; DeBERTa v3 in 256-char chunks with a 0.7 threshold; tree-sitter Bash exfiltration analysis; source-to-sink script analysis.
  - Latency: ~10 ms ONNX for short text, ~580 ms cold start (daemon plus model), ~8 ms on a cache hit, ~50-70 ms per chunk in fast mode, ~1.5 s in full mode.
  - "fails closed". It turns `ask` into block for Codex. `parry-guard reset` clears state.
  - Early development: "expect bugs and false positives".
  
  — [vaporif/parry](https://github.com/vaporif/parry); [parry-guard on PyPI](https://pypi.org/project/parry-guard/)
- **injection-sentinel:**
  - PostToolUse only, for Claude Code (WebFetch, WebSearch, browser MCP) and Codex (browser MCP; hosted web search not visible).
  - Patterns in four categories: header-harvest, exfil, disclosure, override.
  - "never changes the tool result". It tells the agent the text is data, alerts the user and logs the site.
  - Admits that rephrased injections evade it and that articles about injection cause false hits.
  
  — [CodeCombustion/injection-sentinel](https://github.com/CodeCombustion/injection-sentinel)
- **Search-result descriptions only (READMEs not fetched; verify before citing in a final report):**
  - nova-tracer: keyword, semantic-ML and LLM tiers, running passively in PostToolUse.
  - dwarvesf/claude-guardrails: regex scanner on Read, WebFetch and Bash output that warns as JSON to avoid false-positive blocks.
  - Tough-Respawn/defensive-prompt-injection: PreToolUse asks before high-risk operations.
  - CloneGuard: ONNX embedding classifier.
  
  — [nova-tracer](https://github.com/fr0gger/nova-claude-code-protector); [dwarvesf/claude-guardrails](https://github.com/dwarvesf/claude-guardrails/blob/main/CLAUDE.md); [defensive-prompt-injection](https://github.com/Tough-Respawn/defensive-prompt-injection); [cloneguard](https://github.com/prodnull/cloneguard)
- **Snyk / Invariant:**
  - Snyk acquired Invariant Labs (maker of MCP-scan and Invariant Guardrails).
  - Snyk Studio moved from rules to asynchronous hooks-based guardrails that run background scans on code and dependency changes.
  - `uvx snyk-agent-scan@latest` scans MCP configs and skills.
  - Agent Guard runtime enforcement was reported to be in private preview (secondary source, May 2026).
  
  — [Snyk Labs: Invariant Labs](https://labs.snyk.io/resources/snyk-labs-invariant-labs); [Snyk Studio async hooks](https://updates.snyk.io/snyk-studio-introducing-asynchronous-hooks-based-guardrails-for-ai-agents/); [Snyk Claude Code guide](https://docs.snyk.io/evo-by-snyk/agentic-security-with-snyk-studio/quickstart-guides/claude-code-guide)

### Inferences
- The common pattern is that regex and phrase lists are cheap but evadable and prone to false positives. Every project therefore defaults to warn rather than block for output scanning, and reserves blocking for pre-execution of risky actions.
- ctx-guard is stdlib-only, so it can match the Lasso and injection-sentinel tier (regex, phrases, Unicode) but not parry's ML tier. Its differentiators would be:
  - neutralizing inside `ctx-guard-run`, which already sits in the data path
  - `updatedToolOutput` sanitization, which none of the surveyed projects documents
  - consistent behavior across four agents
  - taint-to-ask gating that reuses the existing ask/deny plumbing

### Gaps
- No published precision or recall numbers were found for Lasso, injection-sentinel or dwarvesf. parry publishes latency but not detection accuracy.
- Whether Snyk Agent Guard or any Invariant product ships a Claude Code PostToolUse sanitizer could not be confirmed.

## 5. Implementing taint state across hook invocations

### Takeaway
ctx-guard already keeps per-session state files under `/tmp/ctx-guard-<uid>/state` (mode 700), keyed by a validated session id: `<session>.fired` and `<session>.tool-bytes`. A `<session>.taint` file written by the PostToolUse scanner and read by `pre_bash_rewrite.py` would turn later risky actions from "no decision" into `ask`. This implements Meta's "Rule of Two": once untrusted input is in context, actions that change state or communicate externally need a human. parry-guard ships the same design.

### Cited Findings
- Existing state usage:
  - `STATE_DIR` defaults to `/tmp/ctx-guard-<uid>/state` and is created with mode 0o700 and re-chmodded.
  - `safe_session_id` accepts only `^[A-Za-z0-9_-]+$` and otherwise returns "unknown".
  - `context_monitor.py` skips the per-session counter when the session is "unknown" ("a shared counter would mix sessions").
  - `pending_key` correlates PreToolUse and PostToolUse by `tool_use_id`, or by session plus a command hash.
  
  — [hooks/context_monitor.py](hooks/context_monitor.py); [hooks/lib/ctx_guard_common.py](hooks/lib/ctx_guard_common.py)
- `pre_bash_rewrite.py` already emits `ask` with a rewrite for package warnings ("A pkg warning must surface the human permission prompt, not auto-approve"). It also emits `deny` for env enumeration, and maps `ask` per agent: Codex gets `systemMessage` plus `additionalContext`, and agy gets `decision: ask`. — [hooks/pre_bash_rewrite.py](hooks/pre_bash_rewrite.py); [hooks/lib/ctx_guard_common.py](hooks/lib/ctx_guard_common.py) `_codex_decision`, `_antigravity_decision`
- Meta's Agents Rule of Two (2025-10-31): an agent should combine no more than two of [A] processing untrustworthy inputs, [B] access to sensitive systems or private data, and [C] changing state or communicating externally. If all three are needed, it "should not run autonomously" and needs human-in-the-loop approval or another reliable validation. — [Meta AI blog](https://ai.meta.com/blog/practical-ai-agent-security/)
- parry: "A hit taints the project". PreToolUse enforces the taint, and `parry-guard reset` clears it. — [vaporif/parry](https://github.com/vaporif/parry)
- Copilot autopilot denies `ask`. Codex doesn't support `ask`. agy has `force_ask`. — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration); [Codex hooks](https://learn.chatgpt.com/docs/hooks); [Antigravity hooks](https://antigravity.google/docs/hooks/)

### Inferences
**Suggested design**, within the stdlib-only, fail-safe constraints:
- **Writer.** `context_monitor.py`, the PostToolUse hook, runs outside the Bash sandbox. On a detector hit it atomically writes `STATE_DIR/<session>.taint` (write a temp file, then `os.replace`). The file holds a JSON object with `{ts, tool, source (URL/path, redacted), rule, severity}`. The raw matched text is not stored, because it is attacker-controlled.
- **Writer from `ctx-guard-run`.** It is sandboxed and has no session id in filter mode. Either have it emit a marker line that PostToolUse sees in the Bash output (it already sees the compressed output), or reuse the spool mechanism with the session id passed as an extra `--filter` argument. The first is simpler.
- **Reader.** `pre_bash_rewrite.py` (and optionally a new matcher for `WebFetch|Write|Edit|mcp__*`) reads the taint file if it is younger than a TTL. For commands classified as risky, it returns `ask` with a reason like "ctx-guard: untrusted content from <source> was read this session; confirm this network/write command". Risky means network egress (`curl`, `wget`, `nc`, `ssh`, `scp`, `git push`, package publish), credential or path access (`~/.ssh`, `~/.aws`, `.env`), or destructive operations. Read-only commands stay silent.
- **Per-agent mapping:**
  - Claude: `ask`.
  - Copilot: `ask`. In autopilot that becomes deny, which is acceptable for fail-safe.
  - agy: `ask` or `force_ask`.
  - Codex: no `ask`. Either `deny` with a reason telling the user to rerun after review, or a `systemMessage` warning. Parry chose block for Codex.
- **Clearing taint:**
  - on SessionStart `clear`/`startup`, since a new context window satisfies the Rule of Two's "fresh context"
  - by an explicit `ctx-guard taint clear` CLI
  - by TTL
  
  On `compact` the injected text may survive in the summary, so keep the taint.
- **Fail-safe.** Taint read errors mean "not tainted": pass-through, consistent with ctx-guard's "never a silent block" rule. Make that choice explicit, since parry chose the opposite (fail-closed). Write errors are swallowed.

### Gaps
- Exactly which actions parry blocks while tainted is not documented on its README.
- agy's payload session-id field name was not verified. `safe_session_id` checks `session_id`, `sessionId` and `conversationId`.

## 6. Latency budgets and fail-safe expectations for hooks

### Takeaway
Agent defaults are generous: Claude Code 600 s for command hooks (30 s for UserPromptSubmit), Codex 600 s, Copilot 30 s, agy 30 s. ctx-guard registers 15 s and self-allows at 10 s on agy. Shipping guards run far faster: parry's per-hook timeouts are 1–5 s with ~10 ms warm inference. Failure semantics differ per agent, so a stdlib regex scanner should target single-digit milliseconds and must never crash on Copilot or agy PreToolUse.

### Cited Findings
- Claude Code:
  - Default `timeout` is 600 s for command, http and mcp_tool hooks, lowered to 30 s on UserPromptSubmit.
  - Other exit codes, crashes and missing scripts are a "Non-blocking error … The action goes ahead".
  - On PreToolUse a timeout "doesn't block the tool call" unless `onFailure: "block"` (v2.1.295+) is set.
  - Hooks matching an event run in parallel.
  - A PostToolUse hook after a `Ctrl+Enter` interrupt gets only 10 s.
  
  — [Claude Code hooks reference, Timeouts / Block the action when a hook fails](https://code.claude.com/docs/en/hooks#timeouts)
- Claude Code `async: true` hooks deliver `additionalContext` on the next turn but "can't block or rewrite the result". — [Claude Code hooks reference, async hooks](https://code.claude.com/docs/en/hooks#how-async-hooks-execute)
- Copilot: `timeoutSec` defaults to 30. PreToolUse is fail-closed on crash or non-zero exit and fail-open on timeout. Other events log and skip. — [Copilot hooks configuration](https://docs.github.com/en/copilot/reference/hooks-configuration)
- Codex defaults to 600 s, and `SessionEnd`/`Interrupt` default to 1 s. A PreToolUse hook with unsupported fields is marked failed, and the call continues. — [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- agy defaults to 30 s, and failure behavior is undocumented officially. ctx-guard uses a 15 s timeout with a 10 s self-allow, plus a shell fallback that prints `{"decision":"allow"}` if Python can't start. — [Antigravity hooks](https://antigravity.google/docs/hooks/); [hooks/lib/agent_hooks.py](hooks/lib/agent_hooks.py); [hooks/lib/ctx_guard_common.py](hooks/lib/ctx_guard_common.py) `run_pretooluse_hook`
- ctx-guard's own design rules:
  - every hook wraps everything in try/except
  - a `sys.excepthook` backstop calls `os._exit(0)`
  - "always exits 0 with no output on error … never a silent block"
  - redaction itself fails closed by withholding output
  
  — [docs/how-it-works.md](docs/how-it-works.md); [SECURITY.md](SECURITY.md)
- parry example timeouts: PreToolUse 1000 ms, PostToolUse 5000 ms, UserPromptSubmit 2000 ms. ONNX ~10 ms warm, ~580 ms cold, ~8 ms cache hit. — [vaporif/parry](https://github.com/vaporif/parry)
- Anthropic positions OS-level sandboxing (filesystem plus network isolation) as the primary containment for a prompt-injected Claude, with hooks and permissions as complementary layers. It reported an 84% internal reduction in permission prompts from sandboxing. — [Anthropic engineering: Claude Code sandboxing](https://anthropic.com/engineering/claude-code-sandboxing)

### Inferences
**Fail-safe split for the injection feature:**
- **Detector or scanner errors in PostToolUse:** fail open, with the original output and no taint. Copilot and Claude both continue on PostToolUse errors anyway.
- **Errors while building `updatedToolOutput`:** emit nothing, so the original output is used.
- **Errors inside `ctx-guard-run`'s sanitizer:** keep the existing fail-closed redaction contract. Withholding is already the behavior when redaction fails.
- **Taint read errors in PreToolUse:** treat as untainted, preserving "never a silent block" on Copilot and agy.

**Performance.** Run the scan only on the first N KB, plus a Unicode pass over the full text (`str.translate` is fast), with precompiled regexes. Target under 20 ms of added hook time. Python interpreter startup (~20-40 ms) already dominates, and context_monitor already pays it on every tool call.

**Positioning.** Hook-based detection is best-effort. The defense should be documented as defense in depth beneath the agent's sandbox and permission rules, consistent with ctx-guard's existing caveat that redaction "cannot guarantee detection".

### Gaps
- No agent publishes a recommended latency budget for hooks, only timeouts.
- No measured numbers for ctx-guard's current per-hook overhead were found in the repo docs during this research. docs/benchmark.md covers catch rates, and its timing content was not checked.
- Anthropic's newer "How we contain Claude" post was seen only via a search summary (attack-success figures of about 0.1% single-attempt and 5–6% after 100 adaptive attempts on Gray Swan for Opus 4.7). It was not fetched, so treat it as unverified.
