# T14: Evaluation harness and benchmark report

**Depends on:** T02, T04, T06, T08 · **Phase:** 5 · **Size:** M

## Background

Report what users feel as prominently as what attackers face. Benchmark
results don't transfer between datasets: the best BIPIA-trained detector
caught 2% of AgentDojo attacks at 1% FPR. FPR does transfer, so it is the
stable number to report. Standard cross-validation overstated AUC by 8–16.5
points. ctx-guard's rules are public, so the realistic attacker is adaptive.

## Steps

1. Add `tests/bench_injection.py`, a script and not a unittest, run manually or
   in a separate CI job. It runs the T02 corpus through the scanner, the
   `ctx-guard-run` path and the sink gate, and reports four numbers:
   1. detection counts by carrier × obfuscation class;
   2. **FPR per benign line and per benign command** (any summary line on a
      benign `pytest`/`npm test` output counts);
   3. **utility pass rate** of benign "untrusted read → normal dev command"
      sequences (gate must allow);
   4. **gate coverage** on "untrusted read → risky command" sequences,
      including alternate execution paths (`bash -c`, `python -c`,
      `sh <<EOF`, `env X=… cmd`, `git -c core.sshCommand=…`);

   plus p50/p95 latency per hook.
2. Write a benign real-world sample: run the scanner over this repo's own
   tree, over `research_notes/`, and over a few popular OSS READMEs fetched
   once and committed as fixtures if their licenses allow. Report the
   high-severity hits; target zero.
3. **Adaptive round:** after freezing the patterns, a separate session or
   person reads the rule list and writes evading payloads for each carrier.
   Commit the successes as `expected_miss` cases in the T02 corpus, and report
   this held-out set separately.
4. Add a "Prompt-injection layer" section to `docs/benchmark.md` in its
   existing MEASURED vs OBSERVED style, labelled **"static corpus; not
   evaluated against adaptive attackers"**, apart from the held-out numbers
   from step 3.

## Acceptance criteria

The script runs in under 60 s. The `docs/benchmark.md` section is written
with real measured numbers, not estimates. All CI passes.
