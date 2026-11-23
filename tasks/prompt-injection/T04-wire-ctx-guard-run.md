# T04: Wire the sanitizer into `ctx-guard-run`

**Depends on:** T03 · **Phase:** 1 · **Size:** M

## Background

`bin/ctx-guard-run` already passes all wrapped-command output through
`redact_file` (defined around line 114, called around line 176) before
archiving it or showing it to the model. Redaction fails closed: if the
sanitizer errors, only `ctx-guard: output withheld because redaction failed`
is returned. Hooking the injection scan into this step covers Claude, Copilot
and Antigravity for every wrapped command (runners, builds, compound commands).

## Steps

1. Call `injection_scan.scan` on the redacted text inside the same Python step
   as redaction (or immediately after it), so it inherits the fail-closed
   contract. The order is redact → scan, so the scanner never sees secrets.
2. If `summary_line(findings)` is not None, prepend it to the model-facing
   output. It goes above the existing `=== ctx-guard: output compressed …`
   header, or at the top of uncompressed output.
3. **The archive** (what `ctx-guard-recall` reads) keeps the *redacted but not
   injection-sanitized* text, so a human can inspect the original payload. The
   recall output marks it with the same summary line. Decide whether `recall
   show` should also re-sanitize by default, with `--raw` to opt out. The
   recommended answer is yes, because the model calls recall.
4. Taint handoff: `ctx-guard-run` has no session id in filter mode, so emit a
   stable machine-readable marker line when `taints(findings)` is true, e.g.
   `ctx-guard-taint: rule=tag-unicode,role-marker run=<run-id>`. T07 picks it
   up in PostToolUse. Document the format in a comment.
5. Stats: add per-rule hit counts to the sidecar metadata, next to the
   existing `"redacted"` field (around line 281), e.g.
   `"injection": {"tag-unicode": 3}`. Update `bin/ctx-guard-stats` to show a
   total line if that is trivial; otherwise leave a TODO for T14.
6. `ctx-guard-recall list` labels already go through redaction. Also run them
   through the scan.

## Tests

- Extend `tests/test_redaction.py` or add `tests/test_run_injection.py`. Run
  `bin/ctx-guard-run` on fixture commands that `cat` corpus cases, then assert:
  - the summary line is present for attacks and absent for benign cases;
  - hidden characters are absent from the model-facing output;
  - the archive still holds them;
  - the marker line appears only for tainting findings.
- Fail-closed: monkeypatch or force the scanner to raise, then assert the
  withheld message is returned and nothing raw is returned.
- `CTX_GUARD_INJECTION=off` gives byte-identical output to today.
- Update `tests/smoke.sh` if it snapshots output.

## Acceptance criteria

- All of the above tests pass, and CI passes.
- `bin/ctx-guard-try` still runs cleanly, and its demo output is unchanged except where intentional.
