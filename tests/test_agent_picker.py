"""
Agent registry (hooks/lib/agents.py) and the arrow-key picker
(hooks/lib/agent_picker.py): parsing --agents values, detection, the
picker's key handling, and one run through a real pseudo-terminal.
"""
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO_ROOT, "hooks", "lib")
sys.path.insert(0, LIB)

import agent_picker  # noqa: E402
import agents  # noqa: E402
from agent_picker import Row, State, render, step  # noqa: E402

UP, DOWN, SPACE, ENTER = "\x1b[A", "\x1b[B", " ", "\r"


class ParseTest(unittest.TestCase):
    def test_normalizes_order_and_duplicates(self):
        self.assertEqual(agents.parse(" codex-cli,claude-code,codex-cli "), ["claude-code", "codex-cli"])

    def test_all(self):
        self.assertEqual(agents.parse("all"), agents.IDS)

    def test_unknown_and_empty_rejected(self):
        for text in ("nope", "claude-code,nope", "", ",", "all,claude-code"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    agents.parse(text)

    def test_cli_exit_codes(self):
        script = os.path.join(LIB, "agents.py")
        ok = subprocess.run([sys.executable, script, "parse", "codex-cli"], capture_output=True, text=True)
        self.assertEqual((ok.returncode, ok.stdout), (0, "codex-cli\n"))
        bad = subprocess.run([sys.executable, script, "parse", "nope"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("unknown agent nope", bad.stderr)


class DetectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        for patcher in (mock.patch("shutil.which", return_value=None),
                        mock.patch.dict(os.environ, {"CODEX_HOME": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_config_dirs(self):
        self.assertEqual(agents.detected(self.home), [])
        os.makedirs(os.path.join(self.home, ".copilot"))
        os.makedirs(os.path.join(self.home, ".gemini", "antigravity-cli"))
        self.assertEqual(agents.detected(self.home), ["copilot-cli", "antigravity-cli"])

    def test_gemini_cli_alone_is_not_antigravity(self):
        os.makedirs(os.path.join(self.home, ".gemini"))
        self.assertEqual(agents.detected(self.home), [])

    def test_command_on_path(self):
        with mock.patch("shutil.which", side_effect=lambda name: "/bin/x" if name == "codex" else None):
            self.assertEqual(agents.detected(self.home), ["codex-cli"])


ROWS = [Row("claude-code", "Claude Code", True), Row("copilot-cli", "Copilot", False),
        Row("codex-cli", "Codex CLI", True)]


def press(state, *keys):
    for key in keys:
        state = step(state, key, len(ROWS))
    return state


class StepTest(unittest.TestCase):
    def test_move_wraps(self):
        self.assertEqual(press(State(), UP).cursor, 2)
        self.assertEqual(press(State(), DOWN, DOWN, DOWN).cursor, 0)
        self.assertEqual(press(State(), "j", "k", "j").cursor, 1)

    def test_space_toggles_row_under_cursor(self):
        state = press(State(), DOWN, SPACE)
        self.assertEqual(state.selected, {1})
        self.assertEqual(press(state, SPACE).selected, set())

    def test_a_selects_all_then_none(self):
        self.assertEqual(press(State(), "a").selected, {0, 1, 2})
        self.assertEqual(press(State(), "a", "a").selected, set())

    def test_enter_needs_a_selection(self):
        state = press(State(), ENTER)
        self.assertFalse(state.done)
        self.assertIn("Select at least one", state.message)
        state = press(state, SPACE)
        self.assertEqual(state.message, "")
        self.assertTrue(press(state, ENTER).done)

    def test_cancel_keys(self):
        for key in ("q", "\x1b", "\x03"):
            with self.subTest(key=repr(key)):
                self.assertTrue(press(State(), key).cancelled)

    def test_other_keys_ignored(self):
        state = State(cursor=1, selected=frozenset({0}))
        self.assertEqual(press(state, "x", "\x1b[C"), state)

    def test_preselect(self):
        state = agent_picker.initial_state(ROWS, ["codex-cli", "unknown", ""])
        self.assertEqual(state.selected, {2})


class RenderTest(unittest.TestCase):
    def test_marks_cursor_selection_and_detection(self):
        lines = render(State(cursor=1, selected=frozenset({0})), ROWS)
        rows = lines[3:6]
        self.assertEqual(rows[0], "  [x] Claude Code   detected")
        self.assertEqual(rows[1], "> [ ] Copilot       not found")
        self.assertEqual(rows[2], "  [ ] Codex CLI     detected")


class PseudoTerminalTest(unittest.TestCase):
    """Drive the real picker through a pty: down, space, enter."""

    def test_arrow_keys_select_and_confirm(self):
        pid, fd = pty.fork()
        if pid == 0:  # child: the pty is its controlling terminal (/dev/tty)
            sys.path.insert(0, LIB)
            chosen = agent_picker.run(ROWS, ["claude-code"])
            os.write(1, f"RESULT:{','.join(chosen) if chosen is not None else 'CANCELLED'}\n".encode())
            os._exit(0)

        output = b""

        def read_until(marker, timeout=5.0):
            nonlocal output
            deadline = time.time() + timeout
            while marker not in output and time.time() < deadline:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        output += os.read(fd, 4096)
                    except OSError:
                        break
            return marker in output

        try:
            self.assertTrue(read_until(b"q cancel"), output)
            for key in (DOWN, DOWN, SPACE, "\r"):
                os.write(fd, key.encode())
                time.sleep(0.15)  # longer than the ESC-sequence wait, so keys arrive separately
            self.assertTrue(read_until(b"RESULT:"), output)
            read_until(b"\n", timeout=1)
        finally:
            os.waitpid(pid, 0)
            os.close(fd)
        result = output.split(b"RESULT:", 1)[1].split(b"\n", 1)[0].strip()
        self.assertEqual(result, b"claude-code,codex-cli")
        self.assertIn(b"\x1b[?25h", output)  # cursor shown again


if __name__ == "__main__":
    unittest.main()
