# Contributing to ctx-guard

Thanks for helping improve ctx-guard.

## Before opening an issue

Confirm the behavior with the latest checkout and include:

- operating system and shell;
- Python version;
- Claude Code or Copilot CLI version;
- the hook event and payload shape, with secrets removed;
- the command that was rewritten or wrapped;
- relevant `ctx-guard-stats --json` output.

Never attach unredacted logs. Command output may contain credentials, source
code, or private paths.

## Development workflow

Run the focused checks from the repository root:

```bash
bash -n install.sh bin/ctx-guard-run
python3 -m py_compile hooks/*.py hooks/lib/*.py
bash tests/smoke.sh
```

Keep changes small and add a smoke-test assertion for every new rewrite rule,
payload shape, permission behavior, or statistics field.

## Pull requests

Explain:

- the user-visible behavior;
- the supported agent/runtime versions;
- security or privacy implications;
- how the change was tested on macOS or Linux.

Do not include personal configuration files, `.DS_Store`, runtime logs, or
generated hook state.
