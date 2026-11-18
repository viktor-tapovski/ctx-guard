import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks", "lib"))

import agent_hooks  # noqa: E402

# auto_delegate.py imports nothing outside the standard library.
HOOKS = ["pre_bash_rewrite.py", "pre_tool_bound.py", "context_monitor.py"]
PAYLOAD = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "t",
                      "tool_input": {"command": "make"}})


class TestHookFailSafe(unittest.TestCase):
    """A hook that crashes before main()'s own handler, such as on a broken
    import, must still exit 0 with no output: Copilot CLI denies the tool
    call on a non-zero exit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.env = {**os.environ, "HOME": self.root, "TMPDIR": self.root,
                    "CTX_GUARD_STATE_DIR": os.path.join(self.root, "state"),
                    "CTX_GUARD_LOG_DIR": os.path.join(self.root, "logs"),
                    "CTX_GUARD_STATS_FILE": os.path.join(self.root, "stats.jsonl"),
                    "CTX_GUARD_SPOOL_DIRS": ""}

    def tearDown(self):
        self._tmp.cleanup()

    def broken_install(self):
        dest = os.path.join(self.root, "install")
        shutil.copytree(REPO_ROOT, dest, ignore=shutil.ignore_patterns(".git", "tests"))
        with open(os.path.join(dest, "hooks", "lib", "ctx_guard_common.py"), "w") as f:
            f.write('raise ImportError("broken install")\n')
        return dest

    def run_hook(self, path, env=None):
        return subprocess.run([sys.executable, path], input=PAYLOAD, capture_output=True,
                              text=True, env=env or self.env, timeout=30)

    def test_broken_import_exits_zero_with_no_output(self):
        dest = self.broken_install()
        for hook in HOOKS:
            with self.subTest(hook=hook):
                result = self.run_hook(os.path.join(dest, "hooks", hook))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("[ctx-guard] hook failed: broken install", result.stderr)

    def test_broken_import_still_allows_under_antigravity(self):
        dest = self.broken_install()
        command = agent_hooks.antigravity_command(dest, sys.executable)
        result = subprocess.run(["sh", "-c", command], input=PAYLOAD, capture_output=True,
                                text=True, env={**self.env, "CTX_GUARD_AGENT": "antigravity-cli"},
                                timeout=30)
        self.assertEqual(json.loads(result.stdout), {"decision": "allow"})

    def test_invalid_window_setting_falls_back_to_default(self):
        hook = os.path.join(REPO_ROOT, "hooks", "context_monitor.py")
        for name in ("CTX_GUARD_WINDOW", "CTX_GUARD_COPILOT_WINDOW_TOKENS"):
            for value in ("abc", "0"):
                with self.subTest(name=name, value=value):
                    result = self.run_hook(hook, {**self.env, name: value})
                    self.assertEqual(result.returncode, 0)
                    self.assertNotIn("failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
