"""
Codex CLI support: the preToolUse hook run on Codex payloads, and
install/uninstall of ctx-guard's entry in $CODEX_HOME/hooks.json.

Every command runs through both the Claude-shaped and the Codex payload.
Codex takes Claude's hookSpecificOutput but has no "ask", so a package
warning becomes a systemMessage with no decision and no rewrite. Codex takes
a rewrite only with "allow", which may skip its approval policy, so commands
ctx-guard would wrap in ctx-guard-run pass through untouched.
Payloads follow tests/fixtures/codex/, built from the official field tables
(no live Codex capture yet). The package registry is stubbed.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_antigravity import COMMANDS, REPO_ROOT, HookTestCase  # noqa: E402

import agent_hooks  # noqa: E402
import ctx_guard_common  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "codex")
CODEX = next(t for t in agent_hooks.TARGETS if t.agent == "codex-cli")
# Fields the Codex docs list for PreToolUse output; anything else is a hook error.
CODEX_OUTPUT = {"hookSpecificOutput", "systemMessage"}
CODEX_SPEC = {"hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput", "additionalContext"}


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class CodexHookTest(HookTestCase):
    def codex_payload(self, command, tool_name="Bash"):
        payload = load_fixture("pre_tool_use_bash.json")
        payload["tool_name"] = tool_name
        payload["tool_input"] = {"command": command}
        return payload

    def test_every_command_matches_claude_decision(self):
        for command in COMMANDS + ["pip install reqeusts && make"]:
            with self.subTest(command=command):
                claude_text = self.run_hook(self.claude_payload(command), "claude-code")
                codex_text = self.run_hook(self.codex_payload(command), "codex-cli")
                claude = json.loads(claude_text)["hookSpecificOutput"] if claude_text.strip() else {}
                wrapped = "ctx-guard-run" in claude.get("updatedInput", {}).get("command", "")
                if not claude or (wrapped and claude.get("permissionDecision") == "allow"):
                    # Codex may treat "allow" as approval, so wrapped commands pass through.
                    self.assertEqual(codex_text, "")
                    continue
                codex = json.loads(codex_text)
                spec = codex["hookSpecificOutput"]
                self.assertLessEqual(set(codex), CODEX_OUTPUT)
                self.assertLessEqual(set(spec), CODEX_SPEC)

                if claude.get("permissionDecision") == "ask":
                    self.assertNotIn("permissionDecision", spec)
                    self.assertNotIn("updatedInput", spec)
                    # The warning alone: Codex runs the command unrewritten.
                    self.assertTrue(claude["permissionDecisionReason"].endswith(codex["systemMessage"]))
                    self.assertTrue(codex["systemMessage"].startswith("ctx-guard warned"))
                    self.assertIn(codex["systemMessage"], spec["additionalContext"])
                    continue
                self.assertEqual(spec.get("permissionDecision"), claude.get("permissionDecision"))
                self.assertEqual(spec.get("permissionDecisionReason"), claude.get("permissionDecisionReason"))
                if "updatedInput" in claude:
                    expected = claude["updatedInput"]["command"].replace(
                        "--filter claude-code", "--filter codex-cli")
                    self.assertEqual(spec["updatedInput"], {"command": expected})
                else:
                    self.assertNotIn("updatedInput", spec)

    def test_rewrite(self):
        out = json.loads(self.run_hook(self.codex_payload("git status"), "codex-cli"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"], {"command": "git status --porcelain=v1 -b"})

    def test_unified_exec_tool_name_is_bash(self):
        out = json.loads(self.run_hook(self.codex_payload("git log", tool_name="exec_command"), "codex-cli"))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"], {"command": "git log --oneline -n 20"})

    def test_wrapped_commands_are_not_approved(self):
        for command in ("npm test", "rm -rf build && make", "pip install reqeusts && make"):
            with self.subTest(command=command):
                out = self.run_hook(self.codex_payload(command), "codex-cli")
                spec = json.loads(out)["hookSpecificOutput"] if out else {}
                self.assertNotIn("permissionDecision", spec)
                self.assertNotIn("updatedInput", spec)
        if os.path.exists(self.stats_file):
            with open(self.stats_file, encoding="utf-8") as f:
                kinds = [json.loads(line)["kind"] for line in f]
            self.assertNotIn("rewrite", kinds)

    def test_nonexistent_package_is_denied(self):
        out = json.loads(self.run_hook(
            self.codex_payload("npm install definitely-not-a-real-package-xyz"), "codex-cli"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_pass_through_and_errors_print_nothing(self):
        # Codex treats exit 0 with no output as success and runs the command.
        self.assertEqual(self.run_hook(self.codex_payload("echo hello"), "codex-cli"), "")
        with mock.patch("pre_bash_rewrite.transform", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_hook(self.codex_payload("git status"), "codex-cli"), "")

    def test_stats_are_tagged_with_the_agent(self):
        self.run_hook(self.codex_payload("git log"), "codex-cli")
        with open(self.stats_file, encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([(e["agent"], e["rule"]) for e in events], [("codex-cli", "git-log")])

    def test_exec_command_left_alone_for_other_agents(self):
        with mock.patch.object(ctx_guard_common, "AGENT", "claude-code"):
            name, _ = ctx_guard_common.normalize_tool_call({"tool_name": "exec_command", "tool_input": {}})
        self.assertEqual(name, "exec_command")


class CodexInstallTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.codex_home = os.path.join(self.home, ".codex")
        os.makedirs(self.codex_home)
        self.path = os.path.join(self.codex_home, "hooks.json")
        for patcher in (mock.patch("shutil.which", return_value=None),
                        mock.patch.dict(os.environ, {"CODEX_HOME": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manifest = {}

    def install(self, dest="/opt/ctx guard"):
        with redirect_stdout(io.StringIO()):
            agent_hooks.install_target(CODEX, self.manifest, self.home, dest, "/usr/bin/python3")

    def restore(self):
        with redirect_stdout(io.StringIO()):
            agent_hooks.restore_target(CODEX, self.manifest)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def write(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def ours(self):
        groups = json.loads(self.read())["hooks"]["PreToolUse"]
        return [g for g in groups if g["hooks"][0]["command"].startswith("CTX_GUARD_AGENT=codex-cli ")]

    def test_install_registers_bash_hook(self):
        self.install()
        [group] = self.ours()
        self.assertEqual(group["matcher"], "^Bash$")
        self.assertEqual(group["hooks"][0]["command"],
                         "CTX_GUARD_AGENT=codex-cli /usr/bin/python3 '/opt/ctx guard/hooks/pre_bash_rewrite.py'")

    def test_existing_file_restored_byte_identical(self):
        original = b'{"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": [{"type": "command", "command": "./mine.sh"}]}]}}'
        self.write(original)
        self.install()
        self.install(dest="/elsewhere")  # reinstall replaces our entry, keeps the true original
        groups = json.loads(self.read())["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 2)
        self.assertIn("/elsewhere/", groups[1]["hooks"][0]["command"])
        self.restore()
        self.assertEqual(self.read(), original)

    def test_new_file_removed(self):
        self.install()
        self.restore()
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(os.path.isdir(self.codex_home))

    def test_user_edits_after_install_are_kept(self):
        self.install()
        config = json.loads(self.read())
        config["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "./stop.sh"}]}]
        self.write(json.dumps(config).encode())
        self.restore()
        self.assertEqual(json.loads(self.read()),
                         {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "./stop.sh"}]}]}})

    def test_codex_home_env_is_used(self):
        other = os.path.join(self.home, "custom-codex")
        os.makedirs(other)
        with mock.patch.dict(os.environ, {"CODEX_HOME": other}):
            self.install()
        self.assertTrue(os.path.isfile(os.path.join(other, "hooks.json")))
        self.assertFalse(os.path.exists(self.path))

    def test_skipped_when_codex_not_installed(self):
        os.rmdir(self.codex_home)
        self.install()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.manifest.get("hook_files", {}), {})


if __name__ == "__main__":
    unittest.main()
