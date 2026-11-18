# Public launch plan

Launch release: **v0.11.0** (v0.10.0 plus the "checked" count in the status
line). Show HN is planned for Tuesday 2026-12-08, about 8:00 US Eastern; the
channel plan is in `founder/go-to-market.md`.

## GitHub

### Repository description

> Hooks that keep AI coding agents' context clean: trim bulky command output, keep the full log locally, and block made-up package installs. Claude Code, Copilot CLI, Antigravity CLI, Codex CLI.

### Topics

`ai-coding`, `developer-tools`, `llm`, `context-window`, `supply-chain-security`,
`claude-code`, `github-copilot`, `codex-cli`, `antigravity`, `shell`, `python`

### Release notes (v0.11.0)

Release-please writes the changelog. Add this above it:

- One paragraph: what ctx-guard does, in the README's words.
- Install: `brew install ctx-guard/tap/ctx-guard && ctx-guard install`, or the
  curl one-liner, which checks the release's SHA-256 before running anything.
- Try it: `ctx-guard try`.
- Links: `README.md`, `docs/compatibility.md`, `docs/benchmark.md`,
  `docs/security-review.md`, `SECURITY.md`.
- Known limits: Antigravity CLI and Codex CLI are tested on fixtures from
  their docs, not yet against the live agents; Codex gets read-only rewrites
  only.

Do not attach real command logs to the release.

## Soft-launch sequence

1. Merge the release PR and check that the Homebrew tap and `get.sh` point at
   the new tarball with a matching SHA-256.
2. Install from Homebrew and from curl on a clean macOS account and a clean
   Ubuntu machine. Run `ctx-guard try`, restart the agent, and check that a
   rewritten command still shows the normal permission prompt.
3. Ask 3–5 users to install on their own machines and report:
   - install success, and which channel they used;
   - agent and version;
   - commands rewritten and package installs flagged (`ctx-guard-stats --verbose`);
   - anything that broke or looked wrong.
4. Send `docs/security-review.md` to users at companies that review tools, and
   ask them to forward it to their reviewer. Record each answer: this is the
   one thing to learn from the first 10 users.
5. Post the Show HN, then Reddit and X (below). Link recurring problems to
   GitHub issues instead of debating them in comments.

## Show HN

### Title

Show HN: ctx-guard, hooks that stop AI agents installing typosquatted packages

### First comment

Hi HN. Coding agents run shell commands on your behalf, and two things kept
going wrong for me: they pull 3,000-line test logs into their context window,
and now and then they try to install a package that doesn't exist or is one
letter off a real one.

ctx-guard is a set of hooks for Claude Code, GitHub Copilot CLI, Antigravity
CLI and Codex CLI. Before a shell command runs, it can:

- check `npm`/`pip`/`cargo`/`gem` installs against the registry and block
  names that don't exist (with a "did you mean");
- warn on lookalike names, brand-new packages, `curl | bash` and unpinned
  versions;
- swap noisy commands for lean versions (`git log` → last 20 commits) and run
  test suites through a wrapper that returns the errors plus the first and
  last lines, keeping a redacted copy of the full log on disk so the agent can
  grep it instead of re-running.

It's bash and the Python standard library only: no dependencies, no account,
and no network calls except the registry lookups, which you can turn off. If a
hook fails, it steps aside and the command runs as normal. Rewritten commands
still go through your agent's own permission prompt.

On the package-guard fixtures (105 install commands across four package
managers), it catches 17/17 non-existent names, 13/13 risky shapes and 23/25
typosquats, with 0/47 false positives on real packages. That last number is
flattering: I tuned the rules on those fixtures, and `docs/benchmark.md`
explains the method and both misses.

For savings, it only reports what it measured, and labels estimates as
estimates. I deliberately don't quote a headline percentage, because it
depends entirely on your commands.

If your company reviews tools before install, `docs/security-review.md` lists
everything it runs, writes and sends. I'd love to hear whether that page would
get it approved where you work.

Install: `brew install ctx-guard/tap/ctx-guard && ctx-guard install`, then
`ctx-guard try` to see it block a misspelled package in a few seconds.

Repo: https://github.com/viktor-tapovski/ctx-guard

### In the comments

- Answer with specifics: file paths, the exact rule, a fixture.
- When someone reports a false positive, ask for the package name and open an
  issue; point them to `~/.ctx-guard/pkg-allowlist` meanwhile.
- Don't argue about token savings; offer `docs/benchmark.md` and
  `ctx-guard-stats` on their own work.

## Reddit

### r/ClaudeCode (launch day)

**Title:** Built a hook that flags hallucinated npm/pip installs before Claude
runs them (open source, stdlib only)

**Body:** the problem in two sentences; the `ctx-guard try` output as a
screenshot; how it works (PreToolUse hook, registry check, permission prompt
unchanged); limits (pattern-based redaction, short-name typosquats); link last.

### r/GithubCopilot (Thursday 2026-12-10)

The same post framed around Copilot CLI: the `~/.copilot/hooks/ctx-guard.json`
hook, the status line segment in the footer, and the fact that a crashing hook
can't deny a tool call.

## X thread

### Post 1

AI coding agents waste context on command output, and sometimes try to install
packages that don't exist.

ctx-guard is a set of open-source hooks that trims the output, keeps the full
log on your machine, and blocks made-up installs.

Claude Code, Copilot CLI, Antigravity CLI, Codex CLI.

### Post 2

Before a shell command runs, it can swap it for a leaner version:

- `git log` → the last 20 commits
- `git status` → porcelain
- your test suite → errors, first and last lines, and a pointer to the full log

The agent greps the full log with `ctx-guard-recall` instead of re-running.

### Post 3

It checks npm, PyPI, crates.io and RubyGems before an install:

`pip install reqeusts` → blocked: no such package (did you mean requests?)

Lookalikes, brand-new packages, `curl | bash` and unpinned versions get a
warning but still run.

### Post 4

It never makes up a savings number. Measured savings come from commands where
it saw both the full output and what it returned; estimates are labelled.

The status line shows today's total, or how many calls it checked on a day
with nothing to trim.

### Post 5

Safety:

- bash and the Python stdlib, no dependencies
- no network calls except the registry check (`CTX_GUARD_PKG_CHECK=0` turns it off)
- secrets masked before output is saved or shown (pattern-based, so a safety net)
- a failing hook steps aside
- your permission prompts still apply
- uninstall removes only what it added

### Post 6

Try it:

```bash
brew install ctx-guard/tap/ctx-guard
ctx-guard install
ctx-guard try
```

I'm looking for install reports, rules that are too aggressive, new package
fixtures, and security feedback.

https://github.com/viktor-tapovski/ctx-guard

## Measurement rule for public posts

Any savings or catch-rate number in a post must name:

- the ctx-guard version or commit;
- the fixture or command;
- the operating system;
- original and returned bytes, or the fixture counts;
- the limitations.

Never turn a rewrite count or an estimate into a claimed savings percentage.

## Metrics

Track weekly:

- stars, forks and unique clones;
- release tarball downloads (Homebrew and `get.sh` both fetch it; a
  third-party tap has no install analytics of its own);
- issues opened and resolved;
- successful clean-machine installs reported;
- security reviewer answers (approved, rejected, why);
- package fixtures submitted by users;
- most common agents and versions;
- uninstall reports.
