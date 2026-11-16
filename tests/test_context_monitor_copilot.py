import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITOR = os.path.join(REPO_ROOT, "hooks", "context_monitor.py")


class TestCopilotContextWarnings(unittest.TestCase):
    """context_monitor.py replayed with Copilot CLI PascalCase PostToolUse
    payloads, which carry no transcript_path: usage is estimated from the
    session's cumulative tool output."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.env = {**os.environ, "HOME": self.root, "TMPDIR": self.root,
                    "CTX_GUARD_AGENT": "copilot-cli",
                    "CTX_GUARD_STATE_DIR": os.path.join(self.root, "state"),
                    "CTX_GUARD_STATS_FILE": os.path.join(self.root, "stats.jsonl"),
                    "CTX_GUARD_SPOOL_DIRS": "",
                    # 1000 tokens = 4000 bytes of tool output.
                    "CTX_GUARD_COPILOT_WINDOW_TOKENS": "1000"}

    def tearDown(self):
        self._tmp.cleanup()

    def post(self, session="sess-copilot1", size=250, **env):
        payload = {"hook_event_name": "PostToolUse", "session_id": session,
                   "timestamp": "2026-11-16T12:00:00.000Z", "cwd": self.root,
                   "tool_name": "Bash", "tool_input": {"command": "ls"},
                   "tool_result": {"result_type": "success",
                                   "text_result_for_llm": "x" * size}}
        proc = subprocess.run([sys.executable, MONITOR], input=json.dumps(payload),
                              capture_output=True, text=True, env={**self.env, **env})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def test_replayed_session_fires_each_threshold_once(self):
        fired = {}
        for call in range(1, 21):  # 250 bytes per call, 5000 bytes in all
            out = self.post()
            if out:
                fired[call] = out["hookSpecificOutput"]["additionalContext"]
                self.assertEqual(out["additionalContext"], fired[call])
        # 50% is crossed at 2000 bytes (call 8), 70% at 3000 (call 12, 75%),
        # 85% at 3500 (call 14, 87%).
        self.assertEqual(sorted(fired), [8, 12, 14])
        self.assertIn("~50% estimated from tool output", fired[8])
        self.assertIn("explore or task agents", fired[8])
        self.assertIn("~75% estimated", fired[12])
        self.assertIn("/compact", fired[12])
        self.assertIn("~87% estimated", fired[14])

    def test_sessions_are_counted_separately(self):
        for _ in range(7):
            self.assertIsNone(self.post(session="sess-a"))
        self.assertIsNone(self.post(session="sess-b"))
        self.assertIsNotNone(self.post(session="sess-a"))

    def test_no_session_id_or_other_agent_never_fires(self):
        self.assertIsNone(self.post(session="", size=10000))
        self.assertIsNone(self.post(session="sess-claude", size=10000,
                                    CTX_GUARD_AGENT="claude-code"))


if __name__ == "__main__":
    unittest.main()
