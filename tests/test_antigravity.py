"""
Antigravity CLI (agy) support: the preToolUse hook run on agy-shaped
payloads, and install/uninstall of the named hook in ~/.gemini/config/hooks.json.

Every command runs through both the Claude-shaped and the agy-shaped
payload, and the agy decision must match the Claude one after translation.
Payloads follow tests/fixtures/antigravity/, built from the official hooks
docs (no live agy capture yet). The package registry is stubbed.
"""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks"))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks", "lib"))

import agent_hooks  # noqa: E402
import ctx_guard_common  # noqa: E402
import pkg_install_check  # noqa: E402
import pre_bash_rewrite  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "antigravity")
AGY = next(t for t in agent_hooks.TARGETS if t.agent == "antigravity-cli")
REGISTRY = {"npm": {"react": 4000, "lodash": 4000}, "pip": {"requests": 4000, "reqeusts": 400}}

# Rewrites, wraps, a block, package-guard outcomes, and pass-throughs.
COMMANDS = [
    "git status",
    "git log",
    "git log --stat",
    "cat README.md",
    "grep -r TODO .",
    "find . -name '*.py'",
    "npm test",
    "make && make test",
    "env",
    "printenv HOME",
    "npm install react",
    "npm install definitely-not-a-real-package-xyz",
    "pip install reqeusts",
    "echo hello",
    "ls",
]


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def registry_stub(ecosystem, name):
    age = REGISTRY.get(ecosystem, {}).get(name)
    if age is None:
        return pkg_install_check.RegistryInfo(exists=False)
    return pkg_install_check.RegistryInfo(exists=True, age_days=float(age))


class HookTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.stats_file = os.path.join(self.tmp, "stats.jsonl")
        for patcher in (
            mock.patch.dict(os.environ, {"HOME": self.tmp, "CTX_GUARD_PKG_CHECK": "1"}),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("network access in test")),
            mock.patch.object(pkg_install_check, "registry_lookup", side_effect=registry_stub),
            mock.patch.object(ctx_guard_common, "STATS_FILE", self.stats_file),
            mock.patch.object(ctx_guard_common, "STATS_FILE_IS_DEFAULT", False),
            mock.patch.object(pre_bash_rewrite, "PENDING_DIR", os.path.join(self.tmp, "pending")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_hook(self, payload, agent, main=None):
        """Run the preToolUse hook in-process; return its stdout text."""
        out = io.StringIO()
        with mock.patch.object(ctx_guard_common, "AGENT", agent), \
                mock.patch.object(pre_bash_rewrite, "AGENT", agent), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                redirect_stdout(out):
            pre_bash_rewrite.run_pretooluse_hook(main or pre_bash_rewrite.main)
        return out.getvalue()

    def agy_payload(self, command):
        payload = load_fixture("pre_tool_use_run_command.json")
        payload["toolCall"]["args"]["CommandLine"] = command
        return payload

    def claude_payload(self, command):
        return {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": command}}


class RewriteParityTest(HookTestCase):
    def test_every_command_matches_claude_decision(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                claude_text = self.run_hook(self.claude_payload(command), "claude-code")
                agy = json.loads(self.run_hook(self.agy_payload(command), "antigravity-cli"))
                claude = json.loads(claude_text) if claude_text.strip() else {}
                spec = claude.get("hookSpecificOutput", {})

                self.assertEqual(agy["decision"], spec.get("permissionDecision") or "allow")
                if "permissionDecision" in spec:
                    self.assertEqual(agy.get("reason"), spec["permissionDecisionReason"])
                if "updatedInput" in spec:
                    # Wrapped commands name the agent in ctx-guard-run's --filter tag.
                    expected = spec["updatedInput"]["command"].replace(
                        "--filter claude-code", "--filter antigravity-cli")
                    self.assertEqual(agy["overwrite"], {"CommandLine": expected})
                else:
                    self.assertNotIn("overwrite", agy)

    def test_rewrite_only_overwrites_the_command(self):
        agy = json.loads(self.run_hook(self.agy_payload("git status"), "antigravity-cli"))
        self.assertEqual(agy["overwrite"], {"CommandLine": "git status --porcelain=v1 -b"})
        self.assertEqual(agy["decision"], "allow")

    def test_nonexistent_package_is_denied(self):
        command = "npm install definitely-not-a-real-package-xyz"
        agy = json.loads(self.run_hook(self.agy_payload(command), "antigravity-cli"))
        self.assertEqual(agy["decision"], "deny")
        self.assertIn("definitely-not-a-real-package-xyz", agy["reason"])

    def test_typosquat_asks(self):
        agy = json.loads(self.run_hook(self.agy_payload("pip install reqeusts"), "antigravity-cli"))
        self.assertEqual(agy["decision"], "ask")

    def test_stats_are_tagged_with_the_agent(self):
        self.run_hook(self.agy_payload("git log"), "antigravity-cli")
        with open(self.stats_file, encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([(e["kind"], e["agent"], e["rule"]) for e in events],
                         [("rewrite", "antigravity-cli", "git-log")])


class FailSafeTest(HookTestCase):
    """agy blocks a tool call when a preToolUse hook prints no decision."""

    def test_pass_through_prints_explicit_allow(self):
        self.assertEqual(json.loads(self.run_hook(self.agy_payload("echo hello"), "antigravity-cli")),
                         {"decision": "allow"})
        self.assertEqual(self.run_hook(self.claude_payload("echo hello"), "claude-code"), "")

    def test_non_shell_tool_prints_explicit_allow(self):
        out = self.run_hook(load_fixture("pre_tool_use_view_file.json"), "antigravity-cli")
        self.assertEqual(json.loads(out), {"decision": "allow"})

    def test_internal_error_allows_the_original_command(self):
        with mock.patch.object(pre_bash_rewrite, "transform", side_effect=RuntimeError("boom")):
            out = self.run_hook(self.agy_payload("git status"), "antigravity-cli")
        self.assertEqual(json.loads(out), {"decision": "allow"})

    def test_crash_outside_main_allows_the_original_command(self):
        def crash():
            print("partial", end="")
            raise RuntimeError("boom")
        for agent, expected in (("antigravity-cli", '{"decision": "allow"}\n'), ("claude-code", "partial")):
            with self.subTest(agent=agent):
                self.assertEqual(self.run_hook(self.agy_payload("ls"), agent, main=crash), expected)

    def test_malformed_payload_allows(self):
        for payload in ({}, {"toolCall": None}, {"toolCall": {"name": "run_command", "args": "oops"}}):
            with self.subTest(payload=payload):
                self.assertEqual(json.loads(self.run_hook(payload, "antigravity-cli")), {"decision": "allow"})


class NormalizeTest(unittest.TestCase):
    def test_tool_call_maps_to_claude_names(self):
        name, args = ctx_guard_common.normalize_tool_call(load_fixture("pre_tool_use_run_command.json"))
        self.assertEqual(name, "Bash")
        self.assertEqual(args["command"], "npm test")
        self.assertEqual(args["Cwd"], "/workspace/project")

    def test_conversation_id_is_the_session(self):
        payload = load_fixture("pre_tool_use_run_command.json")
        self.assertEqual(ctx_guard_common.safe_session_id(payload), "ec33ebf9-0cba-4100-8142-c61503f6c587")


class InstallTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.path = os.path.join(self.home, ".gemini", "config", "hooks.json")
        os.makedirs(os.path.join(self.home, ".gemini", "antigravity-cli"))
        patcher = mock.patch("shutil.which", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manifest = {}

    def install(self):
        with redirect_stdout(io.StringIO()):
            agent_hooks.install_target(AGY, self.manifest, self.home, "/opt/ctx guard", "/usr/bin/python3")

    def restore(self):
        with redirect_stdout(io.StringIO()):
            agent_hooks.restore_target(AGY, self.manifest)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def write_existing(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "wb") as f:
            f.write(data)

    def test_install_registers_named_hook(self):
        self.install()
        config = json.loads(self.read())
        group = config["ctx-guard"]["PreToolUse"][0]
        self.assertEqual(group["matcher"], "run_command")
        self.assertEqual(group["hooks"][0]["command"],
                         agent_hooks.antigravity_command("/opt/ctx guard", "/usr/bin/python3"))
        self.assertIn("CTX_GUARD_AGENT=antigravity-cli /usr/bin/python3 '/opt/ctx guard/hooks/pre_bash_rewrite.py'",
                      group["hooks"][0]["command"])
        self.assertEqual(group["hooks"][0]["timeout"], 15)
        self.assertLess(ctx_guard_common.ANTIGRAVITY_DEADLINE_S, 15)

    def run_command(self, python_bin):
        command = agent_hooks.antigravity_command(REPO_ROOT, python_bin)
        payload = json.dumps(load_fixture("pre_tool_use_run_command.json"))
        # The real hook runs here, so keep its stats and logs out of the user's.
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "CTX_GUARD_STATE_DIR": tmp, "CTX_GUARD_LOG_DIR": tmp,
                   "CTX_GUARD_STATS_FILE": os.path.join(tmp, "stats.jsonl")}
            return subprocess.run(["sh", "-c", command], input=payload, capture_output=True,
                                  text=True, env=env, timeout=30).stdout

    def test_command_allows_when_python_cannot_run(self):
        self.assertEqual(json.loads(self.run_command("/nonexistent/python3")), {"decision": "allow"})
        self.assertIn("decision", json.loads(self.run_command(sys.executable)))

    def test_slow_hook_allows_before_the_agy_timeout(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); import ctx_guard_common as c; "
                "c.ANTIGRAVITY_DEADLINE_S = 1; c.run_pretooluse_hook(lambda: time.sleep(20))")
        env = {**os.environ, "CTX_GUARD_AGENT": "antigravity-cli"}
        out = subprocess.run([sys.executable, "-c", code, os.path.join(REPO_ROOT, "hooks", "lib")],
                             env=env, capture_output=True, text=True, timeout=10).stdout
        self.assertEqual(json.loads(out), {"decision": "allow"})

    def test_existing_file_restored_byte_identical(self):
        original = b'{ "other-hook": {"Stop": [{"command": "./x.sh"}]} }'  # odd spacing, no newline
        self.write_existing(original)
        self.install()
        self.install()  # idempotent: a reinstall keeps the true original
        config = json.loads(self.read())
        self.assertEqual(set(config), {"other-hook", "ctx-guard"})
        self.restore()
        self.assertEqual(self.read(), original)

    def test_new_file_and_dir_removed(self):
        self.install()
        self.restore()
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))

    def test_user_edits_after_install_are_kept(self):
        self.write_existing(b'{"other-hook": {}}\n')
        self.install()
        config = json.loads(self.read())
        config["added-later"] = {"enabled": False}
        self.write_existing(json.dumps(config).encode())
        self.restore()
        self.assertEqual(json.loads(self.read()), {"other-hook": {}, "added-later": {"enabled": False}})

    def test_invalid_json_left_alone(self):
        self.write_existing(b"{not json")
        self.install()
        self.assertEqual(self.read(), b"{not json")
        self.assertNotIn("antigravity-cli", self.manifest.get("hook_files", {}))

    def test_skipped_when_agy_not_installed(self):
        os.rmdir(os.path.join(self.home, ".gemini", "antigravity-cli"))
        self.install()
        self.assertFalse(os.path.exists(self.path))
        self.assertNotIn("antigravity-cli", self.manifest.get("hook_files", {}))


if __name__ == "__main__":
    unittest.main()
