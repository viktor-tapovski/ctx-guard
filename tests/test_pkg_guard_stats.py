import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, "hooks", "pre_bash_rewrite.py")
STATS_BIN = os.path.join(REPO_ROOT, "bin", "ctx-guard-stats")

# Runs the real hook main() with registry_lookup stubbed so nothing touches
# the network: only "requests" exists on the fake registry.
HOOK_RUNNER = """
import runpy, sys
sys.path.insert(0, sys.argv[1] + "/hooks")
import pkg_install_check as pic
pic.registry_lookup = lambda eco, name: pic.RegistryInfo(exists=name == "requests", age_days=900.0)
sys.argv = [sys.argv[1] + "/hooks/pre_bash_rewrite.py"]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class TestPkgGuardStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.stats = os.path.join(self._tmp.name, "stats.jsonl")
        self.env = {**os.environ, "HOME": self._tmp.name, "CTX_GUARD_STATS_FILE": self.stats,
                    "CTX_GUARD_STATE_DIR": self._tmp.name, "CTX_GUARD_PKG_CHECK": "1",
                    "CTX_GUARD_AGENT": "claude-code"}

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, command):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        out = subprocess.run([sys.executable, "-c", HOOK_RUNNER, REPO_ROOT], input=payload,
                             capture_output=True, text=True, env=self.env, cwd=self._tmp.name)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout) if out.stdout.strip() else {}

    def stats_json(self):
        out = subprocess.run([sys.executable, STATS_BIN, "--stats-file", self.stats, "--json"],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_blocked_install_shows_in_stats_json(self):
        decision = self.run_hook("pip install --index-url https://ci:s3cr3t@pypi.example.com/simple reqeusts")
        hso = decision["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        reason = hso["permissionDecisionReason"]
        self.assertTrue(reason.startswith(
            "ctx-guard blocked pip install reqeusts: no such package on registry (did you mean requests?). "
            "Allowlist:"), reason)
        self.assertNotIn("\n", reason)

        pg = self.stats_json()["pkg_guard"]
        self.assertEqual((pg["checked"], pg["blocked"], pg["warned"]), (1, 1, 0))
        self.assertEqual(pg["top_reasons"][0], {"reason": "nonexistent", "count": 1})
        self.assertEqual(pg["by_ecosystem"]["pip"]["blocked"], 1)

        with open(self.stats) as f:
            raw = f.read()
        self.assertNotIn("s3cr3t", raw)
        self.assertNotIn("index-url", raw)
        event = next(json.loads(l) for l in raw.splitlines() if '"pkg_guard"' in l)
        self.assertEqual((event["ecosystem"], event["decision"], event["reason_code"], event["package"]),
                         ("pip", "block", "nonexistent", "reqeusts"))

    def test_url_install_credentials_never_logged(self):
        self.run_hook("pip install git+https://user:s3cr3t@github.com/org/repo.git")
        with open(self.stats) as f:
            raw = f.read()
        self.assertNotIn("s3cr3t", raw)
        self.assertIn("git+https://github.com/org/repo.git", raw)

    def test_allowed_install_counts_as_checked(self):
        self.assertEqual(self.run_hook("pip install requests==2.32.3").get("hookSpecificOutput", {})
                         .get("permissionDecision"), None)
        pg = self.stats_json()["pkg_guard"]
        self.assertEqual((pg["checked"], pg["allowed"], pg["blocked"], pg["warned"]), (1, 1, 0, 0))

    def test_legacy_events_still_counted(self):
        with open(self.stats, "w") as f:
            f.write(json.dumps({"ts": 1.0, "kind": "pkg_blocked", "agent": "x", "reason": "old"}) + "\n")
            f.write(json.dumps({"ts": 2.0, "kind": "pkg_warned", "agent": "x", "reason": "old"}) + "\n")
        summary = self.stats_json()
        self.assertEqual((summary["pkg_blocked_count"], summary["pkg_warned_count"]), (1, 1))
        self.assertEqual(summary["pkg_guard"]["checked"], 2)


if __name__ == "__main__":
    unittest.main()
