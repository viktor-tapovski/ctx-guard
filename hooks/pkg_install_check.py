#!/usr/bin/env python3
"""
ctx-guard package-install security check.

Detects package-install / install-and-run commands (npm, pip, cargo, gem,
apt/apt-get, brew, apk, npx, pipx run, uvx) and flags:
  - structural red flags (curl|bash, unpinned versions, bypass flags,
    git/URL-based installs) -- no network, applies to every ecosystem.
  - registry-lookup red flags (nonexistent package, published <7 days ago,
    typosquat distance <=2 vs a bundled popular-package list) -- npm, pip,
    cargo, gem only (apt/brew/apk have no open "anyone can publish"
    registry, so those ecosystems get structural checks only).

No third-party dependencies -- stdlib only (urllib.request), matching the
rest of ctx-guard. Fails open on any network error: registry timeout,
DNS failure, or unexpected HTTP status skip the registry-based signals for
that package but never block or crash the caller.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import os
import re
import shlex

TIMEOUT = 1.5
AGE_WARN_DAYS = 7
TYPOSQUAT_MAX_DISTANCE = 2

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
POPULAR_PACKAGES_DIR = os.path.join(LIB_DIR, "popular_packages")

REGISTRY_ECOSYSTEMS = {"npm", "pip", "cargo", "gem"}

BYPASS_FLAGS = (
    "--force",
    "--yes",
    "-y",
    "--allow-unauthenticated",
    "--no-confirm",
)

PIPE_TO_SHELL = re.compile(
    r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(bash|sh)\b"
)

# (ecosystem, regex matching the command head, group(1) = the argument tail)
_ECOSYSTEM_HEAD = [
    ("npm", re.compile(r"^(?:npm|pnpm|yarn)\s+(?:install|i|add)\s*(.*)$")),
    ("npm", re.compile(r"^npx\s+(.*)$")),
    ("pip", re.compile(r"^(?:pip|pip3)\s+install\s*(.*)$")),
    ("pip", re.compile(r"^pipx\s+run\s+(.*)$")),
    ("pip", re.compile(r"^uvx\s+(.*)$")),
    ("cargo", re.compile(r"^cargo\s+install\s*(.*)$")),
    ("gem", re.compile(r"^gem\s+install\s*(.*)$")),
    ("apt", re.compile(r"^(?:apt|apt-get)\s+install\s*(.*)$")),
    ("brew", re.compile(r"^brew\s+install\s*(.*)$")),
    ("apk", re.compile(r"^apk\s+add\s*(.*)$")),
]

_MANIFEST_FLAGS = {"-r", "--requirement"}

_URL_TOKEN = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git\+)", re.IGNORECASE)

# Truncate the argument tail at the first compound-command separator so a
# chained command (e.g. "npm install left-pad && npm test") only yields the
# install command's own arguments -- not tokens from the next command.
_COMPOUND_SEPARATOR = re.compile(r"\s*(?:;|\|\||&&|&|\|)\s*")


@dataclasses.dataclass
class PackageSpec:
    name: str
    pinned: bool
    is_url: bool


@dataclasses.dataclass
class InstallCommand:
    ecosystem: str
    packages: list  # list[PackageSpec]
    manifest_only: bool


def _split_name_and_pin(token: str, ecosystem: str) -> tuple[str, bool]:
    """Split a single package token into (name, pinned) for one ecosystem."""
    if ecosystem == "npm":
        # scoped packages: @scope/name[@version]; unscoped: name[@version]
        if token.startswith("@"):
            scope_end = token.find("/", 1)
            if scope_end == -1:
                return token, False
            rest = token[scope_end + 1:]
            at = rest.find("@")
            if at == -1:
                return token, False
            return token[: scope_end + 1 + at], True
        at = token.find("@")
        if at <= 0:
            return token, False
        return token[:at], True
    if ecosystem == "pip":
        for sep in ("==", ">=", "<=", "~="):
            if sep in token:
                return token.split(sep, 1)[0], True
        return token, False
    # cargo/gem: version comes from a separate -v/--version flag, handled
    # by the caller; a bare token here is never self-pinned.
    return token, False


def detect_install_command(cmd: str):
    """Return an InstallCommand, or None if cmd isn't install-shaped."""
    stripped = cmd.strip()
    for ecosystem, pattern in _ECOSYSTEM_HEAD:
        match = pattern.match(stripped)
        if not match:
            continue
        tail = match.group(1).strip()
        tail = _COMPOUND_SEPARATOR.split(tail, maxsplit=1)[0]
        try:
            tokens = shlex.split(tail)
        except ValueError:
            tokens = tail.split()

        if not tokens:
            return InstallCommand(ecosystem=ecosystem, packages=[], manifest_only=True)

        manifest_only = any(t in _MANIFEST_FLAGS for t in tokens)

        version_flag_value = None
        if ecosystem in ("cargo", "gem"):
            for i, t in enumerate(tokens):
                if t in ("-v", "--version") and i + 1 < len(tokens):
                    version_flag_value = tokens[i + 1]

        names = [t for t in tokens if not t.startswith("-")]
        # drop the value that followed -v/--version (it isn't a package name)
        if version_flag_value is not None and version_flag_value in names:
            names.remove(version_flag_value)

        if manifest_only or not names:
            return InstallCommand(ecosystem=ecosystem, packages=[], manifest_only=True)

        packages = []
        for name in names:
            if _URL_TOKEN.match(name) or ("://" in name):
                packages.append(PackageSpec(name=name, pinned=False, is_url=True))
                continue
            base_name, pinned = _split_name_and_pin(name, ecosystem)
            if ecosystem in ("cargo", "gem") and version_flag_value:
                pinned = True
            packages.append(PackageSpec(name=base_name, pinned=pinned, is_url=False))

        return InstallCommand(ecosystem=ecosystem, packages=packages, manifest_only=False)
    return None


def has_bypass_flag(cmd: str):
    tokens = cmd.split()
    for flag in BYPASS_FLAGS:
        if flag in tokens:
            return flag
    return None


def has_pipe_to_shell(cmd: str) -> bool:
    return bool(PIPE_TO_SHELL.search(cmd))


_POPULAR_CACHE: dict = {}


def load_popular_packages(ecosystem: str) -> list:
    if ecosystem not in REGISTRY_ECOSYSTEMS:
        return []
    if ecosystem in _POPULAR_CACHE:
        return _POPULAR_CACHE[ecosystem]
    filename = {"npm": "npm.txt", "pip": "pypi.txt", "cargo": "crates.txt", "gem": "gems.txt"}[ecosystem]
    path = os.path.join(POPULAR_PACKAGES_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
    except OSError:
        names = []
    _POPULAR_CACHE[ecosystem] = names
    return names


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr
    return prev[-1]


def typosquat_match(name: str, ecosystem: str):
    lowered = name.lower()
    for popular in load_popular_packages(ecosystem):
        if lowered == popular.lower():
            return None
        if abs(len(lowered) - len(popular)) > TYPOSQUAT_MAX_DISTANCE:
            continue
        if levenshtein(lowered, popular.lower()) <= TYPOSQUAT_MAX_DISTANCE:
            return popular
    return None
