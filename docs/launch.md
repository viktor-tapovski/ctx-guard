# Public launch plan

## GitHub

### Repository description

> Self-hosted hooks that keep AI coding-agent command output compact, with measured savings and full local logs. Claude Code + Copilot CLI.

### Topics

`ai-coding`, `developer-tools`, `llm`, `token-optimization`, `context-window`,
`claude-code`, `github-copilot`, `shell`, `python`, `productivity`

### First release

- **Tag:** `v0.1.0`
- **Title:** `ctx-guard v0.1.0 — measured context reduction for Claude Code and Copilot CLI`
- **Tone:** experimental, transparent, installable, feedback-driven

Release notes should link to:

- the install section in `README.md`;
- `docs/benchmark.md`;
- `docs/compatibility.md`;
- `SECURITY.md`;
- the smoke-test result and the exact commit used for any published benchmark.

Do not attach real command logs to the release.

## Soft-launch sequence

1. Push the repository after reviewing the staged file list and scanning for
   secrets or personal paths.
2. Create the `v0.1.0` release with the compatibility and security notes.
3. Ask 3–5 users to test on clean machines and report:
   - installation success;
   - agent/runtime versions;
   - commands rewritten;
   - measured statistics;
   - regressions or unexpected output.
4. Publish the X thread after the first clean external installs.
5. Reply with individual demonstrations and link recurring problems to GitHub
   issues instead of debating them in the thread.

## X/Twitter thread draft

### Post 1

AI coding agents waste context on command output.

I built `ctx-guard`, a small self-hosted hook layer that keeps useful output
and archives the full result locally.

It currently supports Claude Code and GitHub Copilot CLI.

### Post 2

It handles common noisy commands such as:

- `git status` and `git log`
- recursive search and file listing
- test/build runners
- Docker, Kubernetes, Terraform, npm, and pip output

The goal is not to hide failures. Errors, warnings, head, tail, and the
redacted log path remain available.

### Post 3

The important part: ctx-guard separates:

- **MEASURED** byte reductions from a real before/after wrapper;
- **OBSERVED** rewrite counts where no unbounded baseline was executed.

No universal “90% savings” claim—run the benchmark on your own workload.

### Post 4

Safety properties:

- local-only processing;
- credential-aware redaction by default;
- redaction failure withholds output;
- logs archived with restrictive permissions;
- bounded retention;
- environment enumeration blocked;
- fail-safe hooks;
- idempotent install;
- targeted uninstall.

Read the security notes before using it with sensitive repositories. Redaction
is pattern-based and is not a guarantee that arbitrary secrets are safe.

### Post 5

Try it:

```bash
git clone https://github.com/viktortapovski/ctx-guard.git
cd ctx-guard
bash install.sh
```

Then restart your agent and run:

```bash
ctx-guard-stats
```

### Post 6

I’m soft-launching this as `v0.1.0`.

I’m looking for:

- clean-machine install reports;
- new agent integrations;
- rewrite rules that are too aggressive;
- benchmark fixtures;
- security and privacy feedback.

GitHub: https://github.com/viktortapovski/ctx-guard

## Measurement rule for public posts

Replace any benchmark placeholder with:

- exact ctx-guard commit;
- exact fixture or command;
- operating system;
- original bytes;
- returned bytes;
- reduction percentage;
- limitations.

Never turn an observed rewrite count into a claimed token-savings percentage.

## Metrics

Track weekly:

- stars and forks;
- unique clones;
- release downloads;
- issues opened and resolved;
- successful clean-machine installs;
- benchmark results submitted by users;
- most common agent/runtime versions;
- uninstall or rollback reports.
