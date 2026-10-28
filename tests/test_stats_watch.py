import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_BIN = os.path.join(REPO_ROOT, "bin", "ctx-guard-stats")


def run_event(command="git"):
    return {"ts": time.time(), "kind": "ctx_guard_run", "agent": "test",
            "command": command, "original_bytes": 4000, "returned_bytes": 400,
            "duration_ms": 10}


def append(path, event):
    with open(path, "a") as f:
        f.write(json.dumps(event) + "\n")


def read_frame(proc, timeout=5.0):
    """Read stdout up to the next form-feed frame separator."""
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not ready:
            continue
        ch = os.read(proc.stdout.fileno(), 4096)
        if not ch:
            break
        buf += ch
        if b"\f" in buf:
            frame, _, _rest = buf.partition(b"\f")
            return frame.decode()
    raise AssertionError(f"no frame within {timeout}s; got {buf!r}")


class TestGainsWatch(unittest.TestCase):
    def test_watch_shows_events_appended_while_running(self):
        with tempfile.TemporaryDirectory() as d:
            stats = os.path.join(d, "stats.jsonl")
            append(stats, run_event())
            proc = subprocess.Popen(
                [sys.executable, STATS_BIN, "--stats-file", stats,
                 "--gains", "--watch", "--interval", "0.1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                first = read_frame(proc)
                self.assertIn("Total commands:   1", first)
                append(stats, run_event("npm"))
                seen = ""
                for _ in range(30):
                    seen = read_frame(proc)
                    if "Total commands:   2" in seen:
                        break
                self.assertIn("Total commands:   2", seen)
            finally:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
