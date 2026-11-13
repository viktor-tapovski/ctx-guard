# Benchmark methodology

ctx-guard should be evaluated with reproducible command fixtures rather than a
single headline savings percentage.

## What is measured

`ctx-guard-run` executes a command once and records:

- the raw command output bytes measured before redaction;
- the redacted output bytes archived and returned to the model;
- the resulting byte reduction;
- the command exit status.

`ctx-guard-stats --verbose` reports these values under **MEASURED** and
estimates tokens using the project's bytes-per-four heuristic. (Plain
`ctx-guard-stats` prints the same headline numbers as a one-line gauge.)

## What is observed

Targeted rewrites such as `git status` and `grep` are not run once in their
unbounded form for comparison. Their rewrite counts and delivered output
volume are reported under **OBSERVED**, not presented as measured token
savings.

## Reproduce a local measurement

```bash
export CTX_GUARD_LOG_DIR="$(mktemp -d)"
export CTX_GUARD_STATE_DIR="$(mktemp -d)"
export CTX_GUARD_STATS_FILE="$CTX_GUARD_STATE_DIR/stats.jsonl"

printf '#!/usr/bin/env bash\nfor i in $(seq 1 300); do echo line$i; done\n' \
  > /tmp/ctx-guard-benchmark.sh

bin/ctx-guard-run /tmp/ctx-guard-benchmark.sh
bin/ctx-guard-stats --stats-file "$CTX_GUARD_STATS_FILE" --json
rm -f /tmp/ctx-guard-benchmark.sh
```

For public examples, include the command, fixture, operating system, ctx-guard
commit, raw byte counts, returned byte counts, and limitations. Do not publish
real repository output or secrets.

## Package install guard

`tests/test_pkg_install_fixtures.py` runs 105 install commands from
`tests/fixtures/pkg_install/cases.jsonl` through `check_command` with
`registry_lookup` replaced by a dict-backed stub
(`tests/fixtures/pkg_install/registry.json`). Nothing touches the network; the
test fails if anything calls `urllib.request.urlopen`.

```bash
python3 tests/test_pkg_install_fixtures.py --report   # the table below
python3 -m unittest tests/test_pkg_install_fixtures.py  # the CI assertions
```

**Scoring.** A case is *detected* when the guard raises a signal about the
package itself: `nonexistent`, `typosquat`, `new_package`, `url_install` or
`pipe_to_shell`. "No version pin" and bypass-flag warnings fire on almost every
unpinned install, so they never count as a catch. Typosquat, nonexistent and
risky cases are positives. A legit case is a false positive when it is
detected or when the decision is stricter than its label (unpinned legit
installs are labelled `warn`, because warning on a missing pin is policy).
`offline` cases run with the registry unreachable and are not scored.

Measured at commit `11d64e1`, which added the fixture set (based on v0.6.0,
`f2ef879`).

```
By ecosystem
             cases   TP   FP   FN   TN    prec  recall  FP rate  decision==expected
cargo           15    7    0    1    7  100.0%   87.5%    0.0%  14/15
gem             14    6    0    1    7  100.0%   85.7%    0.0%  13/14
npm             35   17    0    0   17  100.0%  100.0%    0.0%  35/35
pip             34   16    0    0   16  100.0%  100.0%    0.0%  34/34
shell            7    7    0    0    0  100.0%  100.0%       -  7/7
ALL            105   53    0    2   47  100.0%   96.4%    0.0%  103/105

By category
             cases   TP   FP   FN   TN    prec  recall  FP rate  decision==expected
legit           47    0    0    0   47       -       -    0.0%  47/47
nonexistent     17   17    0    0    0  100.0%  100.0%       -  17/17
offline          3    0    0    0    0       -       -       -  3/3
risky           13   13    0    0    0  100.0%  100.0%       -  13/13
typosquat       25   23    0    2    0  100.0%   92.0%       -  23/25

Misses and decision mismatches
  FN  typosquat   expected=warn  actual=allow codes=-  cargo add rnad@0.8.5  [known gap: names under TYPOSQUAT_MIN_LENGTH (5) are never distance-checked]
  FN  typosquat   expected=warn  actual=allow codes=-  gem install rais -v 7.1.3  [known gap: names under TYPOSQUAT_MIN_LENGTH (5) are never distance-checked]
```

**Against the targets.**
- Nonexistent names: 17/17 recall (target 95%): met.
- Typosquats: 23/25 = 92% recall (target 95%): **not met.** Both misses are
  four-character names (`rnad`, `rais`). The checker skips names shorter than
  5 characters on purpose, because short names sit within two edits of many
  real packages (`toml`/`tomli`, `rack`/`rake`). Typosquats and nonexistent
  names together: 40/42 = 95.2%. The CI test asserts 95% on typosquats outside
  the two marked gaps, and fails if either gap closes so the numbers here get
  updated.
- Legit installs: 0/47 false positives (target 2% or lower): met, but see the
  next paragraph.

**Before and after.** The same fixtures scored against the v0.6.0 checker
gave 15/47 false positives (31.9%), typosquat recall 84%, nonexistent recall
88.2%, and risky-shape recall 76.9%. The fixes are:

- flag values are no longer read as package names (`-w`, `--filter`, `-c`,
  `-i`, `--features` and others);
- inline versions are parsed: `cargo install name@ver`, `gem install name:ver`,
  `poetry add name@ver`;
- `npm init` and `npm adduser` no longer match `npm i` / `npm add`;
- PyPI and crates.io names are compared after canonicalising them
  (`typing_extensions` = `typing-extensions`, `serde-json` = `serde_json`);
- `cargo add`, `uv add`, `uv pip install`, `poetry add`, `pipx install`,
  `python -m pip install`, `bun add` and `bunx` are now detected;
- npm `github:` and `user/repo` specs count as unverifiable sources;
- `| python3 -`, `bash <(curl …)` and `sh -c "$(curl …)"` count as
  pipe-to-shell;
- the offline typosquat check still runs when the registry is unreachable.

After those fixes, 9 false positives remained (19.1%). All were real, popular
packages within two edits of a listed name: `vitest`, `preact`, `bcryptjs` and
`globby` (npm), `black`, `scapy`, `numba` and `psycopg` (PyPI), and `faker`
(RubyGems). They were fixed by widening the
bundled popular lists from about 50 to about 100 names per ecosystem. That
fix was informed by these fixtures, so 0% overstates how the rule will do on
packages the fixtures do not cover. A fairer estimate is the leave-one-out
check: remove each name from its popular list and ask whether the rest would
flag it. 40 of 397 listed packages (10%) would be flagged, for example
`react`/`preact`, `httpx`/`httpie` and `tokio`/`tonic`. So a popular package
that is missing from the bundled list has roughly a 1-in-10 chance of a
typosquat warning. It is a warning, not a block, and the allowlist clears it.

**Limitations.**
- The fixtures are synthetic. The commands are written to look like real agent
  installs, and several typosquats follow publicly reported shapes
  (`mongose`, `jquerry`, `urlib3`, `python3-dateutil`), but they were not
  sampled from real agent sessions.
- The registry is stubbed. Which typosquats "exist" and every package age in
  `registry.json` are illustrative, not real registry data. In real use the
  `new_package` rule reads the latest version's publish time for npm, PyPI and
  RubyGems. That means a fresh release of an established package also warns,
  and this benchmark does not measure that.
- The popular lists are bundled snapshots written by hand. They are not live
  download rankings, and they go stale.
- A hallucinated name is only blocked when the registry answers. Offline, the
  guard fails open by design (see the `offline` cases).
