import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks", "lib"))

import statusline_config as sc  # noqa: E402


class LineFormat:
    """A non-JSON config: the whole file is the status line command, the way
    an agent with a plain-text or TOML setting would need its own format."""

    def load(self, path):
        if not os.path.isfile(path):
            return {"line": None}
        with open(path) as f:
            return {"line": f.read() or None}

    def current(self, doc):
        return doc["line"]

    def command_of(self, line):
        return line or ""

    def set(self, doc, command, previous):
        doc["line"] = command

    def restore(self, doc, previous):
        doc["line"] = previous

    def save(self, path, doc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(doc["line"] or "")


class TestStatuslineConfig(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.dest = os.path.join(self.home, ".ctx-guard")

    def tearDown(self):
        self._tmp.cleanup()

    def path(self, rel):
        return os.path.join(self.home, rel)

    def write_json(self, rel, doc):
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w") as f:
            json.dump(doc, f)

    def read_json(self, rel):
        with open(self.path(rel)) as f:
            return json.load(f)

    def test_claude_and_copilot_round_trip(self):
        mine = {"type": "command", "command": "bash ~/my-line.sh", "padding": 1}
        self.write_json(".claude/settings.json", {"statusLine": mine, "theme": "dark"})
        manifest = {}
        sc.install(manifest, self.home, self.dest, "python3")
        sc.install(manifest, self.home, self.dest, "python3")  # re-run keeps the original

        claude = self.read_json(".claude/settings.json")
        self.assertIn("ctx-guard-statusline claude-code", claude["statusLine"]["command"])
        self.assertEqual(claude["statusLine"]["padding"], 1)
        self.assertEqual(manifest["statusline"]["claude-code"]["previous"], mine)
        copilot = self.read_json(".copilot/settings.json")
        self.assertIn("ctx-guard-statusline copilot-cli", copilot["statusLine"]["command"])
        self.assertEqual(manifest["statusline"]["copilot-cli"]["format"], "json")

        sc.restore(manifest)
        self.assertEqual(self.read_json(".claude/settings.json"),
                         {"statusLine": mine, "theme": "dark"})
        self.assertNotIn("statusLine", self.read_json(".copilot/settings.json"))

    def test_changed_status_line_is_preserved(self):
        manifest = {}
        sc.install(manifest, self.home, self.dest, "python3")
        self.write_json(".claude/settings.json", {"statusLine": {"type": "command", "command": "new"}})
        sc.restore(manifest)
        self.assertEqual(self.read_json(".claude/settings.json")["statusLine"]["command"], "new")

    def test_new_agent_is_one_target_and_skipped_when_not_installed(self):
        targets = sc.TARGETS + [sc.Target("gemini-cli", ".gemini/settings.json",
                                          only_if_installed=".gemini")]
        with mock.patch.object(sc, "TARGETS", targets):
            manifest = {}
            sc.install(manifest, self.home, self.dest, "python3")
            self.assertNotIn("gemini-cli", manifest["statusline"])
            self.assertFalse(os.path.exists(self.path(".gemini")))

            os.makedirs(self.path(".gemini"))
            sc.install(manifest, self.home, self.dest, "python3")
            gemini = self.read_json(".gemini/settings.json")
            self.assertIn("ctx-guard-statusline gemini-cli", gemini["statusLine"]["command"])

    def test_new_config_format_plugs_in(self):
        targets = [sc.Target("codex-cli", ".codex/statusline", format="line")]
        with mock.patch.object(sc, "TARGETS", targets), \
                mock.patch.dict(sc.FORMATS, {"line": LineFormat()}):
            os.makedirs(self.path(".codex"))
            with open(self.path(".codex/statusline"), "w") as f:
                f.write("echo mine")
            manifest = {}
            sc.install(manifest, self.home, self.dest, "python3")
            with open(self.path(".codex/statusline")) as f:
                self.assertIn("ctx-guard-statusline codex-cli", f.read())
            sc.restore(manifest)
            with open(self.path(".codex/statusline")) as f:
                self.assertEqual(f.read(), "echo mine")

    def test_manifest_from_older_install_has_no_format(self):
        manifest = {}
        sc.install(manifest, self.home, self.dest, "python3")
        for entry in manifest["statusline"].values():
            del entry["format"]
        sc.restore(manifest)
        self.assertNotIn("statusLine", self.read_json(".claude/settings.json"))


if __name__ == "__main__":
    unittest.main()
