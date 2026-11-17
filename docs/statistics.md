# Statistics

Check whether ctx-guard is actually saving tokens — don't take it on faith:

`install.sh` symlinks `ctx-guard-stats` onto your `PATH` via `~/.local/bin`, so just run:

```bash
ctx-guard-stats            # one-line savings gauge
ctx-guard-stats --verbose  # full MEASURED/OBSERVED report
ctx-guard-stats --json     # machine-readable
ctx-guard-stats --gains    # measured savings per command (add -n N for top N rows)
ctx-guard-stats --since 7d # last 7 days only
ctx-guard-stats --breakdown [daily|weekly|monthly]  # per-period table (bare = all)
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

Once the package guard has checked an install, the gauge also carries
`pkg-guard: 8 checked, 2 blocked, 4 warned`, and `--verbose` adds a
**PACKAGE GUARD** section:

```
== PACKAGE GUARD (install commands checked before they ran) ==
  installs checked: 8  (allowed 2, warned 4, blocked 2)
  top reasons (warned/blocked installs):
    unpinned             4
    nonexistent          2
    typosquat            1
    pipe_to_shell        1
```

`--json` carries the same numbers as a `pkg_guard` object (`checked`,
`allowed`, `warned`, `blocked`, `top_reasons`, `by_ecosystem`) next to the
existing `pkg_blocked_count` and `pkg_warned_count`. Each check is one
`pkg_guard` event holding the ecosystem, decision, reason codes
(`nonexistent`, `typosquat`, `new_package`, `url_install`, `pipe_to_shell`,
`bypass_flag`, `unpinned`) and the package name, with credentials stripped
from URL specs. The command line itself is never logged. `pkg_blocked` and
`pkg_warned` events written by v0.6.0 and earlier still count.

`--verbose` prints the full report, where the compression ratio and the
per-rule / per-tool breakdowns each get their own bar. It is split into two
honestly-labeled sections:

- **MEASURED** — real before/after byte counts from `ctx-guard-run`. The full command output and what was actually returned are both known from the same execution, so this is a hard number (bytes and an estimated token count using the same bytes/4 heuristic ctx-guard uses elsewhere), not a guess.
- **OBSERVED** — counts of command rewrites applied (e.g. `git status` → `--porcelain`), counts of bounded Read/Grep calls, and total tool-output bytes delivered per tool. These are **not** converted into a "tokens saved" figure, because the unbounded/original version of those commands is never actually run — there's no ground truth to diff against. Fabricating a number there would defeat the point of asking for proof. The exception is read-only git rewrites (`git status`, `git log`, `git diff`, `git show`, `git branch -a`): the hook runs both forms (2s timeout) and records the real before/after bytes as MEASURED. Because the hook runs outside any agent sandbox while `.git/config` is writable from inside it, measurement only happens when every repo-local git config key is on an inert allowlist (no filters, includes, gpg, aliases, ...); otherwise the rewrite falls back to an ESTIMATE.
- **ESTIMATED** — rewrites that couldn't be measured (`grep`/`find`/`cat`/`docker`/`kubectl`/...), and bounded Read/Grep calls (`read-bound`, `grep-bound`). A bounded Read's ratio comes from the file's line count against the lines the read would otherwise return (2,000 for Claude Code's Read, the whole file for Copilot's `view`). The returned bytes are known; the unbounded size is assumed from a per-rule reduction ratio (`ESTIMATE_RATIOS` in `hooks/lib/ctx_guard_common.py`, overridable with a JSON object in `$CTX_GUARD_ESTIMATE_RATIOS`). Always shown separately and labeled, never added to MEASURED. The estimate pairs PreToolUse with PostToolUse via `tool_use_id`; Copilot CLI sends none, so there it pairs them by session and the command, file path or pattern.

`--gains` replaces the default report with a measured-only view: a header
(commands, input/output tokens, tokens saved, exec time, efficiency meter) and a
**By Command** table sorted by tokens saved, with per-command count, saved
tokens, average reduction (green >= 90%, yellow 50-90%, red < 50% on a
terminal), total time and an impact bar. It composes with `--since` and
`--json`, takes `-n/--top N` (default 10) for the table length, and cannot be
combined with `--verbose` or `--breakdown`. Measured git rewrites appear as rows
(e.g. `git`); unmeasured rewrites and blocks are a footer count, followed by the
ESTIMATED rewrite total on its own line. With no measured commands in range it
prints `No measured commands yet` and exits 0. `--gains --json` emits
`{"gains": {"total_commands", "input_tokens", "output_tokens", "tokens_saved",
"saved_pct", "exec_ms_total", "exec_ms_avg", "by_command": [...], "rewrites",
"blocked", "estimated"}}`, where each `by_command` entry has `command`, `count`,
`saved_tokens`, `avg_saved_pct` and `avg_ms`.

Each `ctx_guard_run` event records `command` (the program name only, e.g.
`git`; leading `VAR=value`, `sudo`/`env`/`time`/`command`/`nice` and `cd <dir> &&`
are skipped, and `(unknown)` is logged if it cannot be parsed) and
`duration_ms`. Arguments and paths are never logged. Events written by older
versions have neither field and group under `(unknown)` with zero time.

## Where stats are stored

All stats are per-agent (`claude-code` / `copilot-cli`), recorded to `${XDG_STATE_HOME:-$HOME/.local/state}/ctx-guard/stats.jsonl` (persistent across reboots; dir mode `700`), one JSON object per line, mode `600`. If that dir is not writable, writers fall back to `/tmp/ctx-guard-<uid>/state/stats.jsonl`. `ctx-guard-uninstall` leaves this file in place.
