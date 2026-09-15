#!/usr/bin/env python3
"""
ctx-guard UserPromptSubmit hook: detects exploration/research-style prompts
and injects a delegation directive so Claude routes the heavy reading to a
subagent instead of polluting the main context.
"""
import json
import re
import sys

PATTERNS = [
    (re.compile(r"\b(investigate|explore|research|understand how|trace|"
                r"find (out|where|all)|look into|audit|map out)\b", re.I),
     "researcher"),
    (re.compile(r"\b(why (is|are|does).*(fail|break|error)|(test|tests|build) .*fail|"
                r"failing|flaky|stack ?trace|root cause|debug)", re.I),
     "log-triager"),
    (re.compile(r"\b(where is .* (defined|used|called)|search the (code|repo)|"
                r"which files|list all (usages|references|callers))\b", re.I),
     "code-searcher"),
]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        prompt = payload.get("prompt", "")
        if len(prompt) < 15:
            return
        for pat, agent in PATTERNS:
            if pat.search(prompt):
                msg = (
                    f"[ctx-guard] This looks like an exploration task. Delegate it to "
                    f"the '{agent}' subagent so file reads and command output stay out "
                    f"of the main context. Have it return only: findings, exact file "
                    f"paths with line numbers, and a recommended next step. Do not "
                    f"read the underlying files yourself unless you then need to edit them."
                )
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": msg,
                    }
                }))
                return
    except Exception as e:
        print(f"[ctx-guard] auto_delegate failed: {e}", file=sys.stderr)
        return


if __name__ == "__main__":
    main()
