import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_BIN = os.path.join(REPO_ROOT, "bin", "ctx-guard-run")
RECALL_BIN = os.path.join(REPO_ROOT, "bin", "ctx-guard-recall")


class TestRecall(unittest.TestCase):
    """ctx-guard-recall against logs archived by the real ctx-guard-run, all
    under a throwaway dir (never the real /tmp/ctx-guard-<uid>/logs)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.log_dir = os.path.join(root, "logs")
        os.makedirs(os.path.join(root, "tmp"))
        self.env = {**os.environ, "HOME": root, "TMPDIR": os.path.join(root, "tmp"),
                    "CTX_GUARD_LOG_DIR": self.log_dir,
                    "CTX_GUARD_STATE_DIR": os.path.join(root, "state"),
                    "CTX_GUARD_STATS_FILE": os.path.join(root, "stats.jsonl"),
                    "CTX_GUARD_MAX_LINES": "150", "CTX_GUARD_REDACT_LOGS": "1"}

    def tearDown(self):
        self._tmp.cleanup()

    def run_wrapped(self, body):
        script = os.path.join(self._tmp.name, "cmd.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n" + body + "\n")
        return subprocess.run(["bash", RUN_BIN, script], capture_output=True, text=True, env=self.env)

    def recall(self, *args):
        return subprocess.run([sys.executable, RECALL_BIN, *args], capture_output=True,
                              text=True, env=self.env)

    def failing_suite(self):
        """A long failing 'test run': 400 passes, then 300 FAIL lines, exit 1."""
        return self.run_wrapped(
            'for i in $(seq 1 400); do echo "test_ok_$i passed"; done\n'
            'for i in $(seq 1 300); do echo "FAIL: test_bad_$i"; done\n'
            'exit 1')

    def test_hint_names_recall_and_run_id(self):
        out = self.failing_suite()
        self.assertEqual(out.returncode, 1)
        last = out.stdout.strip().splitlines()[-1]
        self.assertIn("Full log:", last)
        self.assertIn("ctx-guard-recall show run-", last)
        self.assertIn("--grep <pattern>", last)

    def test_show_last_grep_returns_failures_within_cap(self):
        self.failing_suite()
        out = self.recall("show", "last", "--grep", "FAIL")
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        body, notice = lines[:-1], lines[-1]
        self.assertEqual(len(body), 200)
        self.assertTrue(all("FAIL: test_bad_" in line for line in body))
        self.assertNotIn("passed", out.stdout)
        self.assertIn("TRUNCATED at 200 of 300", notice)

    def test_show_lines_and_context(self):
        self.failing_suite()
        out = self.recall("show", "last", "--lines", "399:402")
        self.assertEqual(out.stdout.splitlines()[:4], [
            "399: test_ok_399 passed", "400: test_ok_400 passed",
            "401: FAIL: test_bad_1", "402: FAIL: test_bad_2"])
        self.assertIn("not truncated", out.stdout.splitlines()[-1])
        out = self.recall("show", "last", "--grep", "test_bad_1$", "--context", "1")
        self.assertEqual(out.stdout.splitlines()[:3], [
            "400: test_ok_400 passed", "401: FAIL: test_bad_1", "402: FAIL: test_bad_2"])

    def test_show_by_run_id_with_and_without_prefix(self):
        self.run_wrapped("echo alpha")
        run_id = [n for n in os.listdir(self.log_dir) if not n.endswith(".meta")][0]
        for ident in (run_id, run_id[len("run-"):]):
            out = self.recall("show", ident)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("1: alpha", out.stdout)

    def test_list_shows_exit_code_and_redacted_command(self):
        self.run_wrapped('echo "API_KEY=list-test-not-a-real-key"; exit 3')
        out = self.recall("list")
        self.assertEqual(out.returncode, 0, out.stderr)
        row = out.stdout.splitlines()[0]
        self.assertIn("exit=3", row)
        self.assertIn("API_KEY=[REDACTED]", row)
        self.assertNotIn("list-test-not-a-real-key", out.stdout)
        self.assertIn("1 of 1 runs shown", out.stdout.splitlines()[-1])

    def test_search_caps_matches(self):
        self.failing_suite()
        self.run_wrapped('echo "FAIL: one more"')
        out = self.recall("search", "FAIL")
        lines = out.stdout.splitlines()
        self.assertEqual(len(lines), 51)
        self.assertRegex(lines[0], r"^run-[A-Za-z0-9]+:\d+: FAIL: ")
        self.assertIn("TRUNCATED at 50 matches", lines[-1])
        out = self.recall("search", "one more", "--since", "1h")
        self.assertRegex(out.stdout.splitlines()[0], r"^run-[A-Za-z0-9]+:1: FAIL: one more$")
        self.assertEqual(self.recall("search", "no-such-text-anywhere").returncode, 1)

    def test_refuses_paths_outside_log_dir(self):
        self.run_wrapped("echo alpha")
        outside = os.path.join(self._tmp.name, "run-outside")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("outside secret\n")
        os.symlink(outside, os.path.join(self.log_dir, "run-link"))
        with open(os.path.join(self.log_dir, "raw-AAAAAAAA"), "w", encoding="utf-8") as f:
            f.write("raw secret\n")
        for ident in ("../../etc/passwd", "run-../../etc/passwd", "/etc/passwd",
                      "..", "run-link", "raw-AAAAAAAA", outside):
            out = self.recall("show", ident)
            self.assertEqual(out.returncode, 2, ident)
            self.assertEqual(out.stdout, "", ident)
        self.assertIn("outside the log directory", self.recall("show", "../../etc/passwd").stderr)
        self.assertNotIn("secret", self.recall("search", "secret").stdout.split("===")[0])


if __name__ == "__main__":
    unittest.main()
