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

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/). CI lints both
the individual commits and the pull request title, because pull requests are
squash-merged: the title becomes the subject on `main`, and that subject is
what ends up in the changelog.

```
feat: one-line savings gauge for ctx-guard-stats
fix: fall back to ASCII bars on a non-UTF-8 stdout
docs: document CTX_GUARD_BARS
```

`feat` bumps the minor version, `fix` the patch. While ctx-guard is pre-1.0 a
breaking change (`feat!:` or a `BREAKING CHANGE:` footer) bumps the minor too.
`chore`, `ci`, `test`, `refactor`, `build` and `style` are valid but are hidden
from the changelog.

## Releases

Releases are automated with
[release-please](https://github.com/googleapis/release-please-action); nobody
edits `CHANGELOG.md` or version numbers by hand.

1. Merge work into `main` with a conventional squash subject.
2. release-please opens or updates a `chore(main): release X.Y.Z` pull request
   carrying the changelog entry, `version.txt` and `hooks/lib/version.py`.
3. Edit that pull request if a generated bullet reads poorly — commit subjects
   are terser than good release notes, and this is the moment to fix that.
4. Merge it. The tag `vX.Y.Z` and the GitHub release are created for you.

## Pull requests

Explain:

- the user-visible behavior;
- the supported agent/runtime versions;
- security or privacy implications;
- how the change was tested on macOS or Linux.

Do not include personal configuration files, `.DS_Store`, runtime logs, or
generated hook state.
