# ctx-guard

A self-hosted context-window and token optimizer for **Claude Code** and **GitHub Copilot CLI**. No third-party dependencies — pure bash + Python stdlib.

The same hook scripts run unmodified under both tools; only the per-tool wiring differs (`~/.claude/settings.json` vs `~/.copilot/hooks/ctx-guard.json`). See [How cross-tool compatibility works](#how-cross-tool-compatibility-works) below.

> **Status:** ctx-guard is in an early public soft launch. Claude Code and
> GitHub Copilot CLI are the tested integrations; other terminal agents need
> an adapter and are not supported yet.

## Why ctx-guard

ctx-guard is designed to make agent tool output smaller without hiding useful
diagnostics. It keeps a redacted output archive locally, returns a bounded
summary to the model, and reports measured byte reductions separately from
rewrite and output-volume observations.

This project does not promise a universal savings percentage. Results depend
on the commands, repositories, agent, and output being processed. See the
[benchmark methodology](docs/benchmark.md) for reproducible measurements.

## Quick links

- [Compatibility](docs/compatibility.md)
- [Benchmark methodology](docs/benchmark.md)
- [Public launch plan](docs/launch.md)
- [Security and privacy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## What it does

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
redacted before the archive is written. Exit codes are preserved. Commands
already piped through `head/tail/grep/wc` are left untouched. Archived logs
older than `CTX_GUARD_LOG_RETENTION_DAYS` are pruned automatically on every
run. Every invocation also logs a real, measured before/after byte count —
see [Statistics](#statistics).

**2. Auto-delegates exploration to subagents** (`hooks/auto_delegate.py`, `UserPromptSubmit` + three agents) — **Claude Code only**, see caveat below

Prompts like "investigate…", "why are tests failing", "where is X defined" get a delegation directive injected, routing to:
- `researcher` (sonnet) — codebase/library investigation, returns ≤300-word report with `path:line` references
- `log-triager` (haiku) — runs failing tests/builds in its own context, returns root cause + fix plan
- `code-searcher` (haiku) — unbounded grep in isolation, returns only locations

All three carry `Use PROACTIVELY` descriptions, so Claude Code also auto-delegates on its own. Cheap models handle the disposable reading; your main-thread model stays clean.

**3. Auto-manages the context window** (`hooks/context_monitor.py`, `PostToolUse`) — **Claude Code only**, see caveat below

Estimates usage from the transcript file (bytes/4) and injects escalating guidance once per session at 50% (prefer subagents), 70% (stop new file reads, suggest focused `/compact`), and 85% (summarize state, compact or restart now). Under Copilot CLI this hook still runs (for stats logging, see below) but skips the window-threshold logic, since Copilot CLI doesn't expose a transcript path to `postToolUse` hooks and already surfaces context usage natively via `/context` and the status line.

**4. Measures real token savings** (`bin/ctx-guard-stats`)

See [Statistics](#statistics).

**5. Flags risky package installs** (`hooks/pkg_install_check.py`, called from `pre_bash_rewrite.py`)

Detects install/run commands across `npm`/`pnpm`/`yarn`, `pip`/`pip3`, `cargo`,
`gem`, `apt`/`apt-get`, `brew`, `apk`, and `npx`/`pipx run`/`uvx`. Structural
checks (no network) flag `curl|bash`-style remote-script piping, unpinned
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

Set `CTX_GUARD_PKG_CHECK=0` to disable this feature entirely.

## Install

```bash
bash install.sh
```

This installs shared files to `~/.ctx-guard/` (used by both tools) and then:
- **Claude Code**: merges hooks into `~/.claude/settings.json` (backup saved as `.bak`), installs the three subagents to `~/.claude/agents/`, and appends guard rules to `~/.claude/CLAUDE.md`.
- **Copilot CLI**: writes `~/.copilot/hooks/ctx-guard.json` wiring the rewrite and stats hooks.

Idempotent — safe to re-run. Restart each tool: verify Claude Code with `/hooks` and `/agents`; verify Copilot CLI with `/env` (look for `ctx-guard.json` under hooks).

## Statistics

Check whether ctx-guard is actually saving tokens — don't take it on faith:

`install.sh` symlinks it onto your `PATH` via `~/.local/bin`, so just run:

```bash
ctx-guard-stats            # one-line savings gauge
ctx-guard-stats --verbose  # full MEASURED/OBSERVED report
ctx-guard-stats --json     # machine-readable
ctx-guard-stats --since 7d # last 7 days only
ctx-guard-stats --reset    # clear the log
ctx-guard-stats --version  # which ctx-guard is installed
```

(If `~/.local/bin` isn't on your `PATH`, use the full path `~/.ctx-guard/bin/ctx-guard-stats` instead, or add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc file.)

The default is a single line — the headline savings and nothing else:

```
ctx-guard  |███████████████████▉    | 83.0%  ·  319.5KB saved  ·  ~81.8K tokens
```

Bars are decoration only: piped or redirected output drops the bar and stays
plain ASCII, and `CTX_GUARD_BARS=always|never` overrides the TTY auto-detection
either way. On a terminal that cannot encode the block glyphs (`LC_ALL=C` and
similar) the bars fall back to `#`/`-` rather than failing.

`--verbose` prints the full report, where the compression ratio and the
per-rule / per-tool breakdowns each get their own bar. It is split into two
honestly-labeled sections:

- **MEASURED** — real before/after byte counts from `ctx-guard-run`. The full command output and what was actually returned are both known from the same execution, so this is a hard number (bytes and an estimated token count using the same bytes/4 heuristic ctx-guard uses elsewhere), not a guess.
- **OBSERVED** — counts of command rewrites applied (e.g. `git status` → `--porcelain`) and total tool-output bytes delivered per tool. These are **not** converted into a "tokens saved" figure, because the unbounded/original version of those commands is never actually run — there's no ground truth to diff against. Fabricating a number there would defeat the point of asking for proof.

All stats are per-agent (`claude-code` / `copilot-cli`), recorded to `$CTX_GUARD_STATE_DIR/stats.jsonl` (default `/tmp/ctx-guard-<uid>/state/stats.jsonl`), one JSON object per line, mode `600`.

## How cross-tool compatibility works

Claude Code and Copilot CLI have different native hook payload shapes and output contracts:

| | Claude Code | Copilot CLI (native) |
|---|---|---|
| Input fields | `tool_name`, `tool_input` | `toolName`, `toolArgs` |
| Rewrite output | `hookSpecificOutput.updatedInput` | flat `modifiedArgs` |

Copilot CLI also supports a **PascalCase event-name mode** (`PreToolUse` instead of `preToolUse`) that makes it deliver Claude-shaped payloads (`tool_name`/`tool_input`) and apply Claude's matcher semantics — that's what `install.sh` configures. `hooks/lib/ctx_guard_common.py` additionally normalizes both field-name variants and emits a decision object containing **both** the nested Claude shape and the flat Copilot shape, so the identical script works regardless of which runtime is calling it, with no per-tool branching in the rewrite logic itself.

**Important asymmetry:** Copilot CLI's command `preToolUse` hooks are **fail-closed** — a script crash or non-zero exit denies the tool call outright, even if stdout reports `permissionDecision: "allow"` (Claude Code fails open instead). Every hook in this repo therefore wraps its entire body in a top-level `try/except` and always exits `0`, regardless of runtime.

`auto_delegate.py` is intentionally **not** wired into Copilot CLI: Copilot drops all command-hook output (including `additionalContext`) for `userPromptSubmitted`, so the injected delegation directive would never reach the model. Copilot CLI already auto-delegates to its own built-in subagents (`explore`, `task`, `research`, etc.), so this isn't a real capability gap — just a different mechanism.

## Tuning

| Env var | Default | Meaning |
|---|---|---|
| `CTX_GUARD_MAX_LINES` | 150 | Max lines returned per wrapped command |
| `CTX_GUARD_WINDOW` | 200000 | Assumed context window for % thresholds (Claude Code only; set 1000000 for 1M-window models) |
| `CTX_GUARD_LOG_DIR` | /tmp/ctx-guard-\<uid\>/logs | Full-output archive |
| `CTX_GUARD_SCRIPT_DIR` | /tmp/ctx-guard-\<uid\>/scripts | Temp scripts used to wrap generic commands |
| `CTX_GUARD_STATE_DIR` | /tmp/ctx-guard-\<uid\>/state | Per-session "already warned" markers + `stats.jsonl` |
| `CTX_GUARD_STATS_FILE` | `$CTX_GUARD_STATE_DIR/stats.jsonl` | Override the stats log location |
| `CTX_GUARD_LOG_RETENTION_DAYS` | 7 | Archived logs older than this are deleted on every run |
| `CTX_GUARD_REDACT_LOGS` | 1 | Redact common credential patterns before logs reach the model or remain on disk; set to `0` only for an explicitly trusted local workflow |
| `CTX_GUARD_BARS` | auto | `ctx-guard-stats` bars: `auto` draws them only when stdout is a TTY, `always`/`never` force them on/off |
| `CTX_GUARD_AGENT` | `unknown` | Tag written into stats events; set to `claude-code`/`copilot-cli` by the installed hook wiring |

Edit `REWRITES` / `RUNNERS` in `pre_bash_rewrite.py` to add your own commands; edit `PATTERNS` in `auto_delegate.py` for your routing keywords.

## Design notes

- **Fails open on Claude Code, fails safe everywhere.** Every hook catches all exceptions and always exits 0 with no output on error, which both Claude Code (fails open) and Copilot CLI (fails closed on crashes) treat as "no action, run normally" — never a silent block.
- **Nothing lost.** Full output always archived; the model is told the path and instructed to grep it instead of re-running.
- **Compound commands** (`a && b; c`) are never pattern-rewritten (too risky) — they go through the generic wrapper via a temp script, avoiding all shell-quoting pitfalls.
- **Per-user runtime directory.** Logs/scripts/state live under `/tmp/ctx-guard-<uid>/`, created mode `700`, rather than a shared `/tmp/ctx-guard/` — a single predictable, world-readable path would let other local users on a multi-user host read archived command output (potentially including secrets) or race to control the directory before ctx-guard's first run. `install.sh` deletes the old shared path if present.
- **Credential-aware output handling.** Environment enumeration (`env`, `printenv`, `set`, and `export`) is denied. Common credential patterns are redacted before logs are retained or returned to the model. Pattern-based redaction cannot identify every secret, so do not treat ctx-guard as a guarantee that arbitrary sensitive data is safe to disclose.
- Requires a Claude Code version supporting `updatedInput` in `PreToolUse` hooks. On older versions the rewriter is silently inert, but the CLAUDE.md rules and subagents still provide most of the benefit.
- **Manifest-owned uninstall.** Run `ctx-guard-uninstall` (symlinked onto `PATH` by `install.sh`, or `~/.ctx-guard/bin/ctx-guard-uninstall` by full path). It removes only hook commands, files, symlinks, and rules recorded by the install manifest; modified or unrelated user files are preserved.

## License

ctx-guard is released under the [MIT License](LICENSE).
