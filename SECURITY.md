# Security and privacy

## Scope

ctx-guard runs local shell and Python hooks that inspect agent tool calls and
command output. It does not send command output, statistics, or logs to a
remote service. The package-install security check (see README) is the one
exception: for `npm`/`pip`/`cargo`/`gem` install commands, it looks up the
package name against that ecosystem's public registry (registry.npmjs.org,
pypi.org, crates.io, rubygems.org) to check existence, publish age, and
typosquat distance. Only the package name is sent — never command output, file
contents, or credentials. Set `CTX_GUARD_PKG_CHECK=0` to disable this lookup
entirely.

## Sensitive output

Command output is archived locally in redacted form so compressed output can be
inspected without re-running a command. The source command may contain
secrets, tokens, source code, or personal paths. Review the configured log
directory and retention period before using ctx-guard on sensitive repositories.

The default runtime directory is `/tmp/ctx-guard-<uid>/`. Runtime directories
and log files are created with restrictive permissions. Set
`CTX_GUARD_LOG_DIR`, `CTX_GUARD_STATE_DIR`, and
`CTX_GUARD_LOG_RETENTION_DAYS` to fit your environment. Logs are redacted by
default with `CTX_GUARD_REDACT_LOGS=1`; setting it to `0` stores raw output and
should be reserved for an explicitly trusted local workflow.

Usage stats (`stats.jsonl`, byte counts and rule names, no command output) persist in
`${XDG_STATE_HOME:-~/.local/state}/ctx-guard/` (dir `700`, file `600`).

The interceptor denies `env`, `printenv`, `set`, and `export` commands because
they commonly expose credentials. For commands it wraps through
`ctx-guard-run` (test runners, builds, compound commands), it also redacts
common bearer tokens, API keys, passwords, private-key blocks, and known token
formats before output is returned to the model or retained in the default log.
Output from commands it only rewrites (such as `git status`) and from the
agent's built-in file tools reaches the agent unfiltered, as it would without
ctx-guard.

### What redaction catches

`redact_file` in `bin/ctx-guard-run` applies these patterns line by line before
anything is archived or returned. `tests/test_redaction.py` runs every pattern
against a corpus of fake secrets in realistic log lines
(`tests/fixtures/redaction/corpus.json`).

| Pattern | Example shape (fake) | Replaced with |
|---|---|---|
| PEM private-key blocks, including multi-line (`-----BEGIN ... PRIVATE KEY-----` to `END`) | RSA, EC, OpenSSH keys | `[REDACTED PRIVATE KEY]` per line |
| `Authorization: Bearer`, `Basic` or `token` headers | `Authorization: Bearer abc...` | value -> `[REDACTED]` |
| Credentials in URLs (`scheme://user:password@host`) | Postgres, MongoDB (`mongodb+srv`), Redis, git clone URLs | password -> `[REDACTED]` |
| `key=value` / `key: value` / JSON `"key": "value"` where the key contains `password`, `passwd`, `secret`, `token`, `api_key`, `access_key`, `access_token`, `auth_token`, `client_secret`, `private_key` or `credential(s)`, with any prefix or suffix | `.env` lines (`DATABASE_PASSWORD=`, `AWS_SECRET_ACCESS_KEY=`), `aws_secret_access_key = ...`, `.npmrc` `_authToken=` | value -> `[REDACTED]` |
| GitHub tokens: `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_` | anywhere in a line | `[REDACTED TOKEN]` |
| Slack tokens: `xoxb-`, `xoxa-`, `xoxp-`, `xoxr-`, `xoxs-` | anywhere in a line | `[REDACTED TOKEN]` |
| AWS access key IDs: `AKIA` + 16 characters | anywhere in a line | `[REDACTED TOKEN]` |
| `sk-` API keys (OpenAI, Anthropic style), npm `npm_` tokens | anywhere in a line | `[REDACTED TOKEN]` |
| JWTs (`eyJ...`.`eyJ...`.signature) | cookies, logged headers | `[REDACTED TOKEN]` |

If the sanitizer cannot run or exits with an error, the archive holds only
`ctx-guard: output withheld because redaction failed` and nothing raw is kept
or returned (fail closed; covered by `tests/test_redaction.py`). Command labels
that `ctx-guard-recall list` shows go through the same sanitizer.

Known limitations:

- A bare secret with no recognizable prefix or key name, such as an AWS secret
  access key printed alone or a random password on its own line, is not caught.
- Key names must contain one of the keywords above as a separate word part:
  `DB_PASSWORD=` is caught, camelCase like `githubToken=` and joined words like
  `mysecret=` are not.
- Values are cut at whitespace, a comma or a quote, so a quoted secret that
  contains spaces is only partly redacted.
- Provider formats not listed (for example Google `AIza` keys, Stripe
  `sk_live_`/`rk_` keys outside a `secret`- or `token`-named assignment, Azure
  `AccountKey=` connection strings) are not recognized.
- Binary output and other encodings are decoded leniently; a secret split
  across lines (other than a PEM block) is not detected.
- `CTX_GUARD_REDACT_LOGS=0` disables all of the above.

Redaction is pattern-based and cannot guarantee detection of every possible
secret. Do not use ctx-guard as a substitute for secret management, agent
permissions, repository review, or provider-side data controls. Any output
that reaches an AI agent may be transmitted by that provider.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository when
available. Include:

- the affected version or commit;
- the operating system and shell;
- the agent integration and hook payload shape;
- reproduction steps that do not disclose real secrets;
- the expected and observed behavior.

Do not open a public issue containing credentials, private source code, or
unredacted command output.

## Safe defaults

- Hooks catch failures and exit successfully so a hook error does not block a
  command.
- Redaction failures withhold command output rather than returning unsanitized
  output.
- Compound commands are wrapped rather than rewritten with targeted patterns.
- Session identifiers are validated before being used in state-file names.
- `ctx-guard-uninstall` removes only exact manifest-owned configuration entries
  and preserves modified user files. A full uninstall deletes `~/.ctx-guard`,
  including a `~/.ctx-guard/pkg-allowlist` you created there; per-repo
  `.ctx-guard/pkg-allowlist` files are not touched.
- Package-install registry lookups fail open on any network error (timeout,
  DNS failure, unexpected status) rather than blocking the command.
- `~/.ctx-guard/pkg-allowlist` and `.ctx-guard/pkg-allowlist` are read-only
  configuration inputs; ctx-guard never writes to them.
