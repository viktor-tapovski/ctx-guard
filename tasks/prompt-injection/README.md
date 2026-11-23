# Prompt-injection defense: task backlog

Implementation tasks derived from
[`reports/Prompt injection defenses for ctx-guard.md`](../../reports/Prompt%20injection%20defenses%20for%20ctx-guard.md)
(source notes in `research_notes/Prompt injection defenses for ctx-guard/`).

Each `T*.md` file is a self-contained brief for one subagent. To run one, hand
a subagent this README **and** the task file, e.g.:

> Read `tasks/prompt-injection/README.md` and `tasks/prompt-injection/T03-injection-scan-module.md`, then implement T03. Follow the shared conventions in the README.

## Goal in one paragraph

Treat prompt injection mainly as an **action-control** problem. Once a session
has read untrusted content, deterministically gate the few sinks every real
coding-agent exploit ends in: writes to agent config, reading secrets and then
sending data out, and destructive or fetched-code execution. Underneath, a
cheap stdlib sanitizer strips invisible Unicode, neutralizes fake role and
harness markers, and defangs exfiltration links. It **annotates rather than
blocks**. The docs must never claim ctx-guard "prevents" prompt injection.

## Task list and dependencies

| ID | Task | Depends on | Phase | Needs a live agent? |
|---|---|---|---|---|
| T01 | [Fix stale docs about PostToolUse output](T01-fix-stale-docs.md) | none | 1 | no |
| T02 | [Injection test corpus (attack + benign)](T02-test-corpus.md) | none | 1 | no |
| T03 | [`injection_scan.py` sanitizer module](T03-injection-scan-module.md) | T02 (soft) | 1 | no |
| T04 | [Wire sanitizer into `ctx-guard-run`](T04-wire-ctx-guard-run.md) | T03 | 1 | no |
| T05 | [Capture real `tool_response` payloads](T05-capture-tool-response-fixtures.md) | none | 2 | **yes** |
| T06 | [PostToolUse output rewrite in `context_monitor.py`](T06-posttooluse-rewrite.md) | T03, T05 | 2 | no (uses T05 fixtures) |
| T07 | [Session taint state library + marking](T07-taint-state.md) | T03 | 3 | no |
| T08 | [Sink gate in `pre_bash_rewrite.py`](T08-sink-gate.md) | T07 | 3 | no |
| T09 | [`ctx-guard taint` CLI + clearing rules](T09-taint-cli.md) | T07 | 3 | no |
| T10 | [Codex PostToolUse registration](T10-codex-posttooluse.md) | T03, T07 | 3 | no |
| T11 | [Untrusted-carrier envelope + wrapping curl/wget/cat-large](T11-untrusted-envelope.md) | T04 | 4 | no |
| T12 | [SessionStart posture check](T12-posture-check.md) | none | 5 | no |
| T13 | [Optional external classifier hook](T13-external-classifier.md) | T03 | 5 | no |
| T14 | [Evaluation harness + benchmark report](T14-evaluation.md) | T02, T04, T06, T08 | 5 | no |
| T15 | [User-facing docs: feature, limits, honest claims](T15-user-docs.md) | T04, T08 (finish last) | 5 | no |

Tasks that can run in parallel from the start: **T01, T02, T05, T12**. After
T03: **T04, T07, T13** in parallel. After T07: **T08, T09, T10** in parallel.

## Shared conventions (every task must follow these)

1. **Stdlib only.** No third-party imports in `hooks/` or `bin/`.
   `python3 tests/check_stdlib_only.py` must pass.
2. **Hooks never crash.** Every hook body stays inside its top-level
   `try/except` and exits `0`. Copilot fails a crashed `preToolUse` hook
   *closed*, so a crash would deny the user's command. Hook errors **fail open**
   (original output, no taint, allow), with one exception: the sanitizer inside
   `ctx-guard-run` inherits the existing fail-closed redaction contract
   ("output withheld because redaction failed").
3. **One master switch.** `CTX_GUARD_INJECTION` = `on` (default) | `off` |
   `strict`. `off` disables every behavior added by this backlog. `strict`
   upgrades selected "ask" gates to "deny" (see T08). Read it through a single
   helper in `hooks/lib/ctx_guard_common.py`, added by whichever task lands
   first. Later tasks reuse it.
4. **Never store attacker text.** Taint records, stats and warnings hold rule
   names, counts and sources, never the matched payload.
5. **Factual warnings, not imperatives.** Text that ctx-guard injects into
   context reads like `ctx-guard: output contains hidden-unicode:37 (treated as
   data)`. Never write "SYSTEM: ignore…" or anything shaped like a system or
   user turn. Claude Code's docs warn that imperative out-of-band text can trip
   the model's own injection defenses.
6. **Latency budget.** Under 20 ms added per hook call on a 1 MB output.
   Precompile regexes at import time and scan a capped prefix for regex tiers.
   The Unicode pass may scan the full text.
7. **Per-agent capability matrix** (from the report). Do not assume more than
   this:

   | Agent | Replace output | Warn after output | PreToolUse "ask" | "deny" |
   |---|---|---|---|---|
   | Claude Code | `updatedToolOutput` (v2.1.121+, must match tool schema or is silently ignored) | `additionalContext` | yes | yes |
   | Copilot CLI | `modifiedResult: {resultType, textResultForLlm}` | `additionalContext` (≤10 KB) | yes (denied in autopilot) | yes |
   | Codex CLI | whole result only, via `decision:"block"` | `additionalContext` | **no** | yes |
   | Antigravity CLI | **no** (PostToolUse gets no output) | no | yes, plus `force_ask` | yes |

8. **Tests and CI.** Add tests under `tests/test_*.py` using `unittest`, with
   fixtures under `tests/fixtures/`. Before finishing, run everything CI runs:
   ```bash
   for f in install.sh get.sh packaging/render.sh bin/ctx-guard bin/ctx-guard-run bin/ctx-guard-try; do bash -n "$f"; done
   python3 -m py_compile hooks/*.py hooks/lib/*.py bin/ctx-guard-stats bin/ctx-guard-uninstall
   python3 tests/check_stdlib_only.py
   bash tests/smoke.sh
   python3 -m unittest discover -s tests -p 'test_*.py'
   ```
9. **Commits** follow Conventional Commits (`commitlint.config.cjs`), e.g.
   `feat(injection): add hidden-unicode sanitizer`. Use one task per commit or
   PR where practical, and mention the task ID in the body.
10. **Scope discipline.** Touch only the files a task lists, unless a change is
    strictly required, and say why in the commit. Do not edit `research_notes/`
    or `reports/`.
11. **When the report is wrong or a doc claim can't be verified**, stop and
    write the discrepancy into the task's "Open questions" in your final
    summary. Do not invent API shapes, especially hook output fields.
