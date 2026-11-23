# T15: User-facing docs: feature, limits, honest claims

**Depends on:** T04, T08 (finish after all shipped tasks) · **Phase:** 5 · **Size:** S

## Background

Vendors are candid that prompt injection is not solved. OpenAI calls it
"unlikely to ever be fully 'solved'". GitHub's firewall "should not be
considered a comprehensive security solution". ctx-guard's docs must match
that candour. Describe **only features that have actually shipped**; check
each against the code before documenting it.

## Steps

1. **SECURITY.md**: add a "Prompt injection" section with:
   - What it does: hidden-Unicode stripping, marker escaping, exfil-link
     defanging, tainted-session gating of the listed sink classes, and
     per-agent coverage (the capability matrix from the backlog README).
   - **What it will not stop.** Paraphrase without trigger words; non-English
     instructions; payloads split across lines or files; homoglyphs; encodings
     outside the decode path; `python -c` with urllib, scripts the agent writes
     and runs, and DNS exfiltration (CVE-2025-55284). Content ctx-guard can't
     see: Codex hosted WebSearch, Antigravity non-shell tools, and the raw page
     behind Claude's WebFetch summarizer.
   - The fail-open choice for taint reads, and the fail-closed choice for
     sanitizer errors in `ctx-guard-run`.
   - The measured FPR from T14, and a link to the `docs/benchmark.md` section.
   - The suggested framing: "a heuristic, defense-in-depth layer that catches
     common unobfuscated payloads and hidden Unicode, and deterministically
     gates specific dangerous follow-on actions. The patterns are public and
     adaptive attackers will evade them. It is no substitute for sandboxing,
     least-privilege tokens and keeping approval prompts on."
2. **README.md**: a short "What you'll notice" entry, with an example summary
   line and an example "ask", plus the `CTX_GUARD_INJECTION` switch and
   `ctx-guard taint`.
3. **docs/how-it-works.md**: make sure the subsections added by T06, T07 and
   T11 are consistent. Add a diagram or list of the five layers.
4. Optional: add an injection demo step to `bin/ctx-guard-try`, using a Tag
   Unicode README and a gated `curl` after it.

## Acceptance criteria

- No claim of "prevents" or "protects against" prompt injection anywhere (check with `grep -rni "prevent.*injection\|protect.*injection" README.md docs SECURITY.md`).
- Every documented env var and command exists in the code.
- CI passes.
