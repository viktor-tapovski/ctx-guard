# ctx-guard

**Keep your AI coding agent's context window clean.**

Coding agents burn through their context on noise: a 3,000-line test log, a
full `git diff`, a `grep` that matched half the repo. Once the window fills
up, the agent forgets what it was doing, and you pay for tokens it never
needed.

ctx-guard sits between your agent and the shell. It trims bulky output before
the model sees it, keeps the full output on your machine in case the agent
needs it later, and shows you exactly how much it saved. It also stops your
agent from installing packages that don't exist.

It works with **Claude Code**, **GitHub Copilot CLI**, **Antigravity CLI** and
**Codex CLI**. It's plain bash and Python, with no dependencies, no account
and no network calls except optional package-registry checks.

> **Status:** early public release. Claude Code and Copilot CLI are tested
> end to end; Antigravity CLI and Codex CLI support is newer. See
> [compatibility](docs/compatibility.md) for what works where.

## Install

```bash
# Homebrew (macOS, Linux)
brew install ctx-guard/tap/ctx-guard
ctx-guard install

# or curl (checks the release's SHA-256 before running anything)
curl -fsSL https://raw.githubusercontent.com/ctx-guard/install/main/get.sh | bash
```

The installer lists the agents it supports, with the ones on your machine
already ticked. Arrow keys move, space selects, enter continues. Then restart
your agent, and that's it: there's nothing to learn or change in how you work.

Want to read the installer first? Download
[`get.sh`](https://github.com/ctx-guard/install/blob/main/get.sh), look it
over, and run `bash get.sh`. More install options are [below](#more-install-options).

## Try it

See ctx-guard catch a misspelled package and trim a 3,000-line test log, in
a few seconds and without waiting for your agent to make a mistake:

```bash
ctx-guard try     # after curl: ctx-guard-try    from a clone: bin/ctx-guard-try
```

```
1. Your agent tries to install a misspelled package
   $ pip install reqeusts
   ctx-guard blocked pip install reqeusts: no such package on registry (did you mean requests?).
   Nothing was installed. …

2. Your agent runs a test suite with 3,000 lines of output
   The agent gets 81 lines instead. …

3. Your agent pulls just the lines it needs from the full log
   $ ctx-guard-recall show run-FXKIIwa6 --grep token
   2999: using token [REDACTED TOKEN]
```

It runs the real hooks on made-up input, installs nothing, and leaves your
stats alone. With Homebrew it works even before `ctx-guard install`.

## What you'll notice

**Long output gets short.** When your agent runs your test suite, it gets the
errors, the first and last lines, and a pointer to the full log, not 3,000
lines of passing tests:

```
=== ctx-guard: output compressed (    3001 lines -> summary, exit=1) ===
--- errors/warnings (line-numbered) ---
3001:FAIL src/auth.test.ts:42 expected 200, received 401
--- first 37 lines ---
…
--- ... 2927 lines omitted ... ---
--- last 37 lines ---
…
=== Full log: ctx-guard-recall show run-WgONEU37 --grep <pattern> (do not re-run the command) ===
```

Nothing is thrown away. The agent can pull the exact lines it needs with
`ctx-guard-recall` instead of running the command again.

**Common commands get leaner versions.** `git status` becomes
`git status --porcelain`, `git log` shows the last 20 commits, a recursive
`grep` is capped at 50 lines, and in Claude Code and Copilot CLI huge files
are read in pages.

**Made-up packages get blocked.** Agents sometimes invent package names that
look real. ctx-guard checks npm, PyPI, crates.io and RubyGems first:

```
ctx-guard blocked pip install reqeusts: no such package on registry (did you mean requests?)
```

Lookalike names, brand-new packages, `curl | bash` and unpinned versions get a
warning but still run.

**Your agent gets a heads-up before the window fills.** In Claude Code and
Copilot CLI, at 50%, 70% and 85% context, ctx-guard nudges the agent to hand work to subagents, stop reading
new files, or compact.

**Secrets stay out of the logs.** API keys, tokens, passwords and private keys
are masked before output is saved or shown to the model. It's pattern
matching, so treat it as a safety net, not a guarantee.

## See what it saved

```bash
ctx-guard-stats
```

```
ctx-guard  |███████████████████▉    | 83.0%  ·  319.5KB saved  ·  ~81.8K tokens
```

ctx-guard never makes up a savings number. **Measured** savings come from
commands where it knows both the full output and what it returned. Savings it
can only **estimate** are always labelled as estimates and kept separate.
Your mileage depends on your commands and repos; the
[benchmark methodology](docs/benchmark.md) shows how to measure your own.

Try `--gains` for savings per command, `--breakdown weekly` for trends, or
`--verbose` for the full report. More in [docs/statistics.md](docs/statistics.md).

### In your status line

```bash
ctx-guard install --statusline
```

(With curl, rerun the installer with `| bash -s -- --statusline`; from a
clone, `bash install.sh --statusline`. The same goes for the
`--statusline-preset` below.)

adds a live counter to the Claude Code and Copilot CLI status lines:

```
ctx-guard ⬇ today 12.4k (~3.1k) · total 1.2M (~240k)
```

Prefer something else? Pick a preset (`minimal`, `security`, `full`,
`powerline`) or write your own template, and preview it with your real
numbers first:

```bash
ctx-guard-statusline --preview
ctx-guard install --statusline-preset security    # ⬇ 3.0k · 🛡 5
```

Already have a status line, such as one from statusline.sh? ctx-guard keeps
it and adds its segment after it, or you can drop
`ctx-guard-statusline claude-code --segment` into your own script. See
[docs/statusline.md](docs/statusline.md).

## How it works

ctx-guard uses the hook systems that each agent already has. Before a shell
command runs, a hook may swap it for a leaner version or route it through
`ctx-guard-run`, which runs the real command once, saves a redacted copy of the
full output, and hands back a short summary. Exit codes are kept, and commands
you've already piped through `head`, `tail` or `grep` are left alone.

A few principles:

- **It never breaks your agent.** If a hook hits any error, it steps aside and
  the command runs normally.
- **It never changes what you typed.** Only the commands the agent runs are
  rewritten, and your normal permission prompts still apply.
- **It stays on your machine.** Logs live in a private, per-user folder and are
  deleted after 7 days.
- **It cleans up after itself.** Uninstall removes only what ctx-guard added,
  and leaves alone anything you've changed since.

The full details, per feature and per agent, are in
[docs/how-it-works.md](docs/how-it-works.md).

## More install options

```bash
# From a clone
git clone https://github.com/viktor-tapovski/ctx-guard.git && cd ctx-guard && bash install.sh

# Skip the checklist by naming agents
ctx-guard install --agents claude-code,codex-cli
curl -fsSL .../get.sh | bash -s -- --agents all    # claude-code, copilot-cli, antigravity-cli, codex-cli
```

With no terminal and no `--agents` (CI, scripts), or with `--yes`, it sets up
every agent it finds. Running it again is safe, and leaving an agent out
removes ctx-guard from it.

What it changes:

- **Everything shared** goes in `~/.ctx-guard/`.
- **Claude Code**: hooks in `~/.claude/settings.json` (a `.bak` backup is
  kept), three helper subagents in `~/.claude/agents/`, and a few rules in
  `~/.claude/CLAUDE.md`.
- **Copilot CLI**: `~/.copilot/hooks/ctx-guard.json`.
- **Antigravity CLI** and **Codex CLI**: one hook in
  `~/.gemini/config/hooks.json` and `~/.codex/hooks.json`.

To check it's working after a restart, run `/hooks` in Claude Code,
Antigravity CLI or Codex CLI, or `/env` in Copilot CLI. Codex CLI runs the
hook only after you trust it in `/hooks`.

You need bash and Python 3.9 or newer. After `brew upgrade ctx-guard`, run
`ctx-guard install` again, because the hooks are copied into `~/.ctx-guard`.

**Uninstall:** `ctx-guard-uninstall` removes everything, or
`ctx-guard-uninstall --agents codex-cli` removes it from one agent only. Your
stats file is kept.

## Tuning

Everything works out of the box. If you want to adjust it, set these
environment variables:

| Env var | Default | Meaning |
|---|---|---|
| `CTX_GUARD_MAX_LINES` | 150 | Max lines returned per wrapped command |
| `CTX_GUARD_READ_MAX_LINES` | 400 | Read calls without a `limit` (Copilot: `view` without a `view_range`) are capped at this many lines |
| `CTX_GUARD_GREP_MAX` | 100 | `head_limit` added to Grep content searches that set none |
| `CTX_GUARD_BOUND_TOOLS` | 1 | Set to `0` to stop bounding Read and Grep calls |
| `CTX_GUARD_PKG_CHECK` | 1 | Set to `0` to turn off the package install check |
| `CTX_GUARD_WINDOW` | 200000 | Assumed context window for % thresholds (Claude Code only; set 1000000 for 1M-window models) |
| `CTX_GUARD_COPILOT_WINDOW_TOKENS` | 128000 | Assumed window for the Copilot CLI thresholds, which count tool output only; lower it to warn earlier |
| `CTX_GUARD_LOG_DIR` | /tmp/ctx-guard-\<uid\>/logs | Full-output archive |
| `CTX_GUARD_LOG_RETENTION_DAYS` | 7 | Archived logs older than this are deleted on every run |
| `CTX_GUARD_REDACT_LOGS` | 1 | Redact common credential patterns before logs reach the model or remain on disk; set to `0` only for an explicitly trusted local workflow |
| `CTX_GUARD_STATE_DIR` | /tmp/ctx-guard-\<uid\>/state | Per-session "already warned" markers; if set, `stats.jsonl` lives here too |
| `CTX_GUARD_STATS_FILE` | `${XDG_STATE_HOME:-~/.local/state}/ctx-guard/stats.jsonl` | Override the stats log location (wins over `CTX_GUARD_STATE_DIR`) |
| `CTX_GUARD_BARS` | auto | `ctx-guard-stats` bars: `auto` draws them only when stdout is a TTY, `always`/`never` force them on/off |
| `CTX_GUARD_AGENT` | `unknown` | Tag written into stats events; set to `claude-code`/`copilot-cli` by the installed hook wiring |

If `CTX_GUARD_LOG_DIR` or `CTX_GUARD_STATE_DIR` is not writable -- as happens under an agent sandbox that only permits writes to `$TMPDIR` and the project dir -- `ctx-guard-run` falls back to `$TMPDIR/ctx-guard-<uid>/`. With nowhere writable at all it runs the command uncapped rather than failing it.

Claude Code runs Bash inside a sandbox that can't write the stats file, so measured events from wrapped commands land in that `$TMPDIR` spool. The PostToolUse hook (which runs outside the sandbox) and `ctx-guard-stats` drain spools under `$TMPDIR` and `/tmp/claude*` back into the stats file; set `CTX_GUARD_SPOOL_DIRS` (colon-separated `TMPDIR` roots) to replace that search list. If the stats file can't be written, the spool is left in place and still read.

**Package allowlist.** To stop the package guard flagging a name (a private
package, or a real package it mistakes for a typosquat), add it to
`.ctx-guard/pkg-allowlist` in the repo or `~/.ctx-guard/pkg-allowlist` for all
repos. Use one name or glob pattern per line, such as `@yourorg/*`; lines
starting with `#` are comments. An allowlisted package skips every package
check, including the registry lookup.

Edit `REWRITES` / `RUNNERS` in `pre_bash_rewrite.py` to add your own commands; edit `PATTERNS` in `auto_delegate.py` for your routing keywords.

## Learn more

- [How it works](docs/how-it-works.md): every feature in detail, and how the same
  hooks run under different agents
- [Compatibility](docs/compatibility.md): what works with which agent
- [Statistics](docs/statistics.md) and [status line](docs/statusline.md)
- [Benchmark methodology](docs/benchmark.md), including the package guard's
  measured catch rate
- [Security and privacy](SECURITY.md), [Contributing](CONTRIBUTING.md),
  [Changelog](CHANGELOG.md)

## License

ctx-guard is released under the [MIT License](LICENSE).
