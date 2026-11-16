#!/usr/bin/env python3
"""
CI guard for the "nothing to install" promise:

1. Every Python file under hooks/ and bin/ (by .py extension or python
   shebang), and every Python heredoc in a tracked shell script outside
   tests/, imports only the standard library or ctx-guard's own modules.
2. Every tracked executable is a text script with a shebang, and no tracked
   file is binary.

Usage: python3 tests/check_stdlib_only.py [--root DIR]. Exits 1 and prints
one line per problem when either check fails. Needs git for the list of
tracked files. Python 3.10+ supplies sys.stdlib_module_names; older Pythons
fall back to listing the standard library directory.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import sysconfig

PYTHON_DIRS = ("hooks/", "bin/")
# `python3 - ... <<'PYEOF'` up to a line holding only the delimiter.
HEREDOC_RE = re.compile(r"python3?\b[^\n]*<<-?\s*'?(\w+)'?[^\n]*\n(.*?)\n\1\n", re.S)


def stdlib_modules() -> set[str]:
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return set(names)
    # Python < 3.10: everything in the stdlib dir and lib-dynload, plus the
    # interpreter's built-ins. site-packages is a subdirectory and skipped.
    found = set(sys.builtin_module_names)
    stdlib = sysconfig.get_paths()["stdlib"]
    for directory in (stdlib, os.path.join(stdlib, "lib-dynload")):
        for entry in os.listdir(directory):
            name, ext = os.path.splitext(entry)
            if entry == "site-packages":
                continue
            if ext in (".py", ".so", ".pyd") or (
                    not ext and os.path.isfile(os.path.join(directory, entry, "__init__.py"))):
                found.add(name.split(".")[0])
    return found


def tracked_files(root: str) -> list[tuple[str, str]]:
    """(mode, path) for every file in the git index."""
    out = subprocess.run(["git", "-C", root, "ls-files", "-s", "-z"],
                         capture_output=True, check=True).stdout.decode()
    files = []
    for entry in out.split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            files.append((meta.split()[0], path))
    return files


def read_bytes(root: str, path: str) -> bytes:
    with open(os.path.join(root, path), "rb") as f:
        return f.read()


def is_python(path: str, data: bytes) -> bool:
    first = data.split(b"\n", 1)[0]
    return path.endswith(".py") or (first.startswith(b"#!") and b"python" in first)


def is_shell(path: str, data: bytes) -> bool:
    first = data.split(b"\n", 1)[0]
    return path.endswith(".sh") or (first.startswith(b"#!") and
                                    (b"bash" in first or first.endswith(b"/sh")))


def own_modules(files: list[tuple[str, str]]) -> set[str]:
    """Modules the hooks import from the repo itself (hooks/ and hooks/lib/)."""
    return {os.path.splitext(os.path.basename(p))[0]
            for _, p in files if p.startswith("hooks/") and p.endswith(".py")}


def bad_imports(source: str, label: str, line_offset: int, allowed: set[str]) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"{label}:{(e.lineno or 0) + line_offset}: cannot parse: {e.msg}"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top not in allowed:
                problems.append(f"{label}:{node.lineno + line_offset}: "
                                f"non-stdlib import '{name}'")
    return problems


def check_imports(root: str, files: list[tuple[str, str]]) -> list[str]:
    allowed = stdlib_modules() | own_modules(files)
    problems = []
    for _, path in files:
        data = read_bytes(root, path)
        if b"\0" in data:
            continue  # reported by check_files
        text = data.decode("utf-8", errors="replace")
        if path.startswith(PYTHON_DIRS) and is_python(path, data):
            problems += bad_imports(text, path, 0, allowed)
        elif not path.startswith("tests/") and is_shell(path, data):
            for m in HEREDOC_RE.finditer(text):
                offset = text.count("\n", 0, m.start(2))
                problems += bad_imports(m.group(2), path, offset, allowed)
    return problems


def check_files(root: str, files: list[tuple[str, str]]) -> list[str]:
    problems = []
    for mode, path in files:
        data = read_bytes(root, path)
        if b"\0" in data:
            problems.append(f"{path}: binary file")
        elif mode == "100755" and not data.startswith(b"#!"):
            problems.append(f"{path}: executable without a shebang")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = parser.parse_args()
    files = tracked_files(args.root)
    problems = check_imports(args.root, files) + check_files(args.root, files)
    for p in problems:
        print(p)
    if problems:
        print(f"check_stdlib_only: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"check_stdlib_only: {len(files)} tracked files OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
