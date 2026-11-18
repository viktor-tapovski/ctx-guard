# How ctx-guard works

The features in detail. For a shorter tour, see the [README](../README.md).

**1. Compresses bash/git/grep output before it reaches the model** (`hooks/pre_bash_rewrite.py`, `PreToolUse`)

Known token-burners get rewritten to efficient equivalents:

| Agent runs | ctx-guard executes |
|---|---|
| `git status` | `git status --porcelain=v1 -b` |
| `git log` | `git log --oneline -n 20` |
| `git diff` | `git diff --stat` + hint |
| `grep -r foo .` | `grep -r foo . \| head -50` |
| `find . -type f` | `find . -type f \| head -100` |
| `cat big_file` | `head -c 8000` + range hint |
| `docker logs foo` | `docker logs foo \| tail -100` (skipped if `-f`/`--follow`) |
| `kubectl get ...` / `kubectl describe ...` | piped through `head -50` / `head -80` |
| `terraform plan` | `terraform plan -no-color \| tail -100` |
| `npm ls`, `pip list` | piped through `head -50` |
| `npm test`, `pytest`, `cargo test`, compound commands | wrapped in `ctx-guard-run` |

`bin/ctx-guard-run` executes the real command, archives a **redacted** output
log to `/tmp/ctx-guard-<uid>/logs/`, and returns to the model only:
error/warning lines (numbered) + head + tail + the log path. Common bearer
tokens, API keys, passwords, private-key blocks, and known token formats are
redacted before the archive is written (see [what redaction catches](../SECURITY.md#what-redaction-catches)). Exit codes are preserved. Commands
already piped through `head/tail/grep/wc` are left untouched. Archived logs
older than `CTX_GUARD_LOG_RETENTION_DAYS` are pruned automatically on every
run. Every invocation also logs a real, measured before/after byte count —
see [Statistics](statistics.md).

Compressed output ends with `Full log: ctx-guard-recall show <run-id> --grep
<pattern>`. `ctx-guard-recall` (symlinked onto `PATH` by `install.sh`) reads
only those redacted archives under `CTX_GUARD_LOG_DIR` and refuses run ids
outside it; every answer is bounded and ends with a truncation notice:

```bash
ctx-guard-recall list [-n 20]                    # recent runs: time, id, exit code, size, redacted command
ctx-guard-recall show last --grep FAIL           # matching lines, capped at 200
ctx-guard-recall show run-Ab3x9QzK --lines 400:460 --grep error --context 2
ctx-guard-recall search 'ECONNREFUSED' --since 1d # at most 50 matches as run-id:line: text
```

The three subagents are told to use it instead of re-running a command.

**2. Auto-delegates exploration to subagents** (`hooks/auto_delegate.py`, `UserPromptSubmit` + three agents) — **Claude Code only**, see caveat below

Prompts like "investigate…", "why are tests failing", "where is X defined" get a delegation directive injected, routing to:
- `researcher` (sonnet) — codebase/library investigation, returns ≤300-word report with `path:line` references
- `log-triager` (haiku) — runs failing tests/builds in its own context, returns root cause + fix plan
- `code-searcher` (haiku) — unbounded grep in isolation, returns only locations

All three carry `Use PROACTIVELY` descriptions, so Claude Code also auto-delegates on its own. Cheap models handle the disposable reading; your main-thread model stays clean.

**3. Auto-manages the context window** (`hooks/context_monitor.py`, `PostToolUse`)

Estimates usage from the transcript file (bytes/4) and injects escalating guidance once per session at 50% (prefer subagents), 70% (stop new file reads, suggest focused `/compact`), and 85% (summarize state, compact or restart now).

Copilot CLI doesn't give hooks a transcript or the context size, so there the estimate is the session's total tool output (bytes/4) against `CTX_GUARD_COPILOT_WINDOW_TOKENS` (default 128000), and each warning is labelled "estimated from tool output". Tool output is only part of the context, so the estimate runs low; the small default window makes up for some of that. The advice names Copilot's built-in `explore` and `task` agents. Copilot's own `/context` and status line show the real figure.

**4. Measures real token savings** (`bin/ctx-guard-stats`)

See [Statistics](statistics.md).

**5. Flags risky package installs** (`hooks/pkg_install_check.py`, called from `pre_bash_rewrite.py`)

Detects install/run commands across `npm`/`pnpm`/`yarn`/`bun`, `pip`/`pip3`/
`python -m pip`/`uv pip`/`uv add`/`poetry add`/`pipx install`, `cargo install`/
`cargo add`, `gem`, `apt`/`apt-get`, `brew`, `apk`, and `npx`/`bunx`/`pipx run`/
`uvx`. Structural checks (no network) flag `curl|bash`-style remote-script
piping (including `| python3 -`, `bash <(curl …)` and `sh -c "$(curl …)"`), unpinned
versions, confirmation-bypass flags (`--force`, `-y`, `--allow-unauthenticated`),
and git/URL-based installs. For `npm`, `pip`, `cargo`, and `gem` specifically
(the ecosystems with an open, anyone-can-publish registry), a registry lookup
also checks package existence, publish age, and typosquat distance against a
bundled list of popular package names.

A package that does not exist on its registry is **blocked** outright — this
is the common shape of an LLM hallucinating a plausible-looking package name.
Everything else (new packages, near-miss names, unpinned versions, bypass
flags, git/URL sources) is a **warning** that still lets the command run.

Registry lookups use a 1.5s timeout and fail open: if the registry is
unreachable, the registry-based checks are skipped for that install and only
the no-network structural checks apply. Private/internal packages that won't
resolve against the public registry can be exempted via an allowlist file at
`~/.ctx-guard/pkg-allowlist` (global) and/or `.ctx-guard/pkg-allowlist`
(per-repo, glob patterns supported, e.g. `@yourorg/*`).

The agent sees one line naming the decision, the install and the reason, e.g.:

```
ctx-guard blocked pip install reqeusts: no such package on registry (did you mean requests?). Allowlist: add the name to .ctx-guard/pkg-allowlist (see README#tuning)
```

Measured catch rate on a 105-command fixture set (stubbed registry, no
network): 100% of nonexistent names, 92% of typosquats (four-character names
are not distance-checked), 0 of 47 legitimate installs flagged. See
[docs/benchmark.md](benchmark.md#package-install-guard) for the method
and limitations.

Set `CTX_GUARD_PKG_CHECK=0` to disable this feature entirely.

**6. Bounds large Read and Grep calls** (`hooks/pre_tool_bound.py`, `PreToolUse`)

Built-in tool output can't be shrunk after the tool runs, so this hook bounds
the call before it does:

- **Read** with no `limit` on a file with more than `CTX_GUARD_READ_MAX_LINES`
  lines left to read gets `limit` set to that value, plus a note telling the
  model the file's length and the `offset` to continue from. Files named in
  your latest prompt, images, PDFs, notebooks and binary files are left alone.
- **Grep** with `output_mode: "content"` and no `head_limit` gets
  `head_limit: CTX_GUARD_GREP_MAX`.
- **Glob** has no limit field and is not touched.

Under Copilot CLI the same hook bounds the `view` and `grep` tools: a `view`
with no `view_range` gets `view_range: [1, CTX_GUARD_READ_MAX_LINES]`, and the
note gives the next `view_range` to read. Copilot doesn't send the hook a
transcript, so files named in your prompt are bounded too; ask for a specific
range, or set a higher `CTX_GUARD_READ_MAX_LINES`, when you need more.

The hook only changes the input. It never approves the call, so your normal
permission rules still apply. Bounded calls are counted under OBSERVED in
`ctx-guard-stats`, and their savings are reported under ESTIMATED. Set
`CTX_GUARD_BOUND_TOOLS=0` to turn it off.

## How cross-tool compatibility works

Claude Code and Copilot CLI have different native hook payload shapes and output contracts:

| | Claude Code | Copilot CLI (native) |
|---|---|---|
| Input fields | `tool_name`, `tool_input` | `toolName`, `toolArgs` |
| Rewrite output | `hookSpecificOutput.updatedInput` | flat `modifiedArgs` |

Copilot CLI also supports a **PascalCase event-name mode** (`PreToolUse` instead of `preToolUse`) that makes it deliver Claude-shaped payloads (`tool_name`/`tool_input`) and apply Claude's matcher semantics — that's what `install.sh` configures. `hooks/lib/ctx_guard_common.py` additionally normalizes both field-name variants and emits a decision object containing **both** the nested Claude shape and the flat Copilot shape, so the identical script works regardless of which runtime is calling it, with no per-tool branching in the rewrite logic itself.

**Important asymmetry:** Copilot CLI's command `preToolUse` hooks are **fail-closed** — a script crash or non-zero exit denies the tool call outright, even if stdout reports `permissionDecision: "allow"` (Claude Code fails open instead). Every hook in this repo therefore wraps its entire body in a top-level `try/except` and always exits `0`, regardless of runtime.

`auto_delegate.py` is intentionally **not** wired into Copilot CLI: Copilot reads `additionalContext` from a `userPromptSubmitted` hook but never adds it to the conversation (checked in the Copilot CLI 1.0.62 source), so the injected delegation directive would never reach the model. The hook could rewrite the prompt itself (`modifiedPrompt`), but ctx-guard doesn't change what you typed. Copilot CLI already auto-delegates to its own built-in subagents (`explore`, `task`, `research`, etc.), so this isn't a real capability gap — just a different mechanism.

## Design notes

- **Fails open on Claude Code, fails safe everywhere.** Every hook catches all exceptions and always exits 0 with no output on error, which both Claude Code (fails open) and Copilot CLI (fails closed on crashes) treat as "no action, run normally" — never a silent block.
- **Nothing lost.** Full output of wrapped commands is archived (redacted) whenever a log directory is writable; the model is told the run id and instructed to retrieve slices with `ctx-guard-recall` instead of re-running.
- **Compound commands** (`a && b; c`) are never pattern-rewritten (too risky) — they go through the generic wrapper: `(set -o pipefail; { <cmd>\n} 2>&1 | ctx-guard-run --filter …)`. The original command stays verbatim, so the agent's permission check sees and matches the real commands, and `ctx-guard-run` only ever filters output. Rewrites carry no permission decision, so they never skip your approval rules; Codex CLI is the exception, since it applies a rewrite only with `allow`, so there only read-only rewrites are sent ([compatibility](compatibility.md)). In Copilot CLI 1.0.105 (`copilot -p`) wrapped commands needed only the allows the original command needs; no extra allow for `ctx-guard-run`. If an interactive session ever prompts for it, allow it once (`--allow-tool 'shell(ctx-guard-run)'`).
- **Per-user runtime directory.** Logs/scripts/state live under `/tmp/ctx-guard-<uid>/`, created mode `700`, rather than a shared `/tmp/ctx-guard/` — a single predictable, world-readable path would let other local users on a multi-user host read archived command output (potentially including secrets) or race to control the directory before ctx-guard's first run. `install.sh` deletes the old shared path if present.
- **Credential-aware output handling.** Environment enumeration (`env`, `printenv`, `set`, and `export`) is denied. Common credential patterns in the output of wrapped commands are redacted before logs are retained or returned to the model; output from rewritten-only commands and the agent's built-in file tools is not filtered. Pattern-based redaction cannot identify every secret, so do not treat ctx-guard as a guarantee that arbitrary sensitive data is safe to disclose.
- Requires a Claude Code version supporting `updatedInput` in `PreToolUse` hooks. On older versions the rewriter is silently inert, but the CLAUDE.md rules and subagents still provide most of the benefit.
- **Manifest-owned uninstall.** Run `ctx-guard-uninstall` (symlinked onto `PATH` by `install.sh`, or `~/.ctx-guard/bin/ctx-guard-uninstall` by full path). It removes only hook commands, files, symlinks, and rules recorded by the install manifest; modified or unrelated user files are preserved. A full uninstall deletes `~/.ctx-guard` as a whole, including a `pkg-allowlist` you put there.
