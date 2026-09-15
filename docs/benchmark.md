# Benchmark methodology

ctx-guard should be evaluated with reproducible command fixtures rather than a
single headline savings percentage.

## What is measured

`ctx-guard-run` executes a command once and records:

- the raw command output bytes measured before redaction;
- the redacted output bytes archived and returned to the model;
- the resulting byte reduction;
- the command exit status.

`ctx-guard-stats` reports these values under **MEASURED** and estimates tokens
using the project's bytes-per-four heuristic.

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
