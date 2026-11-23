# T13: Optional external classifier hook

**Depends on:** T03 · **Phase:** 5 · **Size:** S

## Background

Llama Prompt Guard 2 (22M/86M) is better than older classifiers. Meta reports
19.3 ms per 512 tokens on an A100. Independent tests measured about 270 ms and
found it weak against encoded attacks, and it needs torch plus Llama-licensed
weights. It must therefore stay **out of** the stdlib core and be **off by
default**. Its score annotates; it never blocks or taints on its own unless
the user opts in.

## Steps

1. In `injection_scan.py`, add an optional hook: if `CTX_GUARD_PI_SCANNER` is
   set to an executable path, pipe the (capped) text to it on stdin, with a
   timeout of `CTX_GUARD_PI_SCANNER_TIMEOUT_MS` (default 300). Read a JSON
   line back: `{"score": 0.0-1.0, "label": "..."}`.
2. If `score >= CTX_GUARD_PI_SCANNER_THRESHOLD` (default 0.9), add
   `Finding(rule="classifier", severity="low", …)`. Treat it as "high" (able
   to taint) only if `CTX_GUARD_PI_SCANNER_TAINTS=1`.
3. Timeout, non-zero exit or bad JSON → ignore it silently, and count it in
   stats as `classifier_error`.
4. Add `contrib/prompt-guard-scanner/` with an example wrapper script, a
   README covering model licence acceptance, install, and expected latency on
   CPU, and a note that it is not covered by CI. Exclude `contrib/` from
   `check_stdlib_only.py` if needed, and say so.

## Tests

Use a fake scanner script under `tests/fixtures/` that returns fixed scores,
sleeps past the timeout, or prints garbage. Assert the findings and that
nothing ever raises.

## Acceptance criteria

The tests pass. Default behaviour is unchanged when the variable is unset. The
stdlib check passes, and CI passes.
