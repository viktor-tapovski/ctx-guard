# Detecting prompt injection in untrusted text (tool output, files, web content) before it reaches an LLM agent

Context for the report writer: ctx-guard runs commands through `bin/ctx-guard-run`. That script writes raw output to a log, passes it through `redact_file` (defined at `bin/ctx-guard-run:114`, called at `:176`), and withholds the output with "ctx-guard: output withheld because redaction failed" if redaction errors. It has no Unicode or injection handling today: a repo grep for unicode, zero-width and injection found only terminal-rendering helpers in `bin/ctx-guard-stats`. Any detector therefore slots in naturally next to `redact_file`. The constraints are: stdlib-only Python or bash, offline, low latency.

Research was done on 2026-11-23. Sources span 2023 to 2026.

## Q1. Heuristic / pattern-based detection: signatures used in practice, rule sets from open-source projects, and their weaknesses

### Takeaway
Open-source rule sets are small and almost all built around one phrase family: "ignore/disregard/forget ... previous/above ... instructions/context". This family is easy to implement in stdlib Python (Rebuff's heuristic itself uses only `difflib`), but paraphrase, other languages and encoding evade it trivially. OWASP explicitly says input screening "is not a security boundary". Higher-value structural signatures for an agent reading tool output are: invisible Unicode, spoofed conversation turns or role markers, and markdown/HTML that fetches URLs. Real developer content rarely contains these, whereas words like "ignore" and "system" are common in it.

### Cited Findings
**Rebuff (ProtectAI) heuristic detector: exact algorithm, stdlib-only**
- It builds 8,800 candidate phrases from verb × adjective × object × preposition lists. Verbs: "Ignore, Disregard, Skip, Forget, Neglect, Overlook, Omit, Bypass, Pay no attention to, Do not follow, Do not obey". Adjectives: "(empty), prior, previous, preceding, above, foregoing, earlier, initial". Objects: "content, text, instructions, instruction, directives, directive, commands, command, context, conversation, input, inputs, data, message, messages, communication, response, responses, request, requests". Prepositions: "(empty), and start over, and start anew, and begin afresh, and start from scratch". — [Rebuff detect_pi_heuristics.py](https://raw.githubusercontent.com/protectai/rebuff/main/python-sdk/rebuff/detect_pi_heuristics.py)
- Normalization lowercases the text, strips punctuation and underscores, and collapses whitespace. The detector then slides a window of the phrase's word length over the input. Score = word-position-match score (0.5 + 0.5·min(matches/5, 1), or 0 if nothing matches) − 0.1 × `difflib.SequenceMatcher` ratio. It returns the maximum score and defines **no threshold**; the caller must choose one. Matching is positional and exact, so reordered words, typos and synonyms evade it. — [Rebuff detect_pi_heuristics.py](https://raw.githubusercontent.com/protectai/rebuff/main/python-sdk/rebuff/detect_pi_heuristics.py)

**Vigil (deadbits/vigil-llm)**
- Vigil layers several scanners: a vector DB of known attacks (nearest-neighbour similarity), YARA heuristics, a transformer classifier (example: `deepset/deberta-v3-base-injection` at a 0.98 threshold), prompt-response similarity, and canary tokens. Its highlights also mention sentiment, relevance and paraphrase scanners. The README says the project is **alpha** and "experimental / for research purposes". — [Vigil README](https://github.com/deadbits/vigil-llm)
- Its YARA rule `InstructionBypass` uses essentially the same phrase grammar as Rebuff. The verb group is `(Ignore|Disregard|Skip|Forget|Neglect|Overlook|Omit|Bypass|Pay no attention to|Do not follow|Do not obey)`, followed by an optional qualifier ("previous", "above", ...), an object noun ("instructions", "context", "messages", ...), and an optional "start over" clause. — [Vigil instruction_bypass.yar](https://raw.githubusercontent.com/deadbits/vigil-llm/main/data/yara/instruction_bypass.yar)

**OWASP LLM Prompt Injection Prevention Cheat Sheet (current version)**
- The only concrete regexes are in an *output* validator: `SYSTEM\s*[:]\s*You\s+are` (system prompt leak), `API[_\s]KEY[:=]\s*\w+`, and `instructions?[:]\s*\d+\.`. It also refuses responses over 5000 characters. — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- Typoglycemia (scrambled words): Damerau-Levenshtein distance catches some misspellings, "but similarity does not identify intent". — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- Encoded payloads: "Decode Base64 or hex candidates for inspection only". Cap input length *before* normalization, decoding or matching, to bound cost. — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- Hidden document text: render each page and flag extracted text that is not visible on it (PhantomLint pairs phrase matching with an OCR check). — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- Stated limitations: input screening "is not a security boundary". Keyword filters produce false positives *and* miss paraphrased, multilingual or encoded injections. Pattern lists don't translate across languages. Guardrail models are themselves injectable. Best-of-N attacks succeed with repeated attempts. — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- Markdown/HTML exfiltration: the sheet cites hidden image tags that send data to external URLs, and disguised links. Its guidance is to treat output as untrusted and sanitize HTML/Markdown. It gives no sanitizer code. — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)

**Attack classes that define useful signature families (from vendor taxonomy)**
- Azure Prompt Shields' document-attack taxonomy is a good checklist of signature families. Six classes describe the attacker's goal: manipulated content, infrastructure access or backdoors, information gathering (exfiltration or deletion), availability, fraud, and malware or links. Four more describe technique, and those map to concrete signatures:
  - **"attempt to change system rules"** (ignore/forget/disregard rules or previous turns)
  - **"embedding a conversation mockup"**: fake user/assistant turns, i.e. `Human:`/`Assistant:` or `</tool_result>` spoofing
  - **"role-play"**: a new persona
  - **"encoding attacks"**: ciphers, character transforms, "talk in URL encoding"

  — [Microsoft Learn: Prompt Shields](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)

**Real attacks on coding agents (why output-side signatures matter)**
- Rehberger's "Month of AI Bugs" (August 2025) was more than 20 posts, mostly about coding agents. Findings include:
  - Claude Code data exfiltration via DNS requests (CVE-2025-55284)
  - GitHub Copilot RCE (CVE-2025-53773)
  - Cursor exfiltration via Mermaid diagrams that embed image URLs (CVE-2025-54132)
  - A Windsurf entry on sneaking invisible instructions past developers

  — [Embrace The Red wrap-up](https://embracethered.com/blog/posts/2025/wrapping-up-month-of-ai-bugs); [announcement](https://embracethered.com/blog/posts/2025/announcement-the-month-of-ai-bugs); [Simon Willison, "The Summer of Johann"](https://simonwillison.net/2025/Aug/15/the-summer-of-johann/). (Details come from index and summary pages, not every individual post.)
- Simon Willison's commentary: arbitrary command execution, a core coding-agent feature, becomes a serious problem once prompt injection can trigger it. — [Simon Willison](https://simonwillison.net/2025/Aug/15/the-summer-of-johann/)

**Weakness of all static detectors against adaptive attackers**
- "The Attacker Moves Second" (Nasr, Carlini et al., Oct 2025; USENIX Security '26) bypassed 12 published jailbreak and prompt-injection defenses. Most fell at over 90% attack success, even though most originally reported near-zero. Adaptive attacks bypassed "every detector they evaluated, even when multiple guardrails are combined". The authors still call detectors useful against unsophisticated or opportunistic attacks: "a useful—but limited—component". — [arXiv 2510.09023](https://arxiv.org/abs/2510.09023v1); [USENIX listing](https://www.usenix.org/conference/usenixsecurity26/presentation/nasr)

### Inferences
- For ctx-guard, the Rebuff/Vigil phrase grammar ports directly to a stdlib `re` pattern, or to Rebuff's `difflib` window scorer. It should only *annotate*, never block. Developer content contains "skip ... tests", "ignore ... warnings" and "disregard ... data" constantly. The object list includes generic nouns like "data", "input", "response", "request", "content", which would collide with logs and docs. Requiring a qualifier ("previous/prior/above/earlier") **plus** an instruction-noun ("instructions/directives/prompt/rules") makes the pattern much more specific than the full 8,800-phrase grammar.
- Structural signatures are probably more specific for tool output than phrase lists. They target text that pretends to be part of the agent conversation:
  - closing or opening harness tags (`</tool_result>`, `</function_results>`, `<system>`, `<system-reminder>`, `</untrusted...>`)
  - line-initial `Human:`/`Assistant:`/`User:`/`System:` turn markers
  - chat-template control tokens such as `<|im_start|>`, `<|im_end|>`, `<|eot_id|>`, `<|start_header_id|>`, `[INST]`, `<<SYS>>`

  The template-token list comes from general knowledge of public model chat formats and was **not verified against a source in this session**. These rarely appear in ordinary logs, but they do appear in LLM-related source code and docs, so they need an allow or context rule.
- Markdown image or link exfiltration (`![..](https://host/...?q=<data>)`, `<img src=...>`, Mermaid image URLs) is a high-value output signature, because the documented coding-agent exfiltration CVEs used rendering or network side channels.
- An attacker who knows ctx-guard's rules can bypass them trivially (per Nasr et al. and OWASP). The feature should be framed as a tripwire that flags or annotates, not a guarantee.

### Gaps
- I did not fetch LLM Guard's `PromptInjection` scanner config, NeMo Guardrails' jailbreak heuristics (length/perplexity and prefix/suffix perplexity), garak's probe list (e.g. `promptinject`, `encoding`, `latentinjection`), or promptmap's rule files. Their exact signatures are not captured here.
- I found no published false-positive rate for regex/YARA phrase rules on developer corpora (logs, READMEs, code).

## Q2. ML classifiers: sizes, latency, accuracy, evasions, and optional invocation from a local tool

### Takeaway
The best small open classifier is Meta's Llama Prompt Guard 2: 22M or 86M DeBERTa, binary benign/malicious, 512-token window. It is far too heavy for a stdlib-only tool (it needs torch or onnx plus model weights, and accepting the Llama license). It could only be an opt-in external command. Older ProtectAI/deepset DeBERTa models are much weaker on agentic benchmarks and over-flag benign text containing trigger words. Every classifier tested has been evaded by encoding or adaptive attacks. Cloud APIs (Azure Prompt Shields, Lakera) violate ctx-guard's no-network rule.

### Cited Findings
**Meta Llama Prompt Guard 2 (2025)**
- Two sizes: **22M** (DeBERTa-xsmall, English-only) and **86M** (mDeBERTa, multilingual). Output is binary benign/malicious. The context window is **512 tokens**; for longer inputs Meta recommends splitting into segments and scanning them in parallel. — [HF model card PG2-22M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M)
- Model card benchmarks (A100, 512 tokens):

  | Model | AUC (EN) | Recall @1% FPR | Latency |
  |---|---|---|---|
  | PG1 86M | .987 | 21.2% | 92.4 ms |
  | PG2 86M | .998 | 97.5% | 92.4 ms |
  | PG2 22M | .995 | 88.7% | **19.3 ms** |

  On AgentDojo, attack prevention rate at 3% utility loss was 81.2% (86M) and 78.4% (22M). Other models scored far lower: ProtectAI 22.2%, Deepset 13.5%, LLM Warden 12.9%. — [HF model card](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M)
- PG2 uses a modified energy-based loss intended to cut out-of-distribution false positives. It also uses a tokenizer hardened against "fragmented tokens or inserted whitespace". Meta recommends domain fine-tuning to reduce false positives and acknowledges adaptive attacks. License: Llama 4 Community License, with gated download. — [HF model card](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M)
- Independent evaluation conflicts with Meta's numbers. It measured roughly 277 ms (86M) and 267 ms (22M), and found both PG2 variants detect encoding and "controlled-release" attacks poorly (around 0.02–0.23), a regression from PG1. — [arXiv 2510.01529, "Bypassing Prompt Guards in Production with Controlled-Release Prompting"](https://arxiv.org/pdf/2510.01529)
- Llama Guard 4 (12B) is a *content-safety* classifier (MLCommons hazard taxonomy, S1–S14, including "Code Interpreter Abuse"). Prompt-attack detection is handled by the separate Prompt Guard line, which needs no special prompt format. — [Meta Llama Guard 4 model card](https://www.llama.com/docs/model-cards-and-prompt-formats/llama-guard-4/); [Meta Prompt Guard docs](https://www.llama.com/docs/model-cards-and-prompt-formats/prompt-guard/)

**ProtectAI deberta-v3-base-prompt-injection-v2 (used by LLM Guard)**
- The model card claims it is "highly accurate" for English. It says the model is **not recommended for system prompts because it produces false positives**. — [HF model card](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2)
- InjecGuard / NotInject (2024–25) built 339 benign sentences with 1–3 "trigger words" (e.g. "ignore", "cancel"). On this benign set, accuracy was:

  | Model | Accuracy on benign trigger-word set |
  |---|---|
  | PromptGuard (v1) | **0.88%** |
  | Deepset | 5.31% |
  | Fmops | 5.60% |
  | ProtectAIv2 | 56.64% ("close to random guessing") |
  | LakeraGuard | 87.61% |
  | InjecGuard | 87.32% |

  The authors attribute the over-flagging to shortcut learning on trigger words. — [InjecGuard, arXiv 2410.22770](https://arxiv.org/html/2410.22770v3)

**Cloud services (network-dependent)**
- Azure Prompt Shields has two analyzers: `userPrompt` and `documents` (up to 5 documents per call in the standalone API, returning `attackDetected` per document). Document-attack detection is off by default in Foundry. For agents it can scan at the **tool response** intervention point (Preview). It is primarily trained on English. Its false-positive advice is to use annotate mode instead of block and to exempt trusted sources. Page updated Oct 2026. — [Microsoft Learn](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)
- Azure "Spotlighting" (preview) base64-encodes untrusted documents so the model treats them as lower trust. Known side effects: more tokens, and the model mentioning the base64 encoding unprompted. — [Microsoft Learn](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)

### Inferences
- Prompt Guard 2 22M at about 20 ms per 512-token chunk (A100) is feasible only on a GPU. On CPU, and with the independent ~270 ms figure, scanning multi-kilobyte command output in 512-token chunks would add hundreds of milliseconds to seconds per command. That is a poor fit for a default-on hook.
- The only stdlib-compatible way to use a classifier is an **optional external hook**: an environment variable or config naming a command (e.g. `CTX_GUARD_PI_SCANNER="python -m my_pg2_wrapper"`) that ctx-guard pipes text to, reading back a score, with a timeout and fail-open/fail-closed policy. This keeps torch, transformers and the model license out of ctx-guard's dependency set and test gate (`tests/check_stdlib_only.py`).
- Given the NotInject results, any classifier used on developer text (which is full of "ignore", "skip", "override") should run in annotate mode, not block mode.

### Gaps
- Lakera Guard's architecture, latency and current accuracy were not fetched. Only its NotInject over-defense number above is sourced.
- I found no CPU latency figures from Meta for PG2. No source gave PG2 false-positive rates on code or log corpora.
- I did not verify whether an ONNX or quantized PG2 build exists that runs without torch.

## Q3. Canary tokens / known-answer detection, perplexity-based detection (and attention-based)

### Takeaway
All three need an LLM in the loop: a forward pass, attention weights, or token log-probs. None fits a stdlib-only, offline, no-model tool. Known-answer detection (and its fine-tuned successor DataSentinel) has been bypassed by adaptive attacks. Perplexity filters catch gibberish adversarial suffixes, but false-positive on exactly the content developers produce (code, URLs, math). Attention Tracker is promising but requires white-box model internals.

### Cited Findings
- **Known-answer detection / DataSentinel (IEEE S&P 2025).** A detection instruction with a secret key is prepended to the untrusted data and sent to a detector LLM. The data is flagged as contaminated if the key is *not* echoed back, because injected text hijacked the detector. DataSentinel fine-tunes the detector via minimax (detector vs. adaptive attacker) and reports lower false-positive and false-negative rates than known-answer baselines. It was evaluated on 7 target tasks, 9 injected tasks, 6 LLMs and 9 attacks. — [arXiv 2504.11358](https://arxiv.org/pdf/2504.11358)
- Critique: "How Not to Detect Prompt Injections with an LLM" (2025). The detection instruction and key are not truly hidden from an adaptive adversary, and they share the context window with the attack. — [arXiv 2507.05630](https://arxiv.org/html/2507.05630v1)
- Nasr et al. explicitly attacked secret-knowledge defenses (DataSentinel, MELON). For MELON, they found conditional instructions that pass its comparison of tool calls across two runs. — [arXiv 2510.09023](https://arxiv.org/html/2510.09023v1) (details from summaries; full PDF not read)
- **Canary tokens (Vigil, Rebuff)**: a random token is inserted into the prompt, and responses are checked for it to detect prompt leakage or goal hijacking. — [Vigil README](https://github.com/deadbits/vigil-llm)
- **Perplexity (Jain et al. 2023, "Baseline Defenses")**: the filter flags inputs whose log-perplexity exceeds a threshold; a windowed variant was also tested. It flagged roughly 1 in 10 prompts across models. Secondary sources report around 7% benign false positives on AlpacaEval (not verified against the original). — [arXiv 2309.00614](https://arxiv.org/pdf/2309.00614)
- Google's Gemini indirect-injection paper: content containing "code, url, math, (any naturally high perplexity content)" yields high false positives for perplexity filters. — [arXiv 2505.14534, "Lessons from Defending Gemini Against Indirect Prompt Injections"](https://arxiv.org/pdf/2505.14534)
- Alon & Kamfonas found false positives are a significant problem for plain perplexity filtering. A classifier on perplexity plus token length fixed most of them. — [arXiv 2308.14132](https://arxiv.org/pdf/2308.14132.pdf)
- Perplexity filters (PPL and windowed PPL) failed to identify most malicious tool documents in a tool-selection injection study. — [arXiv 2504.19793](https://arxiv.org/pdf/2504.19793)
- **Attention Tracker (Hung et al., NAACL Findings 2025)** is training-free. Certain "important heads" shift the last token's attention from the original instruction to an injected one (the "distraction effect"). The "focus score" sums that attention. It reports up to a 10.0% AUROC gain over prior detectors and works on LMs as small as 1.8B, using no extra inference beyond the original pass. These results are self-reported, with no independent replication found. — [arXiv 2411.00348](https://arxiv.org/abs/2411.00348)

### Inferences
- None of these is implementable in ctx-guard without a local LLM, and ctx-guard cannot see the agent's attention or logits, because the agent's model runs remotely (Anthropic, OpenAI, Google).
- A cheap stdlib proxy for "gibberish" is character-class entropy or a long-unbroken-token heuristic. However, the Gemini finding implies it will fire constantly on minified JS, hashes, base64 blobs and lockfiles. It is not recommended except as a weak signal combined with others.
- A ctx-guard-specific "canary" variant is possible. The hook could tell the agent that tool output is wrapped in a per-invocation random nonce tag (e.g. `<ctxg-output id=8f3a…>`), and flag output that contains a *closing* tag, or any `ctxg-output` string, it didn't emit. That would detect delimiter-escape attempts. This is my own design idea, not a published technique. Its value depends on the agent actually honouring the delimiters, which OWASP notes "do not enforce a boundary".

### Gaps
- No source found on whether DataSentinel's published numbers specifically fall under Nasr et al.'s attack; only summaries were available.
- No published evaluation of perplexity filters on build logs or CI output.

## Q4. Unicode normalization / stripping invisible characters as sanitization (not just detection)

### Takeaway
This is the highest-value, lowest-false-positive, fully stdlib-feasible step. Python's `unicodedata.category()` identifies all of the relevant characters. Unicode Tag characters (U+E0000–U+E007F) and similar invisible encodings let attackers hide ASCII instructions that LLMs read but humans cannot see. Legitimate developer text almost never contains tag characters, PUA characters or bidi overrides. Zero-width joiners and variation selectors do appear legitimately (emoji, some scripts), so they need care. OWASP warns that NFKC alone neither removes zero-width characters nor maps homoglyphs.

### Cited Findings
- **Unicode Tags (U+E0000–U+E007F)** mirror ASCII. Text encoded this way is invisible to users but read by LLMs, because tokenizers saw these code points in training. Rehberger demonstrated ChatGPT acting on hidden instructions. Recommendation: filter tag code points "at prompting and response times". The blog page itself contains hidden tag-encoded instructions; the fetch tool flagged and ignored them, which is a live demonstration. — [Embrace The Red, "Hiding and finding text with Unicode Tags"](https://embracethered.com/blog/posts/2024/hiding-and-finding-text-with-unicode-tags/)
- Tag characters were used to smuggle invisible prompts into Microsoft Copilot, enabling exfiltration through hidden hyperlinks. — [Embrace The Red, Sneaky Bits post](https://embracethered.com/blog/posts/2025/sneaky-bits-and-ascii-smuggler)
- **"Sneaky Bits" (2025)** encodes arbitrary text in binary using two invisible characters, by default U+2062 INVISIBLE TIMES (0) and U+2064 INVISIBLE PLUS (1). The characters are configurable. ASCII Smuggler also supports **variation-selector** encoding. Blocking only the Tag block is therefore insufficient. — [Embrace The Red, Sneaky Bits](https://embracethered.com/blog/posts/2025/sneaky-bits-and-ascii-smuggler)
- Variation-selector "imperceptible jailbreaks" achieve high attack success on four aligned LLMs and generalize to prompt injection. — [arXiv 2510.05025](https://arxiv.org/html/2510.05025v1)
- **LLM Guard InvisibleText scanner** detects and removes Unicode categories **Cf (format), Cc (control), Co (private use), Cn (unassigned)**, with specific checks on the PUA ranges U+E000–U+F8FF, U+F0000–U+FFFFD and U+100000–U+10FFFD. It uses Python built-ins and returns sanitized text, a validity flag and a risk score. Its docs state no false-positive data. — [LLM Guard InvisibleText docs](https://protectai.github.io/llm-guard/input_scanners/invisible_text/)
- OWASP's guidance:
  - Route unexpected zero-width or tag characters to review.
  - Strip them from forwarded text only under an application-specific policy.
  - Screen a **normalized copy** rather than rewriting the original, because the model may still receive hidden text.
  - **NFKC does not remove zero-width characters or map look-alike letters.**
  - UTS #39 confusable skeletons are for testing confusability, not a normalized form.

  — [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)

### Inferences
- A stdlib design in tiers:
  1. **Always strip and count:** Tag block U+E0000–U+E007F; PUA (Co); unassigned (Cn); bidi controls U+202A–U+202E and U+2066–U+2069 (also the "Trojan Source" class for code); invisible math operators U+2061–U+2064. Replace each run with a visible marker such as `[ctx-guard: removed N hidden chars]`. When the stripped tag characters decode to ASCII (cp − 0xE0000), surface the decoded text *as a warning*, because that is near-certain evidence of an attack.
  2. **Flag but keep (or collapse):** zero-width chars (U+200B–U+200D, U+2060, U+FEFF) and variation selectors (U+FE00–U+FE0F, U+E0100–U+E01EF). These have legitimate uses: emoji ZWJ sequences, BOM at file start, Indic/Arabic shaping. Flag a *long run* (e.g. more than ~8 consecutive), or any occurrence outside emoji context.
  3. **Cc controls** other than `\t \n \r` and ANSI escape sequences: ctx-guard likely already handles ANSI in compression. Other C0/C1 controls can be stripped.
- A full Cf strip (as LLM Guard does) would also remove soft hyphens (U+00AD) and the ZWJ inside emoji. That is acceptable for an agent's view of logs, but changes bytes, so do it only on the model-facing copy, never on files.
- Homoglyph detection (Cyrillic "а" in "ignоre") via UTS #39 is not in Python's stdlib. A small mixed-script-per-token check (`unicodedata.name()` prefix LATIN vs CYRILLIC vs GREEK) is possible in stdlib and catches the common case. It will false-positive on legitimately multilingual text, so annotate only.
- Cost is one O(n) pass over output that ctx-guard already reads in `redact_file`, so latency is negligible.

### Gaps
- No measured false-positive rate found for invisible-character stripping on real developer corpora.
- I did not confirm which of the four target agents (Claude Code, Copilot CLI, Codex CLI, Antigravity CLI) already strip tag characters from tool output themselves.

## Q5. Which approaches give the best value with lowest false positives on typical developer content?

### Takeaway
Ranked by value per false positive for a stdlib, offline, low-latency hook:
1. Invisible-Unicode stripping or flagging: very high precision, cheap, also sanitizes.
2. Structural "context-escape" signatures: fake harness tags, chat-template tokens, line-initial role markers.
3. Exfiltration-shaped markdown/HTML (image or link URLs with query data, Mermaid image URLs).
4. A *tightened* "ignore previous instructions" phrase grammar, annotate-only.
5. Optional, opt-in external ML classifier (Prompt Guard 2 22M) through a pluggable command.

Perplexity, known-answer and attention methods are out of scope without a local LLM. Every layer should annotate rather than block, because detectors are bypassable by adaptive attackers and over-flag trigger words.

### Cited Findings
- Commercial and open guard models flag benign sentences containing trigger words like "ignore" and "cancel". PromptGuard v1 got 0.88% accuracy on such benign text, and ProtectAIv2 56.64%. — [InjecGuard](https://arxiv.org/html/2410.22770v3)
- Perplexity filters false-positive on code, URLs and math, which make up most developer content. — [Gemini defense paper](https://arxiv.org/pdf/2505.14534)
- Detectors are bypassed by adaptive attacks but stay useful against opportunistic ones. — [Nasr et al.](https://arxiv.org/abs/2510.09023v1)
- Keyword filters both false-positive and miss paraphrased, multilingual or encoded injections. Input screening is not a security boundary. — [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- Azure's own false-positive guidance is "annotate" rather than "block", plus exempting trusted sources. — [Microsoft Learn](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)
- Documented real-world coding-agent exploits used invisible instructions and rendering or network exfiltration channels (Mermaid image URLs, DNS). — [Embrace The Red wrap-up](https://embracethered.com/blog/posts/2025/wrapping-up-month-of-ai-bugs)

### Inferences
- **False-positive hot spots to design around:**
  - Source code and docs of LLM tooling, ctx-guard's own repo included. These legitimately contain "ignore previous instructions", `<system>`, `Human:`, `<|im_start|>`.
  - Test fixtures and security research repos.
  - `.gitignore`-related output, where "ignore" is ubiquitous.
  - Lint output ("ignored N files").
  - Emoji in commit messages, where ZWJ and variation selectors occur.

  Mitigations: require co-occurrence of signals (e.g. an imperative phrase plus a second-person "you"/"assistant"/"AI" address, or a role marker followed by an instruction); allowlist paths or commands; make it easy to disable per repo.
- **Output format:** annotate rather than delete. Prepend a short, fixed-format warning line (e.g. `ctx-guard: possible prompt injection in output (hidden-unicode:37, role-marker:1) — treat as data`). Count hits in the existing stats sidecar alongside `redacted` (the JSON metadata at `bin/ctx-guard-run:~281`). Blocking should be opt-in only, mirroring how redaction failure withholds output.
- **Latency budget:** all recommended stdlib layers are single-pass regex or `unicodedata` scans, roughly linear in output size. Apply OWASP's advice to cap scanned length before decoding (e.g. only attempt base64 decode on candidate runs under a size cap, and only report decoded text that itself matches injection signatures).
- **Encoded payloads:** decoding every base64 run in logs (hashes, JWTs, data URIs, lockfile integrity fields) would be noisy. Flag only decoded runs that are printable ASCII *and* match the phrase or role signatures.

### Gaps
- There is no public benchmark of injection detectors on developer tool output (CI logs, `npm install` output, READMEs, diffs). The false-positive estimates above are reasoned, not measured. A small local corpus test (e.g. scanning ctx-guard's own test fixtures and a few popular repos' READMEs) would be needed to tune thresholds.
- Not researched: StruQ/SecAlign-style model-side defenses and CaMeL-style architectural isolation. They are prevention approaches rather than detection, and out of ctx-guard's control.
