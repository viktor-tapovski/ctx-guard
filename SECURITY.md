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

The interceptor denies `env`, `printenv`, `set`, and `export` commands because
they commonly expose credentials. It also redacts common bearer tokens, API
keys, passwords, private-key blocks, and known token formats before output is
returned to the model or retained in the default log.

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
  and preserves modified user files.
- Package-install registry lookups fail open on any network error (timeout,
  DNS failure, unexpected status) rather than blocking the command.
- `~/.ctx-guard/pkg-allowlist` and `.ctx-guard/pkg-allowlist` are read-only
  configuration inputs; ctx-guard never writes to them.
