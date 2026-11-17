#!/usr/bin/env bash
# ctx-guard curl installer. Each release publishes a copy of this file, with
# its version and the release tarball's SHA-256 filled in, to the
# ctx-guard/install repo:
#
#   curl -fsSL https://raw.githubusercontent.com/ctx-guard/install/main/get.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/ctx-guard/install/main/get.sh | bash -s -- --agents claude-code,codex-cli
#
# It downloads the release tarball, refuses to continue unless its SHA-256
# matches, then runs the tarball's install.sh with your arguments. To review
# first: download get.sh, read it, then run `bash get.sh`.
#
# Everything runs from main() on the last line, so a download cut off midway
# runs nothing.
set -euo pipefail

VERSION="${CTX_GUARD_VERSION:-@VERSION@}"
SHA256="${CTX_GUARD_SHA256:-@SHA256@}"
REPO="https://github.com/viktor-tapovski/ctx-guard"
TARBALL_URL="${CTX_GUARD_TARBALL_URL:-$REPO/releases/download/v$VERSION/ctx-guard-$VERSION.tar.gz}"

WORK_DIR=""  # global: the EXIT trap runs after main() has returned

fail() { echo "ctx-guard: $*" >&2; exit 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    fail "need sha256sum or shasum to verify the download"
  fi
}

main() {
  case "$VERSION$SHA256" in
    *@*) fail "this is the unreleased get.sh from the repository; use the one attached to a release" ;;
  esac
  for tool in curl tar; do
    command -v "$tool" >/dev/null 2>&1 || fail "need $tool"
  done

  WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ctx-guard-get-XXXXXXXX")"
  trap 'rm -rf "$WORK_DIR"' EXIT
  local tmp="$WORK_DIR"

  echo "Downloading ctx-guard $VERSION ..."
  curl -fsSL --proto '=https,file' "$TARBALL_URL" -o "$tmp/ctx-guard.tar.gz" || fail "download failed: $TARBALL_URL"

  local actual
  actual="$(sha256_of "$tmp/ctx-guard.tar.gz")"
  [ "$actual" = "$SHA256" ] || fail "checksum mismatch for $TARBALL_URL (expected $SHA256, got $actual); nothing was installed"
  echo "Checksum verified."

  tar -xzf "$tmp/ctx-guard.tar.gz" -C "$tmp"
  local install_sh
  install_sh="$(find "$tmp" -mindepth 2 -maxdepth 2 -name install.sh -type f | head -1)"
  [ -n "$install_sh" ] || fail "install.sh not found in the release tarball"

  # The agent checklist reads keys from /dev/tty, so it works even though
  # this script's stdin is the curl pipe.
  bash "$install_sh" "$@"
}

main "$@"
