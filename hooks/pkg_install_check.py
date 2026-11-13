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

# Stable reason codes, recorded in stats events and returned on
# PkgCheckResult.codes. Most severe first: the first code present is the
# "primary" reason shown in stats.
REASON_NONEXISTENT = "nonexistent"
REASON_TYPOSQUAT = "typosquat"
REASON_NEW_PACKAGE = "new_package"
REASON_URL_INSTALL = "url_install"
REASON_PIPE_TO_SHELL = "pipe_to_shell"
REASON_BYPASS_FLAG = "bypass_flag"
REASON_UNPINNED = "unpinned"
REASON_ORDER = (
    REASON_NONEXISTENT,
    REASON_TYPOSQUAT,
    REASON_NEW_PACKAGE,
    REASON_URL_INSTALL,
    REASON_PIPE_TO_SHELL,
    REASON_BYPASS_FLAG,
    REASON_UNPINNED,
)

ALLOWLIST_HINT = "Allowlist: add the name to .ctx-guard/pkg-allowlist (see README#tuning)"

BYPASS_FLAGS = (
    "--force",
    "--yes",
    "-y",
    "--allow-unauthenticated",
    "--no-confirm",
)

PIPE_TO_SHELL = re.compile(
    r"(curl|wget)\b[^|]*\|\s*(sudo\s+(-\S+\s+)*)?(bash|sh|zsh|dash|ksh)\b"
    # an interpreter reading the script from stdin ("| python3 -"), but not
    # "| python3 -m json.tool", which only pretty-prints the download
    r"|(curl|wget)\b[^|]*\|\s*(sudo\s+(-\S+\s+)*)?(python3?|perl|ruby|node)(?=\s*(-(\s|$)|$|;|&|\|))"
    # bash <(curl ...) and sh -c "$(curl ...)"
    r"|\b(bash|sh|zsh)\s+(-c\s+)?[\"']?(\$\(|<\()\s*(curl|wget)\b"
)

# (ecosystem, regex matching the command head, single_package_only).
# group(1) = the argument tail. single_package_only is True for the
# run-and-execute forms (npx / pipx run / uvx): only the first positional is
# the package; everything after it is the wrapped tool's own argv.
_ECOSYSTEM_HEAD = [
    ("npm", re.compile(r"^(?:npm|pnpm|yarn|bun)\s+(?:install|i|add)(?=\s|$)\s*(.*)$"), False),
    ("npm", re.compile(r"^(?:npx|bunx)\s+(.*)$"), True),
    ("pip", re.compile(r"^(?:pip|pip3|uv\s+pip|python3?\s+-m\s+pip)\s+install(?=\s|$)\s*(.*)$"), False),
    ("pip", re.compile(r"^(?:uv|poetry)\s+add(?=\s|$)\s*(.*)$"), False),
    ("pip", re.compile(r"^pipx\s+install(?=\s|$)\s*(.*)$"), False),
    ("pip", re.compile(r"^pipx\s+run\s+(.*)$"), True),
    ("pip", re.compile(r"^uvx\s+(.*)$"), True),
    ("cargo", re.compile(r"^cargo\s+(?:install|add)(?=\s|$)\s*(.*)$"), False),
    ("gem", re.compile(r"^gem\s+install(?=\s|$)\s*(.*)$"), False),
    ("apt", re.compile(r"^(?:apt|apt-get)\s+install(?=\s|$)\s*(.*)$"), False),
    ("brew", re.compile(r"^brew\s+install(?=\s|$)\s*(.*)$"), False),
    ("apk", re.compile(r"^apk\s+add(?=\s|$)\s*(.*)$"), False),
]

_MANIFEST_FLAGS = {"-r", "--requirement"}

# Flags whose separate next token is a value (a URL, directory, workspace or
# feature name), not a package to look up: "npm install lodash -w api" must
# not look up "api". "--flag=value" forms are a single token and need no entry.
# pip -e and cargo --path/--git are deliberately absent: their value *is* the
# thing being installed and gets the URL/local-path warning.
_VALUE_FLAGS = {
    "npm": {"--registry", "--prefix", "--tag", "-w", "--workspace", "--filter",
            "-C", "--dir", "--cwd", "--omit", "--include", "--save-prefix",
            "--cache", "--userconfig"},
    "pip": {"-i", "--index-url", "--extra-index-url", "-c", "--constraint",
            "-t", "--target", "--prefix", "--root", "-f", "--find-links",
            "--python-version", "--platform", "--only-binary", "--no-binary",
            "--trusted-host", "--cache-dir", "--src", "--implementation",
            "--abi", "--progress-bar", "--upgrade-strategy", "--python", "-p",
            "-G", "--group", "--optional", "--extra", "-E", "--extras",
            "--source", "--index", "--default-index", "--directory",
            "--project", "--suffix"},
    "cargo": {"--root", "--target", "--target-dir", "--features", "-F",
              "--bin", "--example", "--registry", "--index", "--branch",
              "--tag", "--rev", "-j", "--jobs", "--profile", "--rename",
              "--package", "-p", "--manifest-path", "--config", "-Z"},
    "gem": {"-i", "--install-dir", "-n", "--bindir", "-s", "--source",
            "--platform", "-P", "--trust-policy", "-g", "--file"},
}

_URL_TOKEN = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git\+)", re.IGNORECASE)

# Local filesystem paths / archives are not registry-lookupable package names.
_LOCAL_PATH = re.compile(r"^(\.{1,2}(/|$)|/|~/)|\.(whl|tar\.gz|tgz|zip)$", re.IGNORECASE)

# npm specs that resolve outside the registry: github:/gitlab:/file: style
# prefixes and the bare "user/repo" GitHub shorthand (scoped names start
# with "@" and are excluded).
_NPM_NON_REGISTRY = re.compile(r"^(?:(?:github|gitlab|bitbucket|gist|git|file|link):|[^@/][^/]*/)", re.IGNORECASE)

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
    head: str = ""  # the matched command head, e.g. "npm install" / "npx"


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
        # poetry add requests@^2.31 / uv add requests@2.31
        at = token.find("@")
        if at > 0:
            return token[:at].strip(), True
        return token, False
    # cargo install/add ripgrep@14.1.0 and gem install rails:7.1.0 carry the
    # version inline; otherwise it comes from a separate -v/--version flag,
    # handled by the caller.
    sep = "@" if ecosystem == "cargo" else ":" if ecosystem == "gem" else None
    if sep and token.find(sep) > 0:
        return token[: token.find(sep)], True
    return token, False


def detect_install_command(cmd: str):
    """Return an InstallCommand, or None if cmd isn't install-shaped."""
    stripped = cmd.strip()
    for ecosystem, pattern, single_package_only in _ECOSYSTEM_HEAD:
        match = pattern.match(stripped)
        if not match:
            continue
        head = " ".join(stripped[: match.start(1)].split())
        tail = match.group(1).strip()
        tail = _COMPOUND_SEPARATOR.split(tail, maxsplit=1)[0]
        try:
            tokens = shlex.split(tail)
        except ValueError:
            tokens = tail.split()

        if not tokens:
            return InstallCommand(ecosystem=ecosystem, packages=[], manifest_only=True, head=head)

        manifest_only = any(t in _MANIFEST_FLAGS for t in tokens)

        version_flag_value = None
        if ecosystem in ("cargo", "gem"):
            for i, t in enumerate(tokens):
                if t in ("-v", "--version") and i + 1 < len(tokens):
                    version_flag_value = tokens[i + 1]

        value_flags = _VALUE_FLAGS.get(ecosystem, set())
        names = []
        skip_next = False
        for t in tokens:
            if skip_next:
                skip_next = False
                continue
            if t == "--":
                break  # gem/cargo: everything after "--" goes to the build
            if t.startswith("-"):
                skip_next = t in value_flags
                continue
            names.append(t)
        # drop the value that followed -v/--version (it isn't a package name)
        if version_flag_value is not None and version_flag_value in names:
            names.remove(version_flag_value)

        if manifest_only or not names:
            return InstallCommand(ecosystem=ecosystem, packages=[], manifest_only=True, head=head)

        if single_package_only:
            names = names[:1]

        packages = []
        for name in names:
            if _URL_TOKEN.match(name) or ("://" in name) or _LOCAL_PATH.search(name) \
                    or (ecosystem == "npm" and _NPM_NON_REGISTRY.match(name)):
                packages.append(PackageSpec(name=name, pinned=False, is_url=True))
                continue
            base_name, pinned = _split_name_and_pin(name, ecosystem)
            if ecosystem in ("cargo", "gem") and version_flag_value:
                pinned = True
            packages.append(PackageSpec(name=base_name, pinned=pinned, is_url=False))

        return InstallCommand(ecosystem=ecosystem, packages=packages, manifest_only=False, head=head)
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


def _canonical(name: str, ecosystem: str) -> str:
    """Registry-equivalent spelling: PyPI (PEP 503) and crates.io treat
    "-", "_" (and on PyPI ".") as the same character, so typing_extensions
    and typing-extensions are one package, not a typosquat pair."""
    lowered = name.lower()
    if ecosystem == "pip":
        return re.sub(r"[-_.]+", "-", lowered)
    if ecosystem == "cargo":
        return lowered.replace("_", "-")
    return lowered


def typosquat_match(name: str, ecosystem: str):
    popular = load_popular_packages(ecosystem)
    popular_lower = {_canonical(p, ecosystem) for p in popular}
    lowered = _canonical(name, ecosystem)
    # npm scopes are namespace-owned, so compare the bare name: "@babel/core"
    # is judged as "core", "@types/react" as "react" (exact -> not flagged).
    bare = lowered.split("/", 1)[-1] if lowered.startswith("@") else lowered
    if lowered in popular_lower or bare in popular_lower:
        return None
    if len(bare) < TYPOSQUAT_MIN_LENGTH:
        return None
    for candidate in popular:
        cand = _canonical(candidate, ecosystem)
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
class Finding:
    code: str  # one of REASON_ORDER
    package: str = None  # display-safe package name, None for whole-command findings
    detail: str = ""


@dataclasses.dataclass
class PkgCheckResult:
    action: str  # "block" | "warn" | "allow" ("allow" only from inspect_command)
    reason: str  # single-line message for the agent; see format_message
    codes: list = dataclasses.field(default_factory=list)  # reason codes, most severe first
    ecosystem: str = None
    package: str = None  # package behind the primary (first) reason code
    packages_checked: int = 0


_URL_USERINFO = re.compile(r"(://)[^/@\s]*@")


def safe_package_name(name: str) -> str:
    """A package spec that is safe to show and log: drop credentials and
    query strings from URL installs (git+https://user:token@host/...)."""
    if name is None:
        return None
    if "://" in name:
        name = _URL_USERINFO.sub(r"\1", name).split("?", 1)[0].split("#", 1)[0]
    return name[:200]


def _did_you_mean(name: str, ecosystem: str) -> str:
    match = typosquat_match(name, ecosystem)
    return f" (did you mean {match}?)" if match else ""


def format_message(action: str, head: str, names: list, findings: list, allowlist_hint: bool = True) -> str:
    """One line, e.g. "ctx-guard blocked npm install reqeusts: no such package
    on registry (did you mean requests?). Allowlist: ..." """
    verb = "blocked" if action == "block" else "warned"
    shown = [n for n in names if n]
    if len(shown) > 5:
        shown = shown[:5] + [f"+{len(names) - 5} more"]
    subject = " ".join([head] + shown).strip()
    multi = len([n for n in names if n]) > 1
    parts = []
    for f in findings:
        if multi and f.package:
            parts.append(f"'{f.package}' {f.detail}")
        else:
            parts.append(f.detail)
    line = f"ctx-guard {verb} {subject}: " + "; ".join(parts) + "."
    if allowlist_hint:
        line += " " + ALLOWLIST_HINT
    return " ".join(line.split())  # always a single line


def _result(action: str, findings: list, ecosystem: str, head: str, names: list) -> PkgCheckResult:
    codes = sorted({f.code for f in findings}, key=REASON_ORDER.index)
    primary = next((f for f in findings if codes and f.code == codes[0]), None)
    shown = findings if action != "block" else [f for f in findings if f.code == REASON_NONEXISTENT]
    reason = format_message(action, head, names, shown,
                            allowlist_hint=any(f.package for f in shown)) if findings else ""
    return PkgCheckResult(
        action=action,
        reason=reason,
        codes=codes,
        ecosystem=ecosystem,
        package=primary.package if primary else None,
        packages_checked=len(names),
    )


def inspect_command(cmd: str, cwd: str = None):
    """Check an install-shaped command. Returns a PkgCheckResult with action
    "allow", "warn" or "block", or None when the check is disabled or cmd is
    not an install of named packages."""
    if os.environ.get("CTX_GUARD_PKG_CHECK", "1") == "0":
        return None

    if has_pipe_to_shell(cmd):
        finding = Finding(
            code=REASON_PIPE_TO_SHELL,
            detail="pipes a remote script directly into a shell (curl|bash / wget|sh) -- review before running",
        )
        return _result("warn", [finding], ecosystem="shell", head="curl|sh", names=[])

    install_cmd = detect_install_command(cmd)
    if install_cmd is None or install_cmd.manifest_only or not install_cmd.packages:
        return None

    ecosystem = install_cmd.ecosystem
    names = [safe_package_name(p.name) for p in install_cmd.packages]
    allowlist = load_allowlist(cwd=cwd)
    findings = []
    blocked = False

    bypass_flag = has_bypass_flag(cmd)
    if bypass_flag:
        findings.append(Finding(REASON_BYPASS_FLAG, None,
                                f"command uses '{bypass_flag}', bypassing a normal confirmation prompt"))

    for pkg in install_cmd.packages:
        if is_allowlisted(pkg.name, allowlist):
            continue
        shown = safe_package_name(pkg.name)

        if pkg.is_url:
            findings.append(Finding(REASON_URL_INSTALL, shown,
                                    "is a git/URL-based install -- unverifiable source, not from a package registry"))
            continue

        if ecosystem not in REGISTRY_ECOSYSTEMS:
            continue  # apt/brew/apk: no registry, no meaningful version-pin concept

        if not pkg.pinned:
            findings.append(Finding(REASON_UNPINNED, shown, "has no version pin"))

        info = registry_lookup(ecosystem, pkg.name)

        if info is not None and not info.exists:
            blocked = True
            findings.append(Finding(REASON_NONEXISTENT, shown,
                                    f"no such package on registry{_did_you_mean(pkg.name, ecosystem)}"))
            continue

        if info is not None and info.age_days is not None and info.age_days < AGE_WARN_DAYS:
            findings.append(Finding(REASON_NEW_PACKAGE, shown,
                                    f"was published {info.age_days:.1f} days ago (< {AGE_WARN_DAYS}d)"))

        # The typosquat check needs no network, so it still runs when the
        # registry is unreachable (info is None) -- fail open on the lookup,
        # not on the offline signal.
        match = typosquat_match(pkg.name, ecosystem)
        if match:
            findings.append(Finding(REASON_TYPOSQUAT, shown,
                                    f"is within edit-distance {TYPOSQUAT_MAX_DISTANCE} of popular package "
                                    f"'{match}' -- possible typosquat (did you mean {match}?)"))

    action = "block" if blocked else "warn" if findings else "allow"
    return _result(action, findings, ecosystem, install_cmd.head, names)


def check_command(cmd: str, cwd: str = None):
    """Return a PkgCheckResult for a warn/block decision, or None to allow."""
    result = inspect_command(cmd, cwd=cwd)
    if result is None or result.action == "allow":
        return None
    return result
