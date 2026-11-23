# T09: `ctx-guard taint` CLI and protection against self-clearing

**Depends on:** T07 · **Phase:** 3 · **Size:** S

## Steps

1. Add a `taint` subcommand to `bin/ctx-guard`, following how existing
   subcommands dispatch:
   - `ctx-guard taint status [--session ID]`: lists tainted sessions (newest
     first), each with its events (ts, tool, source, rule) and
     `confidentiality_touched`.
   - `ctx-guard taint clear [--session ID | --all]`: clears taint, and asks
     for confirmation when stdin is a TTY.
2. **The model must not clear its own taint.** In `pre_bash_rewrite.py`, deny
   any command that invokes `ctx-guard taint clear`, or deletes or writes
   `*.taint` files in the state dir, while the session is tainted. The reason
   says the user must run it themselves in their own terminal. `status` is
   allowed.
3. Add a `ctx-guard-uninstall` cleanup for `*.taint` files if uninstall
   already removes other state files.
4. Mention it in `bin/ctx-guard --help` and the README command list.

## Tests

- CLI status and clear against a temporary `CTX_GUARD_STATE_DIR`.
- Self-clear attempts (`ctx-guard taint clear`, `bash -c 'ctx-guard taint clear'`, `rm $STATE_DIR/x.taint`) → deny when tainted.

## Acceptance criteria

The tests pass, `bash -n bin/ctx-guard` passes, and CI passes.
