#!/usr/bin/env python3
"""
ctx-guard PreToolUse hook for the Bash tool. Works unmodified under Claude
Code and GitHub Copilot CLI (see lib/ctx_guard_common.py for the payload/
output compatibility shim).

Two layers of protection:
1. Known verbose commands (git, grep, find, test runners) are rewritten
   to token-efficient equivalents.
2. Everything else is wrapped in ctx-guard-run, which caps output lines
   and archives the full log to disk.

Reads the hook payload from stdin, emits JSON with the rewrite decision on
stdout. Fails open on Claude Code (original command runs untouched) but
note: Copilot CLI's command preToolUse hooks are FAIL-CLOSED on a crash or
non-zero exit -- so every code path here must reach the outer try/except
and exit 0, never letting an exception escape uncaught.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ctx_guard_common import (  # noqa: E402
    build_pretooluse_decision,
    normalize_tool_call,
    record_stats_event,
)
import pkg_install_check

CTX_GUARD_RUN = os.environ.get(
    "CTX_GUARD_RUN", os.path.expanduser("~/.ctx-guard/bin/ctx-guard-run")
)
SCRIPT_DIR = os.environ.get(
    "CTX_GUARD_SCRIPT_DIR", f"/tmp/ctx-guard-{os.getuid()}/scripts"
)

# Commands that are already cheap -- never touch these.
CHEAP = re.compile(
    r"^\s*(cd|pwd|echo|which|ls(\s|$)|mkdir|touch|rm|mv|cp|chmod|"
    r"head|tail|wc|true|date)"
)

# Environment enumeration can expose credentials and other secrets to the
# agent/model. Deny it instead of passing the output through unchanged.
SENSITIVE_ENV = re.compile(r"^\s*(env|printenv|set|export)(?:\s|$)")

# (name, pattern, replacement) rewrites for known token-burners.
REWRITES = [
    # git: compact-by-default forms
    ("git-status", re.compile(r"^git status\s*$"), "git status --porcelain=v1 -b"),
    ("git-log", re.compile(r"^git log\s*$"), "git log --oneline -n 20"),
    ("git-log-args", re.compile(r"^git log (?!.*(-n|--oneline|-\d))(.*)$"),
     r"git log --oneline -n 20 \2"),
    ("git-diff", re.compile(r"^git diff\s*$"),
     "git diff --stat && echo '--- (run: git diff -- <file> for details)'"),
    ("git-show", re.compile(r"^git show\s*([a-fA-F0-9]*)\s*$"), r"git show --stat \1"),
    ("git-branch-a", re.compile(r"^git branch -a\s*$"), "git branch -a | head -30"),
    # unbounded search / listing
    ("grep-unbounded", re.compile(r"^(grep|rg|ag)\b(?!.*\|\s*(head|tail|wc))(.*)$"),
     r"\1\3 | head -50"),
    ("find-unbounded", re.compile(r"^find\b(?!.*\|\s*(head|tail|wc))(.*)$"),
     r"find\2 | head -100"),
    ("cat-large", re.compile(r"^cat\s+([^|;&<>]+?)\s*$"),
     r"head -c 8000 \1; echo; echo '[ctx-guard: capped at 8KB; use sed -n X,Yp for ranges]'"),
    # container / infra tooling
    ("docker-logs", re.compile(r"^docker logs\b(?!.*\|\s*(head|tail|wc))(?!.*(-f|--follow))(.*)$"),
     r"docker logs\3 | tail -100"),
    ("kubectl-get", re.compile(r"^kubectl get\b(?!.*\|\s*(head|tail|wc))(.*)$"),
     r"kubectl get\2 | head -50"),
    ("kubectl-describe", re.compile(r"^kubectl describe\b(?!.*\|\s*(head|tail|wc))(.*)$"),
     r"kubectl describe\2 | head -80"),
    ("terraform-plan", re.compile(r"^terraform plan\b(?!.*\|\s*(head|tail|wc))(.*)$"),
     r"terraform plan\2 -no-color | tail -100"),
    ("pkg-list", re.compile(r"^(npm ls|pip list|pip3 list)\b(?!.*\|\s*(head|tail|wc))(.*)$"),
     r"\1\3 | head -50"),
]

# Test/build runners: pipe through an error filter, keep the tail.
RUNNERS = re.compile(
    r"^(npm (test|run)|npx |pnpm |yarn |pytest|python -m pytest|cargo (test|build)|"
    r"go (test|build)|mvn |gradle |make(\s|$)|docker (build|logs)|tsc(\s|$))"
)


def already_filtered(cmd: str) -> bool:
    return bool(re.search(r"\|\s*(head|tail|grep|rg|awk|sed|wc|ctx-guard)", cmd)) \
        or "ctx-guard-run" in cmd


def wrap_generic(cmd: str) -> str:
    """Write cmd to a temp script and run it through ctx-guard-run (safe quoting)."""
    os.makedirs(SCRIPT_DIR, mode=0o700, exist_ok=True)
    os.chmod(SCRIPT_DIR, 0o700)  # makedirs' mode is masked by umask; enforce explicitly
    fd, path = tempfile.mkstemp(suffix=".sh", dir=SCRIPT_DIR)
    with os.fdopen(fd, "w") as f:
        f.write("#!/usr/bin/env bash\n" + cmd + "\n")
    return f"{shlex.quote(CTX_GUARD_RUN)} {shlex.quote(path)}"


def transform(cmd: str) -> tuple[str | None, str | None, str | None] | None:
    """Return (rule_name, rewritten command, pkg_warning), or None to leave
    cmd untouched with no pkg_warning either."""
    stripped = cmd.strip()

    if SENSITIVE_ENV.match(stripped):
        return "environment-enumeration", None, None

    pkg_result = pkg_install_check.check_command(stripped, cwd=os.getcwd())
    if pkg_result is not None and pkg_result.action == "block":
        return "pkg-install-blocked", None, pkg_result.reason
    pkg_warning = pkg_result.reason if pkg_result is not None else None

    if already_filtered(stripped) or CHEAP.match(stripped):
        return (None, None, pkg_warning) if pkg_warning else None
    # Never rewrite compound commands with targeted rules; wrap them instead.
    compound = bool(re.search(r"[;&]|\|\|", stripped))

    if not compound:
        for name, pat, repl in REWRITES:
            if pat.match(stripped):
                return name, pat.sub(repl, stripped), pkg_warning

    if RUNNERS.match(stripped) or compound:
        if os.path.isfile(CTX_GUARD_RUN):
            rule = "compound-wrap" if compound else "runner-wrap"
            return rule, wrap_generic(cmd), pkg_warning
    return (None, None, pkg_warning) if pkg_warning else None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        # Only act on Bash tool calls (Unix shell semantics); PowerShell and
        # other tools pass through untouched -- these rewrite rules assume
        # bash pipes/quoting and would not translate correctly.
        tool_name, tool_args = normalize_tool_call(payload)
        if tool_name.lower() != "bash":
            return
        cmd = tool_args.get("command", "")
        if not cmd:
            return
        result = transform(cmd)
        if not result:
            return
        rule, new_cmd, pkg_warning = result

        if rule is None and new_cmd is None:
            if pkg_warning:
                record_stats_event("pkg_warned", tool_name=tool_name, reason=pkg_warning)
                decision = build_pretooluse_decision(
                    reason=f"ctx-guard-pkg: {pkg_warning}",
                )
                print(json.dumps(decision))
            return

        if new_cmd is None:
            record_stats_event("blocked", rule=rule, tool_name=tool_name)
            if rule == "pkg-install-blocked":
                record_stats_event("pkg_blocked", tool_name=tool_name, reason=pkg_warning)
                reason = f"ctx-guard-pkg: {pkg_warning}"
            else:
                reason = "ctx-guard: environment enumeration is blocked to prevent secret leakage"
            decision = build_pretooluse_decision(reason=reason, decision="deny")
            print(json.dumps(decision))
            return

        if new_cmd == cmd:
            if pkg_warning:
                record_stats_event("pkg_warned", tool_name=tool_name, reason=pkg_warning)
                decision = build_pretooluse_decision(
                    reason=f"ctx-guard-pkg: {pkg_warning}",
                )
                print(json.dumps(decision))
            return

        new_args = {**tool_args, "command": new_cmd}
        record_stats_event("rewrite", rule=rule, tool_name=tool_name)
        reason = f"ctx-guard: rewrote to token-efficient form ({rule})"
        if pkg_warning:
            record_stats_event("pkg_warned", tool_name=tool_name, reason=pkg_warning)
            reason += f" | ctx-guard-pkg: {pkg_warning}"
        decision = build_pretooluse_decision(reason=reason, new_args=new_args)
        print(json.dumps(decision))
    except Exception as e:
        # Fail open on Claude Code; on Copilot CLI a crash would deny the
        # tool call instead, which is exactly why we catch everything here
        # and always exit 0 with no output (falls through to normal execution).
        print(f"[ctx-guard] pre_bash_rewrite failed: {e}", file=sys.stderr)
        return


if __name__ == "__main__":
    main()
