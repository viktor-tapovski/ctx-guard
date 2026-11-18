# Releasing

release-please opens a release PR from the Conventional Commits on `main`.
Merging it tags the release, and `.github/workflows/release.yml` then:

1. Builds `ctx-guard-X.Y.Z.tar.gz` with `git archive` from the tag. Paths
   marked `export-ignore` in `.gitattributes` (tests, CI, packaging) are left out.
2. Runs `packaging/render.sh` to fill the version and the tarball's SHA-256
   into `get.sh` and the Homebrew formula.
3. Attaches the tarball, `get.sh` and `SHA256SUMS` to the GitHub release.
4. Publishes the installers to the `ctx-guard` GitHub organization:
   - `get.sh` to [ctx-guard/install](https://github.com/ctx-guard/install), served as
     `https://raw.githubusercontent.com/ctx-guard/install/main/get.sh`
   - the formula to [ctx-guard/homebrew-tap](https://github.com/ctx-guard/homebrew-tap)
     as `Formula/ctx-guard.rb`, installed with `brew install ctx-guard/tap/ctx-guard`

## One-time setup

- Create the GitHub organization `ctx-guard` with two public repos:
  `install` and `homebrew-tap`. Give `homebrew-tap` a `Formula/` directory.
- Create a fine-grained token, owned by the `ctx-guard` organization (the
  organization must allow fine-grained tokens), with **Contents: read and
  write** on those two repos only. Save it in this repo as the Actions secret
  `INSTALLERS_TOKEN`. Without it, step 4 is skipped with a warning.

## Publishing by hand

When step 4 was skipped or failed:

```bash
gh release download vX.Y.Z -p 'ctx-guard-X.Y.Z.tar.gz' -D /tmp/rel
packaging/render.sh X.Y.Z "$(shasum -a 256 /tmp/rel/ctx-guard-X.Y.Z.tar.gz | cut -d' ' -f1)" /tmp/rel
cp /tmp/rel/ctx-guard.rb ../homebrew-tap/Formula/ctx-guard.rb
cp /tmp/rel/get.sh ../install/get.sh
(cd ../homebrew-tap && git commit -am "ctx-guard X.Y.Z" && git push)
(cd ../install && git commit -am "ctx-guard X.Y.Z" && git push)
```

## Checking a release

```bash
brew update && brew install ctx-guard/tap/ctx-guard && brew test ctx-guard
curl -fsSL https://raw.githubusercontent.com/ctx-guard/install/main/get.sh | bash -s -- --yes
```

## Plain `brew install ctx-guard`

That needs the formula in homebrew-core, which requires (as of 2026-11-17,
https://docs.brew.sh/Package-Acceptance-Policy) a repo at least 30 days old
with at least 75 stars, 30 forks or 30 watchers when someone else submits
it, or 225 stars, 90 forks or 90 watchers when the owner submits it, plus a
stable tagged release and a working `livecheck`. Until then, the tap is the
Homebrew route.
