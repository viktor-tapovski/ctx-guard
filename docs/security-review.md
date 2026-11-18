# Security review

This page is for the person who approves tools before they are installed. It
lists what ctx-guard runs, what it writes and where, what it sends over the
network, and how to remove it. It should take about 10 minutes to read. Every
claim points at the file that implements it, so you can check it yourself.

For vulnerability reports and the full redaction pattern list, see
[SECURITY.md](../SECURITY.md).

## In one paragraph

ctx-guard is a set of hook scripts for AI coding agents (Claude Code, GitHub
Copilot CLI, Antigravity CLI, Codex CLI). The agent calls the hooks before and
after its own tool calls. The hooks may rewrite a shell command into a shorter
form, route its output through a summarizer that saves a redacted copy on
disk, block a few risky commands, or add a warning to the agent's context.
It is bash and Python standard library only. There is no binary, no daemon,
no account, no telemetry, and one optional network call: a package-name lookup
on the public registry.

## What ships

| Claim | How it is enforced |
|---|---|
| No compiled binaries | `tests/check_stdlib_only.py` fails CI if any tracked file contains a NUL byte or any mode-755 file has no shebang |
| No third-party Python packages | The same check parses every Python file in `bin/` and `hooks/`, and every Python heredoc in tracked shell scripts, and fails on any import outside the standard library and `hooks/` |
| Python 3.9 or newer, bash | `install.sh` picks the first `python3`, then `python3.14` down to `python3.9`, that reports 3.9 or newer, or uses `CTX_GUARD_PYTHON` |
| Release contents match the repo | The release tarball is built with `git archive` from the tagged commit; tests and CI files are excluded through `.gitattributes` |

The whole runtime is 23 files in `bin/`, `hooks/` and `hooks/lib/`, four of
them plain lists of popular package names. It can be read in one sitting.

## How it gets installed

There are three ways in. All of them end in the same `install.sh`.

- **Homebrew** (`brew install ctx-guard/tap/ctx-guard`): installs the release
  into Homebrew's `libexec` with `python@3.13`. It changes no agent
  configuration until the user runs `ctx-guard install`.
- **curl** (`get.sh`): downloads the release tarball from GitHub Releases over
  HTTPS only (`curl --proto '=https'`), checks its SHA-256 against the value
  baked into `get.sh`, and runs nothing on a mismatch. The trust root is
  `get.sh` itself, served from the `ctx-guard/install` repository.
- **From a clone**: `bash install.sh`.

Releases are cut by release-please. A release workflow publishes `get.sh` and
the Homebrew formula to `ctx-guard/install` and `ctx-guard/homebrew-tap`
using the `INSTALLERS_TOKEN` repository secret
(`.github/workflows/release.yml`). Without it, that step is skipped.

The installer asks which agents to set up. With no terminal, or with `--yes`,
it sets up every supported agent it finds.

## What it writes, and where

| Path | What | Mode |
|---|---|---|
| `~/.ctx-guard/` | The hook scripts, tools and install manifest | dir follows umask; manifest `600` |
| `~/.ctx-guard/install-manifest.json` | Everything the installer changed, with SHA-256 hashes and original contents, so uninstall can undo exactly that | `600` |
| `~/.local/bin/ctx-guard-{recall,stats,statusline,try,uninstall}` | Symlinks into `~/.ctx-guard/bin` | symlinks |
| `/tmp/ctx-guard-<uid>/logs/` | Redacted output of wrapped commands, plus a `.meta` file each | dir `700`, files `600` |
| `/tmp/ctx-guard-<uid>/state/` | Per-session "already warned" markers and byte counters | dir `700` |
| `~/.local/state/ctx-guard/stats.jsonl` | Usage stats: timestamps, byte counts, rule names, agent name. No command text or output | dir `700`, file `600` |

Agent configuration it edits:

| Agent | File | Change |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | Adds four hook entries; a copy of the previous file is saved as `settings.json.bak` |
| Claude Code | `~/.claude/agents/{researcher,log-triager,code-searcher}.md` | Adds three subagents; refuses to overwrite a file the user has changed |
| Claude Code | `~/.claude/CLAUDE.md` | Appends a marked block of four working rules |
| Copilot CLI | `~/.copilot/hooks/ctx-guard.json` | A new file holding three hooks |
| Antigravity CLI | `~/.gemini/config/hooks.json` | Adds one entry named `ctx-guard`, written atomically |
| Codex CLI | `$CODEX_HOME/hooks.json` (default `~/.codex`) | Adds one hook, written atomically. Codex runs it only after the user trusts it in `/hooks` |

With `--statusline` (opt-in) it also sets the `statusLine` command in the
Claude Code and Copilot CLI settings. If one was already set, ctx-guard keeps
calling it and appends its own segment.

The runtime directories fall back to `$TMPDIR/ctx-guard-<uid>/` when `/tmp`
is not writable, as happens inside agent sandboxes. All locations can be moved
with environment variables (see the README's Tuning section).

## What runs, and what it can do

| Agent | Event | Script | Can change the call | Can block | Adds context |
|---|---|---|---|---|---|
| All four | Before a shell command | `pre_bash_rewrite.py` | Yes | Yes | Yes |
| Claude Code, Copilot CLI | Before Read / Grep (`view` / `grep` in Copilot) | `pre_tool_bound.py` | Adds a line or result limit only | No | No |
| Claude Code, Copilot CLI | After every tool call | `context_monitor.py` | No | No | Yes |
| Claude Code | When the user sends a prompt | `auto_delegate.py` | No | No | Yes |

`pre_bash_rewrite.py` does four things:

1. **Rewrites a few read-only commands** to leaner forms, such as
   `git status` to `git status --porcelain=v1 -b`. The table is `REWRITES` in
   the same file.
2. **Wraps test runners, builds and compound commands** as
   `(set -o pipefail; { <command> } 2>&1 | ctx-guard-run --filter …)`. The
   command itself runs unchanged; only its output is summarized. Commands the
   user already piped through `head`, `tail` or `grep` are left alone.
3. **Checks package installs** (`npm`, `pip`, `cargo`, `gem`). A package that
   does not exist on the registry is blocked. A lookalike name, a package
   published less than 7 days ago, `curl | bash` or an unpinned version gets a
   warning and is left for the user to decide.
4. **Blocks `env`, `printenv`, `set` and `export`** when they start the
   command, because they print credentials.

To measure what a rewrite saved, the hook runs the original read-only `git`
command once itself, with hardened flags and only when the repository's git
config cannot run external programs (`git_config_is_inert`).

### Permission decisions

ctx-guard does not approve commands on the user's behalf.

| Situation | Claude Code, Copilot CLI | Antigravity CLI | Codex CLI |
|---|---|---|---|
| Rewritten or wrapped command | No decision: the agent's own permission rules apply to the new command | `allow`, which Antigravity treats as "no objection" and still applies the user's policy | Read-only rewrites only, with `allow` (Codex requires it to apply a rewrite); wraps are not sent |
| Package warning | `ask` | `ask` | A warning message; Codex has no `ask` |
| Missing package, env dump | `deny` | `deny` | `deny` |

## Network

The package check is the only code that opens a network connection
(`registry_lookup` in `hooks/pkg_install_check.py`).

- **Hosts:** `registry.npmjs.org`, `pypi.org`, `crates.io`, `rubygems.org`.
- **Sent:** an HTTPS GET for the package name, with
  `User-Agent: ctx-guard-pkg-check`. No command text, output, file contents,
  paths or identifiers.
- **When:** only for an install command, once per package named in it.
- **Timeout:** 1.5 seconds per package. Any error other than a 404 skips the
  check and lets the command run.
- **Proxy:** Python's standard `http_proxy` / `https_proxy` handling.
- **Off switch:** `CTX_GUARD_PKG_CHECK=0`, or list names in
  `~/.ctx-guard/pkg-allowlist` to skip them.

Nothing else talks to the network: no telemetry, no update check, no error
reporting. The grep under [Check it yourself](#check-it-yourself) finds
only `hooks/pkg_install_check.py`.

## Command output on disk

- **What is saved:** the output of wrapped commands only (test runners,
  builds, compound commands). The command runs once; its full output is
  redacted, saved, and summarized for the agent.
- **Redaction:** before the output is saved or shown to the agent,
  `ctx-guard-run` masks private keys, auth headers, credentials in URLs,
  `password=`/`token=`-style assignments, and known token formats (GitHub,
  Slack, AWS key IDs, `sk-` keys, npm, JWTs). The full list and its known gaps
  are in [SECURITY.md](../SECURITY.md#what-redaction-catches).
- **Fail closed:** if redaction fails, the saved log and the agent both get
  `ctx-guard: output withheld because redaction failed`, never the raw output.
- **Retention:** logs older than 7 days are deleted on every run
  (`CTX_GUARD_LOG_RETENTION_DAYS`).
- **Reading it back:** `ctx-guard-recall` reads only regular, non-symlink
  files owned by the current user inside the log directory.
- **Off switch for redaction:** `CTX_GUARD_REDACT_LOGS=0` saves and returns
  raw output. It is off by default.

## When something goes wrong

ctx-guard is built so that a broken hook does not break the agent.

- Every hook catches its own errors and exits with no output, which every
  agent treats as "carry on".
- **Antigravity CLI** blocks a call when a hook prints nothing, crashes or
  times out. ctx-guard's hook command prints `allow` if Python cannot start,
  and the hook answers `allow` itself after 10 seconds, inside Antigravity's
  15-second limit.
- **Package lookups** fail open on any network error.
- **`ctx-guard-run`** with nowhere writable to save a log runs the command and
  returns its output without summarizing or saving it, the same as without
  ctx-guard.

## Removal

```bash
ctx-guard-uninstall                      # everything
ctx-guard-uninstall --agents codex-cli   # one agent
```

Uninstall works only from the manifest written at install time.

- It removes the exact hook entries it added and leaves the user's other
  hooks alone.
- A file ctx-guard created is deleted only if its hash is unchanged. A file it
  edited (Antigravity, Codex) is restored byte for byte if unchanged since
  install; otherwise only the ctx-guard entries are taken out.
- Subagent files and the `CLAUDE.md` block are restored or removed the same
  way. Status line settings are restored to what they were.
- Symlinks in `~/.local/bin` are removed only if they still point into
  `~/.ctx-guard`.
- A full uninstall deletes `~/.ctx-guard` and `/tmp/ctx-guard-<uid>`.
- It keeps the stats file and `~/.claude/settings.json.bak`. Delete
  `~/.local/state/ctx-guard/` to remove the stats.

## Known limitations

These are the things a reviewer is likely to ask about. They are listed here
rather than discovered later.

- **Redaction covers wrapped commands only.** Output from a command
  ctx-guard rewrites but does not wrap (such as `git status`), and from the
  agent's built-in file tools, reaches the agent exactly as it would without
  ctx-guard. ctx-guard does not make that output any less private; it just
  does not filter it.
- **Redaction is pattern matching.** A bare secret with no recognizable
  prefix or key name is not caught. See the known limitations in
  [SECURITY.md](../SECURITY.md#what-redaction-catches).
- **The env-command block is a guard rail, not a boundary.** It matches only
  when `env`, `printenv`, `set` or `export` starts the command. `cd x && env`
  is wrapped instead, and its output then goes through redaction.
- **Output reaches the agent's provider.** Anything the agent sees, summarized
  or not, may be sent to the model provider under that provider's terms.
- **A full uninstall deletes `~/.ctx-guard/pkg-allowlist`** along with the
  rest of `~/.ctx-guard`. Keep a copy if you maintain one there, or use the
  per-repo `.ctx-guard/pkg-allowlist`.
- **GitHub Actions are pinned by major version tag** (`@v4`), not by commit
  SHA.
- **`~/.ctx-guard` and the edited agent config files take the user's umask**;
  only the manifest, logs, state and stats are set to owner-only explicitly.

## Check it yourself

```bash
git clone https://github.com/viktor-tapovski/ctx-guard.git && cd ctx-guard
python3 tests/check_stdlib_only.py          # stdlib only, no binaries
grep -rnE "urllib|socket|http\.client" bin hooks --exclude-dir=popular_packages  # network code
bash tests/smoke.sh                         # installs and uninstalls in temp HOMEs
python3 -m unittest discover -s tests       # unit tests, including redaction
bin/ctx-guard-try                           # see the hooks fire, writes only to a temp dir
```

After installing, `cat ~/.ctx-guard/install-manifest.json` shows every file
ctx-guard changed.

If this page does not answer a question your review needs, open an issue or
use GitHub's private vulnerability reporting, and the answer will be added
here.
