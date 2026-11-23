# T08: Sink gate in `pre_bash_rewrite.py`

**Depends on:** T07 · **Phase:** 3 · **Size:** L

## Background

Every studied coding-agent incident ended in one of three things: a config
write (Copilot CVE-2025-53773 autoApprove, `.cursor/mcp.json`), secret
exfiltration (the GitHub MCP toxic flow, "Comment and Control"), or
destruction (Amazon Q). HarnessSecurity-Bench found command allow and deny
lists to be one of the few mechanisms with a good security/utility tradeoff.
This task gates those sinks **only when the session is tainted**. Read-only
commands are never gated.

## Sink classes

| Class | Examples (match on parsed argv, not substrings) |
|---|---|
| **egress** | `curl`/`wget`/`nc`/`ncat`/`ssh`/`scp`/`rsync` to a host not on the allowlist, `git push`, `gh pr create`, `gh issue comment`, `gh gist create`, `npm publish`, `twine upload`, `cargo publish` |
| **persistence/config write** | writes (via `>`, `>>`, `tee`, `cp`, `mv`, `sed -i`, `cat <<EOF >`) to `.claude/settings*.json`, `.vscode/settings.json`, `.cursor/`, `.mcp.json`, `~/.codex/`, `.git/hooks/`, `.github/workflows/`, `~/.bashrc` `~/.zshrc` `~/.profile`, `~/.ssh/`, `crontab` |
| **fetched-code exec** | `curl … \| sh`, `bash <(curl …)`, `python -c "$(curl …)"`, `eval "$(…)"` |
| **destructive** | `rm -rf` outside the repo or on `~`/`/`, `git reset --hard origin`, `git push --force`, cloud deletes (`aws … delete`, `gcloud … delete`, `kubectl delete`) |
| **secret read** (sets `confidentiality_touched`, gated only as a precursor) | `cat .env*`, `~/.aws/`, `~/.config/gh/`, `printenv`, `/proc/*/environ` |

- Egress allowlist: `CTX_GUARD_EGRESS_ALLOW` (comma-separated hosts) plus
  `.ctx-guard/egress-allowlist`. The format mirrors the existing
  `pkg-allowlist`, and ctx-guard never writes to these files. Defaults:
  `github.com` for `git push` to `origin`, and package registries.
- **Alternate execution paths** must be checked by unwrapping `bash -c`,
  `sh -c`, `env X=… cmd`, `sudo`, `xargs`, `git -c core.sshCommand=…`, and
  heredocs. Anything unparseable while tainted → treat it as the
  most-sensitive class it might contain → "ask". Document that `python -c`
  with urllib and scripts the agent writes are **not** caught.

## Decision per agent (reuse the existing ask plumbing)

| | Tainted | Tainted + `confidentiality_touched` | `CTX_GUARD_INJECTION=strict` |
|---|---|---|---|
| Claude | ask | egress → **deny** | config write and secret→egress → deny |
| Copilot | ask (autopilot turns it into deny; acceptable) | egress → deny | same as Claude |
| Antigravity | `force_ask` | egress → deny | same |
| Codex | `systemMessage` warning (no "ask" support) | egress → **deny** | config write → deny |

The reason text is factual. It names the taint source tool and rule and the
sink class, e.g. `ctx-guard: session read untrusted content (WebFetch,
provenance); this command sends data to example.com. Approve if intended.`
It must never include payload text.

## Tests: `tests/test_sink_gate.py`

- A table-driven list of 80 or more commands × {untainted, tainted, tainted+confidential} × each agent → the expected decision.
- **Utility set:** common dev commands after taint must stay "allow": `git status`, `npm test`, `pytest`, `ls`, `cat src/x.py`, `git commit`, `git push` to origin when the allowlist covers it.
- **Bypass set:** the alternate paths above. Record the known misses as `expected_miss` rows.
- Taint read failure → allow (fail open). Hook crash → exit 0.
- `off` → no gating.

## Acceptance criteria

All tests pass, and CI passes. The utility set has zero "ask" decisions.
Measure p95 added latency and report it in the PR.
