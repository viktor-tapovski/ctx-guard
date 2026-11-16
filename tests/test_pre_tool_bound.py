import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, "hooks", "pre_tool_bound.py")
MONITOR = os.path.join(REPO_ROOT, "hooks", "context_monitor.py")
STATS_BIN = os.path.join(REPO_ROOT, "bin", "ctx-guard-stats")


class BoundHookCase(unittest.TestCase):
    """Runs pre_tool_bound.py with stats and state under a throwaway dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.stats = os.path.join(self.root, "stats.jsonl")
        self.env = {**os.environ, "HOME": self.root, "TMPDIR": self.root,
                    "CTX_GUARD_AGENT": "claude-code",
                    "CTX_GUARD_STATE_DIR": os.path.join(self.root, "state"),
                    "CTX_GUARD_LOG_DIR": os.path.join(self.root, "logs"),
                    "CTX_GUARD_STATS_FILE": self.stats}
        for name in ("CTX_GUARD_BOUND_TOOLS", "CTX_GUARD_READ_MAX_LINES",
                     "CTX_GUARD_GREP_MAX", "CTX_GUARD_ESTIMATE_RATIOS"):
            self.env.pop(name, None)

    def tearDown(self):
        self._tmp.cleanup()

    def make_file(self, name, lines):
        path = os.path.join(self.root, name)
        with open(path, "w") as f:
            f.writelines(f"line {i}\n" for i in range(1, lines + 1))
        return path

    def run_hook(self, payload, script=HOOK, **env):
        proc = subprocess.run([sys.executable, script],
                              input=payload if isinstance(payload, str) else json.dumps(payload),
                              capture_output=True, text=True, env={**self.env, **env})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def events(self):
        if not os.path.isfile(self.stats):
            return []
        with open(self.stats) as f:
            return [json.loads(line) for line in f if line.strip()]


class TestPreToolBound(BoundHookCase):
    """pre_tool_bound.py fed Claude Code PreToolUse payloads."""

    def read_payload(self, path, **args):
        return {"tool_name": "Read", "tool_use_id": "toolu_read1",
                "tool_input": {"file_path": path, **args}}

    def test_large_file_gets_limit(self):
        path = self.make_file("big.py", 5000)
        out = self.run_hook(self.read_payload(path))
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["updatedInput"], {"file_path": path, "limit": 400})
        self.assertNotIn("permissionDecision", spec)
        self.assertNotIn("permissionDecision", out)
        self.assertIn("file has 5000 lines", spec["additionalContext"])
        self.assertIn("offset=401", spec["additionalContext"])

    def test_offset_is_kept_and_counted(self):
        path = self.make_file("big.py", 5000)
        out = self.run_hook(self.read_payload(path, offset=1001))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"],
                         {"file_path": path, "offset": 1001, "limit": 400})
        self.assertIn("offset=1401", out["hookSpecificOutput"]["additionalContext"])
        # Only 300 lines left after this offset: nothing to bound.
        self.assertIsNone(self.run_hook(self.read_payload(path, offset=4701)))

    def test_small_file_untouched(self):
        self.assertIsNone(self.run_hook(self.read_payload(self.make_file("small.py", 50))))
        self.assertEqual(self.events(), [])

    def test_explicit_limit_untouched(self):
        path = self.make_file("big.py", 5000)
        self.assertIsNone(self.run_hook(self.read_payload(path, limit=3000)))

    def test_custom_threshold(self):
        path = self.make_file("big.py", 5000)
        out = self.run_hook(self.read_payload(path), CTX_GUARD_READ_MAX_LINES="1000")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["limit"], 1000)

    def test_opt_out(self):
        path = self.make_file("big.py", 5000)
        self.assertIsNone(self.run_hook(self.read_payload(path), CTX_GUARD_BOUND_TOOLS="0"))
        grep = {"tool_name": "Grep", "tool_input": {"pattern": "x", "output_mode": "content"}}
        self.assertIsNone(self.run_hook(grep, CTX_GUARD_BOUND_TOOLS="0"))

    def test_binary_and_non_text_untouched(self):
        binary = os.path.join(self.root, "blob.dat")
        with open(binary, "wb") as f:
            f.write(b"\0\n" * 5000)
        self.assertIsNone(self.run_hook(self.read_payload(binary)))
        pdf = self.make_file("doc.pdf", 5000)
        self.assertIsNone(self.run_hook(self.read_payload(pdf)))

    def test_file_named_in_prompt_untouched(self):
        path = self.make_file("schema.sql", 5000)
        transcript = os.path.join(self.root, "t.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "message": {
                "role": "user", "content": "read all of schema.sql please"}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "big.py"}]}}) + "\n")
        payload = {**self.read_payload(path), "transcript_path": transcript}
        self.assertIsNone(self.run_hook(payload))
        # A prompt that names a different file does not stop the bound.
        other = self.make_file("other.sql", 5000)
        self.assertIsNotNone(self.run_hook({**self.read_payload(other), "transcript_path": transcript}))

    def test_grep_content_gets_head_limit(self):
        args = {"pattern": "TODO", "path": "src", "output_mode": "content", "-n": True}
        out = self.run_hook({"tool_name": "Grep", "tool_input": args})
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"], {**args, "head_limit": 100})
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        out = self.run_hook({"tool_name": "Grep", "tool_input": args}, CTX_GUARD_GREP_MAX="25")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["head_limit"], 25)

    def test_grep_other_modes_and_explicit_limit_untouched(self):
        for args in ({"pattern": "x"},
                     {"pattern": "x", "output_mode": "files_with_matches"},
                     {"pattern": "x", "output_mode": "count"},
                     {"pattern": "x", "output_mode": "content", "head_limit": 500}):
            self.assertIsNone(self.run_hook({"tool_name": "Grep", "tool_input": args}), args)

    def test_other_tools_and_bad_input_untouched(self):
        self.assertIsNone(self.run_hook({"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}}))
        self.assertIsNone(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        self.assertIsNone(self.run_hook("not json"))
        self.assertIsNone(self.run_hook(self.read_payload(os.path.join(self.root, "missing.py"))))

    def test_stats_report_bound_calls_as_estimated(self):
        path = self.make_file("big.py", 5000)
        payload = self.read_payload(path)
        self.run_hook(payload)
        bound = [e for e in self.events() if e["kind"] == "bound"]
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["rule"], "read-bound")
        self.assertEqual(bound[0]["file_lines"], 5000)
        self.assertEqual(bound[0]["bounded_lines"], 400)

        # PostToolUse sees 400 returned lines; Read would have returned 2000,
        # so the per-call ratio is 1 - 400/2000 = 0.8.
        returned = "".join(f"line {i}\n" for i in range(1, 401))
        self.run_hook({**payload, "tool_response": returned}, script=MONITOR)
        est = [e for e in self.events() if e["kind"] == "rewrite_estimate"]
        self.assertEqual(len(est), 1)
        self.assertEqual(est[0]["rule"], "read-bound")
        self.assertAlmostEqual(est[0]["ratio"], 0.8)
        self.assertEqual(est[0]["est_saved_bytes"], len(returned.encode()) * 4)

        report = subprocess.run([sys.executable, STATS_BIN, "--stats-file", self.stats, "--json"],
                                capture_output=True, text=True, env=self.env)
        summary = json.loads(report.stdout)
        self.assertEqual(summary["observed"]["bounded_calls_by_tool"], {"Read": 1})
        self.assertIn("read-bound", summary["estimated"]["by_rule"])
        self.assertEqual(summary["measured"]["saved_bytes"], 0)


class TestPreToolBoundCopilot(BoundHookCase):
    """The same hook fed payloads shaped like Copilot CLI 1.0.62 sends them for
    PascalCase PreToolUse/PostToolUse: Claude tool names, Copilot arguments,
    no tool_use_id and no transcript_path."""

    def setUp(self):
        super().setUp()
        self.env["CTX_GUARD_AGENT"] = "copilot-cli"

    def view_payload(self, path, **args):
        return {"hook_event_name": "PreToolUse", "session_id": "sess-copilot1",
                "timestamp": "2026-11-16T12:00:00.000Z", "cwd": self.root,
                "tool_name": "Read", "tool_input": {"path": path, **args}}

    def test_view_gets_view_range(self):
        path = self.make_file("big.py", 5000)
        out = self.run_hook(self.view_payload(path))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"],
                         {"path": path, "view_range": [1, 400]})
        self.assertEqual(out["modifiedArgs"], {"path": path, "view_range": [1, 400]})
        self.assertNotIn("permissionDecision", out)
        self.assertIn("view_range=[401, 800]", out["additionalContext"])

    def test_view_untouched(self):
        path = self.make_file("big.py", 5000)
        self.assertIsNone(self.run_hook(self.view_payload(path, view_range=[1, -1])))
        self.assertIsNone(self.run_hook(self.view_payload(self.make_file("small.py", 50))))
        self.assertIsNone(self.run_hook(self.view_payload(self.root)))  # directory listing
        self.assertIsNone(self.run_hook(self.view_payload(path), CTX_GUARD_BOUND_TOOLS="0"))

    def test_grep_gets_head_limit(self):
        args = {"pattern": "TODO", "paths": ["src"], "output_mode": "content"}
        out = self.run_hook({**self.view_payload(""), "tool_name": "Grep", "tool_input": args})
        self.assertEqual(out["modifiedArgs"], {**args, "head_limit": 100})

    def test_stats_pair_without_tool_use_id(self):
        path = self.make_file("big.py", 5000)
        payload = self.view_payload(path)
        self.run_hook(payload)
        # Copilot's view has no default cap: unbounded it returns all 5000 lines.
        returned = "".join(f"{i}. line {i}\n" for i in range(1, 401))
        post = {**payload, "hook_event_name": "PostToolUse", "tool_input": {
                    "path": path, "view_range": [1, 400]},
                "tool_result": {"result_type": "success", "text_result_for_llm": returned}}
        self.run_hook(post, script=MONITOR)
        est = [e for e in self.events() if e["kind"] == "rewrite_estimate"]
        self.assertEqual(len(est), 1)
        self.assertEqual(est[0]["agent"], "copilot-cli")
        self.assertAlmostEqual(est[0]["ratio"], 0.92)


if __name__ == "__main__":
    unittest.main()
