import json
import os
import stat
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_BIN = os.path.join(REPO_ROOT, "bin", "ctx-guard-run")
CORPUS = os.path.join(REPO_ROOT, "tests", "fixtures", "redaction", "corpus.json")
WITHHELD = "ctx-guard: output withheld because redaction failed"


def load_corpus():
    """Corpus cases with secrets joined from their parts at runtime (see the
    fixture's _comment: no token-shaped literal is stored in the repo)."""
    with open(CORPUS, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    for case in cases:
        secrets = {name: "".join(parts) for name, parts in case.get("secrets", {}).items()}
        text = "\n".join(case["lines"]) + "\n"
        for name, value in secrets.items():
            text = text.replace("{{" + name + "}}", value)
        case["secret_values"] = sorted(secrets.values())
        case["text"] = text
    return cases


class RedactionHarness(unittest.TestCase):
    """Runs the real ctx-guard-run under a throwaway log dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.log_dir = os.path.join(root, "logs")
        os.makedirs(os.path.join(root, "tmp"))
        self.env = {**os.environ, "HOME": root, "TMPDIR": os.path.join(root, "tmp"),
                    "CTX_GUARD_LOG_DIR": self.log_dir,
                    "CTX_GUARD_STATE_DIR": os.path.join(root, "state"),
                    "CTX_GUARD_STATS_FILE": os.path.join(root, "stats.jsonl"),
                    "CTX_GUARD_REDACT_LOGS": "1"}
        self.counter = 0

    def tearDown(self):
        self._tmp.cleanup()

    def run_text(self, text, env=None, cmd_line=None):
        """Echo text through ctx-guard-run; return (stdout, archived log, meta)."""
        self.counter += 1
        base = os.path.join(self._tmp.name, f"case{self.counter}")
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(text)
        with open(base + ".sh", "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n" + (cmd_line or f"cat '{base}.txt'") + "\n")
        before = set(os.listdir(self.log_dir)) if os.path.isdir(self.log_dir) else set()
        out = subprocess.run(["bash", RUN_BIN, base + ".sh"], capture_output=True, text=True,
                             env=env or self.env)
        new = sorted(set(os.listdir(self.log_dir)) - before)
        self.assertFalse([n for n in new if n.startswith("raw-")], "raw log left behind")
        logs = [n for n in new if n.startswith("run-") and not n.endswith(".meta")]
        self.assertEqual(len(logs), 1, new)
        log_path = os.path.join(self.log_dir, logs[0])
        with open(log_path, encoding="utf-8") as f:
            archived = f.read()
        meta = ""
        if os.path.exists(log_path + ".meta"):
            with open(log_path + ".meta", encoding="utf-8") as f:
                meta = f.read()
        return out.stdout, archived, meta


class TestRedactionCorpus(RedactionHarness):
    def test_corpus_secrets_never_reach_archive_or_output(self):
        for case in load_corpus():
            with self.subTest(case=case["name"]):
                stdout, archived, meta = self.run_text(case["text"])
                if case.get("keep"):
                    self.assertEqual(archived, case["text"])
                    continue
                self.assertTrue(case["secret_values"])
                for secret in case["secret_values"]:
                    self.assertNotIn(secret, archived)
                    self.assertNotIn(secret, stdout)
                    self.assertNotIn(secret, meta)
                self.assertIn("[REDACTED", archived)

    def test_secret_in_command_line_redacted_in_list_metadata(self):
        password = "fake" + "MetaPass" + "EXAMPLE"
        _, _, meta = self.run_text("", cmd_line=f"echo connecting to postgres://u:{password}@db/app")
        self.assertIn("cmd=", meta)
        self.assertNotIn(password, meta)
        self.assertIn("[REDACTED]", meta)

    def test_compressed_view_is_redacted(self):
        token = "ghp" + "_" + "EXAMPLEfakeTokenNotReal0000000000000"
        text = "".join(f"line {i}\n" for i in range(300)) + f"error: auth failed for {token}\n"
        stdout, archived, _ = self.run_text(text)
        self.assertIn("output compressed", stdout)
        self.assertIn("[REDACTED TOKEN]", stdout)
        self.assertNotIn(token, stdout + archived)


class TestRedactionFailsClosed(RedactionHarness):
    """Force the sanitizer to fail by shadowing python3 on PATH: ctx-guard-run
    must withhold the output and never archive it raw."""

    SECRET = "API_KEY=" + "fake" + "-fail-closed-" + "EXAMPLE"

    def shadow_python(self, body):
        shim_dir = os.path.join(self._tmp.name, "shim")
        os.makedirs(shim_dir, exist_ok=True)
        shim = os.path.join(shim_dir, "python3")
        with open(shim, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(shim, stat.S_IRWXU)
        return {**self.env, "PATH": shim_dir + os.pathsep + self.env.get("PATH", "")}

    def assert_withheld(self, env):
        stdout, archived, meta = self.run_text(f"starting\n{self.SECRET}\ndone\n", env=env)
        self.assertEqual(archived, WITHHELD + "\n")
        self.assertEqual(stdout.strip(), WITHHELD)
        self.assertNotIn("fail-closed", stdout + archived + meta)
        self.assertEqual([n for n in os.listdir(self.log_dir) if n.startswith(("raw-", "out-"))], [])

    def test_sanitizer_unavailable_withholds_output(self):
        self.assert_withheld(self.shadow_python("exit 1"))

    def test_sanitizer_crash_after_partial_output_withholds_output(self):
        # Worst case: the sanitizer echoes raw input, then fails.
        self.assert_withheld(self.shadow_python('cat "$2"; exit 1'))


if __name__ == "__main__":
    unittest.main()
