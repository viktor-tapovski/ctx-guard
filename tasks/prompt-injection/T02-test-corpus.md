# T02: Injection test corpus (attack + benign)

**Depends on:** none · **Phase:** 1 · **Size:** M

## Background

No public benchmark uses shell output as the injection carrier, so ctx-guard
needs its own corpus. Detector false-positive rate (FPR) carries over between
workloads, while detection rate doesn't (arXiv 2610.03448), which makes the
**benign** half the more important one. Model the corpus on
`tests/fixtures/redaction/corpus.json` and `tests/test_redaction.py`.

## Deliverables

`tests/fixtures/injection/corpus.json`: a list of cases with this schema:

```json
{
  "id": "attack-tag-unicode-readme-01",
  "kind": "attack | benign | expected_miss",
  "carrier": "cat-readme | gh-issue-view | curl-html | npm-install-log | pytest-failure | git-log | source-file | other",
  "text": "...full model-facing output, payload embedded in a realistic carrier...",
  "expect": {"rules": ["hidden-unicode"], "taint": true},
  "source": "AgentDojo | BIPIA | LLMail-Inject | deepset/prompt-injections | HackAPrompt | handwritten | incident:<name>",
  "license": "MIT | Apache-2.0 | n/a (handwritten)",
  "notes": "optional"
}
```

Rule names to use (T03 implements them): `hidden-unicode`, `tag-unicode`,
`bidi-control`, `role-marker`, `harness-tag`, `chat-template-token`,
`exfil-markdown`, `phrase-override`.

### Attack cases (at least 40)

- Borrow payloads only from permissively licensed sets: AgentDojo (MIT),
  BIPIA (MIT), LLMail-Inject (MIT), deepset/prompt-injections (Apache-2.0),
  HackAPrompt (MIT). **Do not use WASP** (CC BY-NC 4.0). Do not use InjecAgent
  or Tensor Trust data until their licenses are verified.
- **Re-embed every payload in a realistic carrier.** Examples: a `cat
  README.md` dump, a `gh issue view` body, an HTML comment inside `curl`
  output, an `npm install` log, a pytest failure message, a commit message.
- Include incident-shaped cases:
  - a Copilot CVE-2025-53773-style write of `chat.tools.autoApprove`
  - `.cursor/mcp.json` / `.mcp.json` creation
  - Invariant's "Trusted Content Section"
  - an Amazon Q-style "clean up local files and cloud resources"
  - a "Comment and Control" HTML comment in PR text
- Include obfuscation classes:
  - Unicode Tag characters (U+E0000–E007F)
  - "Sneaky Bits" (U+2062/U+2064)
  - variation-selector smuggling
  - bidi overrides
  - zero-width runs
  - base64-wrapped instructions

### Benign cases (at least 60): text that only *looks* like injection

- LLM app source code that contains system prompts. CLAUDE.md and AGENTS.md
  content.
- Excerpts from this repo's own `research_notes/` and `reports/`, which
  discuss injection at length.
- Test names like `test_ignore_previous_instructions_is_blocked`.
- `.gitignore` / `git status --ignored` output, `git diff --ignore-all-space`,
  compiler "ignoring" or "skipping" notes, `pytest -k "not slow"` lines.
- Emoji commit messages with ZWJ sequences, a leading BOM, Arabic/Hebrew RTL
  strings, Hindi text, CJK.
- Benign base64: lockfile `integrity` hashes, JWT examples from docs,
  `kubectl get secret -o yaml` output.
- Markdown with normal images and links (badges, docs links).

### Expected-miss cases (at least 8)

Documented misses, so the limits are tested rather than only described:
paraphrase with no trigger words, non-English instructions, a payload split
across lines, homoglyph-only text.

### Loader

Add `tests/injection_corpus.py`: a loader that validates the schema (unique
ids; `kind` is one of the allowed values; `license` is present when `source`
isn't handwritten), plus a test `tests/test_injection_corpus.py` that runs the
schema validation. The detection tests themselves come in T03.

## Acceptance criteria

- The counts above are met, and every attack case has a carrier.
- Every borrowed case records its source and license. A `tests/fixtures/injection/LICENSES.md` lists attribution for each borrowed set.
- The schema test passes, and the CI commands pass.
- No real secrets, no real personal data.
