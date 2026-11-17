#!/usr/bin/env bash
# Fill a release's version and tarball SHA-256 into get.sh and the Homebrew
# formula. Used by .github/workflows/release.yml, and by hand when the tap
# can't be updated automatically (see docs/releasing.md).
#
#   packaging/render.sh 1.2.3 <sha256 of ctx-guard-1.2.3.tar.gz> OUT_DIR
#
# Writes OUT_DIR/get.sh and OUT_DIR/ctx-guard.rb.
set -euo pipefail

[ $# -eq 3 ] || { echo "usage: $0 VERSION SHA256 OUT_DIR" >&2; exit 2; }
version="$1"
sha256="$2"
out="$3"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad version: $version" >&2; exit 2; }
[[ "$sha256" =~ ^[0-9a-f]{64}$ ]] || { echo "bad sha256: $sha256" >&2; exit 2; }

root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$out"
for pair in "get.sh:get.sh" "packaging/homebrew/ctx-guard.rb.tmpl:ctx-guard.rb"; do
  sed -e "s/@VERSION@/$version/g" -e "s/@SHA256@/$sha256/g" "$root/${pair%%:*}" > "$out/${pair##*:}"
done
chmod +x "$out/get.sh"
echo "Rendered $out/get.sh and $out/ctx-guard.rb for $version"
