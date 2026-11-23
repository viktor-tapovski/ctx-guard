# T03: `hooks/lib/injection_scan.py` sanitizer module

**Depends on:** T02 (soft; write the module first if T02 isn't merged, then run against the corpus) · **Phase:** 1 · **Size:** M

## Background

This is a stdlib detector and sanitizer, ranked by value per false positive.
See the report section "Detection catches the opportunist, never the adaptive
attacker":

| Tier | Rules | Action |
|---|---|---|
| 1 | Invisible Unicode: Tag block U+E0000–E007F, bidi controls (U+202A–202E, U+2066–2069), U+2061–2064, private-use, unassigned | **Strip** and count. Decode Tag text into the warning as evidence (truncated) |
| 1b | Zero-width (U+200B–200D, U+2060, U+FEFF) and variation selectors (U+FE00–FE0F, U+E0100–E01EF) | Strip **only** long runs (more than 4 in a row) or runs outside emoji context. Keep a ZWJ inside emoji and a BOM at offset 0 |
| 2 | Context-escape markers: `</tool_result>`, `<system-reminder>`, `<system>`, `</function_results>`, line-initial `Human:` / `Assistant:` / `System:`, chat-template tokens (`<\|im_start\|>`, `<\|eot_id\|>`, `[INST]`…) | **Escape** (`</tool_result>` → `<\/tool_result>`) and annotate |
| 3 | Exfiltration-shaped markup: markdown image or link whose URL query carries a long or high-entropy value, `<img src=…?…>`, Mermaid image URLs | **Defang** the URL (`https://` → `hxxps://`, or drop the query) and annotate |
| 4 | Phrase override: a qualifier (previous/prior/above/earlier) **and** an instruction noun (instructions/rules/prompt/guidelines) **and** a second signal (addresses "you", "the assistant", "AI", "Claude", "agent") | **Annotate only**, never strip or taint on its own |

OWASP: NFKC normalization does **not** remove zero-width characters or map
homoglyphs, so the stripping must be explicit. Scan a normalized copy for the
regex tiers, but rewrite only the original model-facing text.

## Deliverable: the API

```python
# hooks/lib/injection_scan.py  (stdlib only)
@dataclass(frozen=True)
class Finding:
    rule: str          # e.g. "tag-unicode", "role-marker"
    count: int
    severity: str      # "high" (tiers 1-3) | "low" (tier 4)
    evidence: str      # SAFE summary, never raw payload > 80 chars; decoded tag text truncated

def scan(text: str, *, max_regex_bytes: int = 256_000) -> tuple[str, list[Finding]]:
    """Return (sanitized_text, findings). Never raises on any str input."""

def summary_line(findings) -> str | None:
    """'ctx-guard: output contains hidden-unicode:37, role-marker:1 (treated as data)' or None."""

def taints(findings) -> bool:
    """True if any high-severity finding is present (tier 4 alone never taints)."""
```

- Replace each stripped run with a visible marker: `[ctx-guard: removed N hidden chars]`.
- Read the `CTX_GUARD_INJECTION` switch (add the helper to `ctx_guard_common.py` if it is missing). When the switch is `off`, `scan` returns `(text, [])`.

## Tests: `tests/test_injection_scan.py`

- Run every corpus case from T02:
  - `attack` cases must produce at least the expected rules.
  - `benign` cases must produce **no high-severity finding**. Tier 4 low-severity hits are allowed but must be counted and printed.
  - `expected_miss` cases must produce no findings. If one starts matching, flip it to `attack` deliberately.
- Idempotence: `scan(scan(x)[0])[0] == scan(x)[0]`.
- No raw payload over 80 chars in any `evidence`.
- Performance: a 1 MB mixed input scans in under 20 ms on CI hardware. Use a loose bound (e.g. 100 ms) as the assertion and print the measured time.
- Fuzz: 1,000 random Unicode strings never raise.

## Acceptance criteria

- The API above is implemented exactly, with docstrings.
- Benign high-severity FPR is 0 on the T02 corpus. Report the tier-4 count.
- Stdlib check and CI pass.

## Out of scope

Wiring into any hook or wrapper (T04, T06, T10).
