# T12: SessionStart posture check

**Depends on:** none (T07 adds a taint-clear call into this hook later) · **Phase:** 5 · **Size:** S

## Background

Nothing ctx-guard does in-process matters as much as the host's own sandbox
and approval settings. In HarnessSecurity-Bench, auto-approve raised attack
success from 29.2% to 95.6%. Anthropic: "effective sandboxing requires both
filesystem and network isolation." Only an OS-level egress sandbox closes the
gaps the sink gate can't, such as `python -c` with urllib and DNS
exfiltration.

## Steps

1. Add `hooks/session_start.py`, a SessionStart hook for Claude Code and any
   other agent that supports one. Check each agent's docs first, and don't
   register where the event doesn't exist.
2. Detect, from config files only (no subprocess calls to the agent):
   - **Claude Code:** sandbox enabled in settings; `permissions.defaultMode` is
     `bypassPermissions` or similar; broad `Bash(*)` allow rules.
   - **Codex:** `sandbox_mode` / `approval_policy` in `~/.codex/config.toml`.
     Use `tomllib` (stdlib on 3.11+); skip quietly on older Python.
   - **Copilot:** `--allow-all-tools` / autopilot, where detectable.
3. Emit **one** factual `additionalContext` or `systemMessage` per session
   when a risky posture is found, e.g. `ctx-guard: no OS sandbox detected;
   prompt-injection gates are best-effort without one.` Use the existing
   `.fired` state for once-per-session behaviour.
4. Expose a function the T07 taint-clear call can hook into, for `source in
   {startup, clear}`.
5. Register the hook in `install.sh`, and make sure uninstall removes it
   exactly.

## Acceptance criteria

- Fixture-based tests for each detected setting.
- Fires at most once per session; fails open; latency under 20 ms.
- Install and uninstall round trip is clean, and CI passes.
