import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO_ROOT, "tests", "check_stdlib_only.py")


class TestCheckStdlibOnly(unittest.TestCase):
    """check_stdlib_only.py run against a throwaway git repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        subprocess.run(["git", "init", "-q", self.root], check=True)
        self.write("hooks/lib/ctx_guard_common.py", "import json\n")
        self.write("hooks/good.py", "#!/usr/bin/env python3\nimport os, sys\n"
                   "from ctx_guard_common import AGENT\nfrom . import sibling\n", mode=0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, path, content, mode=0o644):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode())
        os.chmod(full, mode)

    def check(self):
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        proc = subprocess.run([sys.executable, CHECK, "--root", self.root],
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout

    def test_stdlib_and_own_modules_pass(self):
        self.assertEqual(self.check()[0], 0)

    def test_third_party_import_in_hook_fails(self):
        self.write("hooks/good.py", "import os\n\ndef f():\n    import requests\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("hooks/good.py:4: non-stdlib import 'requests'", out)

    def test_third_party_import_in_extensionless_bin_script_fails(self):
        self.write("bin/tool", "#!/usr/bin/env python3\nfrom yaml import safe_load\n", mode=0o755)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("bin/tool:2: non-stdlib import 'yaml'", out)

    def test_third_party_import_in_install_heredoc_fails(self):
        self.write("install.sh", "#!/usr/bin/env bash\necho hi\n"
                   "python3 - \"$X\" << 'PYEOF'\nimport json\nimport toml\nPYEOF\n", mode=0o755)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("install.sh:5: non-stdlib import 'toml'", out)

    def test_tests_dir_is_not_import_checked(self):
        self.write("tests/helper.py", "import pytest\n")
        self.assertEqual(self.check()[0], 0)

    def test_binaries_and_shebangless_executables_fail(self):
        self.write("bin/blob", b"\x7fELF\x00\x01", mode=0o755)
        self.write("docs/logo.png", b"\x89PNG\r\n\x1a\n\x00")
        self.write("bin/noshebang", "echo hi\n", mode=0o755)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("bin/blob: binary file", out)
        self.assertIn("docs/logo.png: binary file", out)
        self.assertIn("bin/noshebang: executable without a shebang", out)

    def test_this_repo_passes(self):
        proc = subprocess.run([sys.executable, CHECK], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
