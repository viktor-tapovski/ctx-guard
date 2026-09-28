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
# Names shorter than this sit within edit-distance 2 of too many real
# packages (six/pip, ring/rand) for a match to be meaningful signal.
TYPOSQUAT_MIN_LENGTH = 5

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

# (ecosystem, regex matching the command head, single_package_only).
# group(1) = the argument tail. single_package_only is True for the
# run-and-execute forms (npx / pipx run / uvx): only the first positional is
# the package; everything after it is the wrapped tool's own argv.
_ECOSYSTEM_HEAD = [
    ("npm", re.compile(r"^(?:npm|pnpm|yarn)\s+(?:install|i|add)\s*(.*)$"), False),
    ("npm", re.compile(r"^npx\s+(.*)$"), True),
    ("pip", re.compile(r"^(?:pip|pip3)\s+install\s*(.*)$"), False),
    ("pip", re.compile(r"^pipx\s+run\s+(.*)$"), True),
    ("pip", re.compile(r"^uvx\s+(.*)$"), True),
    ("cargo", re.compile(r"^cargo\s+install\s*(.*)$"), False),
    ("gem", re.compile(r"^gem\s+install\s*(.*)$"), False),
    ("apt", re.compile(r"^(?:apt|apt-get)\s+install\s*(.*)$"), False),
    ("brew", re.compile(r"^brew\s+install\s*(.*)$"), False),
    ("apk", re.compile(r"^apk\s+add\s*(.*)$"), False),
]

_MANIFEST_FLAGS = {"-r", "--requirement"}

_URL_TOKEN = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git\+)", re.IGNORECASE)

# Local filesystem paths / archives are not registry-lookupable package names.
_LOCAL_PATH = re.compile(r"^(\.{1,2}(/|$)|/|~/)|\.(whl|tar\.gz|tgz|zip)$", re.IGNORECASE)

# pip version-specifier operators, longest/most-specific first.
_PIP_VERSION_SEPARATORS = ("===", "==", "!=", "~=", ">=", "<=", ">", "<")

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
        token = re.sub(r"\[[^\]]*\]", "", token)  # drop extras: requests[security]
        for sep in _PIP_VERSION_SEPARATORS:
            if sep in token:
                return token.split(sep, 1)[0].strip(), True
        return token, False
    # cargo/gem: version comes from a separate -v/--version flag, handled
    # by the caller; a bare token here is never self-pinned.
    return token, False


def detect_install_command(cmd: str):
    """Return an InstallCommand, or None if cmd isn't install-shaped."""
    stripped = cmd.strip()
    for ecosystem, pattern, single_package_only in _ECOSYSTEM_HEAD:
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

        if single_package_only:
            names = names[:1]

        packages = []
        for name in names:
            if _URL_TOKEN.match(name) or ("://" in name) or _LOCAL_PATH.search(name):
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
    popular = load_popular_packages(ecosystem)
    popular_lower = {p.lower() for p in popular}
    lowered = name.lower()
    # npm scopes are namespace-owned, so compare the bare name: "@babel/core"
    # is judged as "core", "@types/react" as "react" (exact -> not flagged).
    bare = lowered.split("/", 1)[-1] if lowered.startswith("@") else lowered
    if lowered in popular_lower or bare in popular_lower:
        return None
    if len(bare) < TYPOSQUAT_MIN_LENGTH:
        return None
    for candidate in popular:
        cand = candidate.lower()
        if abs(len(bare) - len(cand)) > TYPOSQUAT_MAX_DISTANCE:
            continue
        if levenshtein(bare, cand) <= TYPOSQUAT_MAX_DISTANCE:
            return candidate
    return None


def _read_allowlist_file(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except OSError:
        return []


def load_allowlist(cwd: str = None) -> list:
    global_path = os.path.join(os.path.expanduser("~"), ".ctx-guard", "pkg-allowlist")
    patterns = _read_allowlist_file(global_path)
    if cwd:
        repo_path = os.path.join(cwd, ".ctx-guard", "pkg-allowlist")
        patterns = patterns + [p for p in _read_allowlist_file(repo_path) if p not in patterns]
    return patterns


def is_allowlisted(name: str, patterns: list) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_REGISTRY_URL = {
    "npm": "https://registry.npmjs.org/{name}",
    "pip": "https://pypi.org/pypi/{name}/json",
    "cargo": "https://crates.io/api/v1/crates/{name}",
    "gem": "https://rubygems.org/api/v1/gems/{name}.json",
}


@dataclasses.dataclass
class RegistryInfo:
    exists: bool
    age_days: float = None


def _parse_iso8601(value: str) -> float:
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return delta.total_seconds() / 86400.0


def _extract_age_days(ecosystem: str, data: dict):
    try:
        if ecosystem == "npm":
            latest = data["dist-tags"]["latest"]
            return _parse_iso8601(data["time"][latest])
        if ecosystem == "pip":
            latest = data["info"]["version"]
            releases = data["releases"].get(latest) or []
            if not releases:
                return None
            return _parse_iso8601(releases[0]["upload_time_iso_8601"])
        if ecosystem == "cargo":
            return _parse_iso8601(data["crate"]["created_at"])
        if ecosystem == "gem":
            ts = data.get("version_created_at")
            return _parse_iso8601(ts) if ts else None
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    return None


def registry_lookup(ecosystem: str, name: str):
    """Return RegistryInfo, or None if the lookup itself failed (fail open)."""
    template = _REGISTRY_URL.get(ecosystem)
    if template is None:
        return None
    url = template.format(name=urllib.parse.quote(name, safe="@/"))
    req = urllib.request.Request(url, headers={"User-Agent": "ctx-guard-pkg-check"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return RegistryInfo(exists=False)
        return None  # unexpected status -- fail open, don't guess
    except urllib.error.URLError:
        return None  # timeout / DNS / offline -- fail open
    except Exception:
        return None  # never let a parsing surprise escape -- fail open

    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None

    return RegistryInfo(exists=True, age_days=_extract_age_days(ecosystem, data))


@dataclasses.dataclass
class PkgCheckResult:
    action: str  # "block" | "warn"
    reason: str


def check_command(cmd: str, cwd: str = None):
    if os.environ.get("CTX_GUARD_PKG_CHECK", "1") == "0":
        return None

    if has_pipe_to_shell(cmd):
        return PkgCheckResult(
            action="warn",
            reason="pipes a remote script directly into a shell (curl|bash / wget|sh) -- review before running",
        )

    install_cmd = detect_install_command(cmd)
    if install_cmd is None or install_cmd.manifest_only or not install_cmd.packages:
        return None

    allowlist = load_allowlist(cwd=cwd)
    warnings = []
    block_reasons = []

    bypass_flag = has_bypass_flag(cmd)
    if bypass_flag:
        warnings.append(f"command uses '{bypass_flag}', bypassing a normal confirmation prompt")

    for pkg in install_cmd.packages:
        if is_allowlisted(pkg.name, allowlist):
            continue

        if pkg.is_url:
            warnings.append(f"'{pkg.name}' is a git/URL-based install -- unverifiable source, not from a package registry")
            continue

        if install_cmd.ecosystem not in REGISTRY_ECOSYSTEMS:
            continue  # apt/brew/apk: no registry, no meaningful version-pin concept

        if not pkg.pinned:
            warnings.append(f"'{pkg.name}' has no version pin")

        info = registry_lookup(install_cmd.ecosystem, pkg.name)
        if info is None:
            continue  # registry unreachable -- fail open, structural warnings above still stand

        if not info.exists:
            block_reasons.append(
                f"'{pkg.name}' does not exist on the {install_cmd.ecosystem} registry "
                "(possible hallucinated or typosquatted package name)"
            )
            continue

        if info.age_days is not None and info.age_days < AGE_WARN_DAYS:
            warnings.append(f"'{pkg.name}' was published {info.age_days:.1f} days ago (< {AGE_WARN_DAYS}d)")

        match = typosquat_match(pkg.name, install_cmd.ecosystem)
        if match:
            warnings.append(f"'{pkg.name}' is within edit-distance {TYPOSQUAT_MAX_DISTANCE} of popular package '{match}' -- possible typosquat")

    if block_reasons:
        return PkgCheckResult(action="block", reason="; ".join(block_reasons))
    if warnings:
        return PkgCheckResult(action="warn", reason="; ".join(warnings))
    return None
