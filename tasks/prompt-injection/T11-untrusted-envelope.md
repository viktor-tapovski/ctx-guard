# T11: Untrusted-carrier envelope, context minimization, and wrapping curl/wget/cat-large

**Depends on:** T04 · **Phase:** 4 · **Size:** M

## Background

- **Envelope.** Spotlighting with *random* per-call markers is cheap hygiene.
  It is not protection: the framing has no more authority than attacker text,
  and adaptive attacks defeat it. Datamarking and base64 encoding are rejected
  because they corrupt code and break compression and recall.
- **Context minimization** is the stronger lever. For high-risk carriers, show
  structural facts by default and keep the free-form prose one
  `ctx-guard-recall` away.
- **Coverage gap.** Today the `cat-large` and `grep-unbounded` rewrites pipe
  through `head` unscanned, and `curl`/`wget` aren't wrapped at all. On
  Antigravity, the wrapper is the *only* place sanitizing can happen, because
  its PostToolUse gets no output.

## Steps

1. **Carrier classification** in `pre_bash_rewrite.py`: mark a command as an
   untrusted carrier if it is `gh issue view`, `gh pr view`, `gh api`, `curl`,
   `wget`, or reads files under `node_modules/`, `vendor/`, `site-packages/`.
   Pass `--source <carrier>` to `ctx-guard-run`.
2. **Wrap** those carriers through `ctx-guard-run`, plus the existing
   `cat-large` and `grep-unbounded` rewrites, so they get redaction and the
   scan.
3. **Envelope** for untrusted carriers only:
   ```
   <untrusted-output id="{secrets.token_hex(8)}" source="gh-issue-view">
   …
   </untrusted-output id="…">
   ```
   Escape any literal `<untrusted-output` in the body. Add no imperative
   reminder text, beyond an optional one-line factual note.
4. **Minimization** (opt-in at first, `CTX_GUARD_MINIMIZE_UNTRUSTED=1`). For
   `gh issue view` / `gh pr view` and `curl` HTML, the model sees title,
   author, labels, state, counts, URLs and the first N lines, with the rest
   behind `ctx-guard-recall show <run-id>`. Recall output gets the same
   envelope.
5. Keep `CTX_GUARD_INJECTION=off` byte-identical to today.

## Tests

- Envelope id differs on every run, and a body containing `</untrusted-output id="x">` can't close it.
- Carrier classification table tests.
- Antigravity fixture: a `run_command` with `curl` gets rewritten through the wrapper.
- `cat-large` output now carries the summary line for an attack fixture.

## Acceptance criteria

The tests pass, and CI passes. `docs/how-it-works.md` documents the envelope
and states explicitly that it is not a security boundary.
