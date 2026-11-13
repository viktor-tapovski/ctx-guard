"""
Fixture benchmark for the package-install guard (hooks/pkg_install_check.py).

Runs every command in tests/fixtures/pkg_install/cases.jsonl through the
checker with registry_lookup replaced by a dict-backed stub built from
tests/fixtures/pkg_install/registry.json, so nothing touches the network.

    python3 -m unittest tests/test_pkg_install_fixtures.py   # assertions
    python3 tests/test_pkg_install_fixtures.py --report       # numbers

Scoring. A case is "detected" when the guard raises a signal that is about
the package itself (REASON_SIGNALS below); "no version pin" and bypass-flag
warnings fire on almost every unpinned install, so they never count as a
catch. Typosquat, nonexistent and risky cases are positives; legit cases are
negatives, and a legit case is a false positive when it is detected or when
the decision is stricter than its expected label. "offline" cases run with
the registry unreachable and are reported separately.
"""
import json
import os
import sys
import tempfile
import unittest
from collections import defaultdict
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks"))

import pkg_install_check as pic  # noqa: E402

FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "pkg_install")
CASES_FILE = os.path.join(FIXTURE_DIR, "cases.jsonl")
REGISTRY_FILE = os.path.join(FIXTURE_DIR, "registry.json")

REASON_SIGNALS = {"nonexistent", "typosquat", "new_package", "url_install", "pipe_to_shell"}
POSITIVE = {"typosquat", "nonexistent", "risky"}
RANK = {"allow": 0, "warn": 1, "block": 2}

# Acceptance targets from founder/feature-roadmap.md (T1).
MIN_RECALL = 0.95
MAX_FP_RATE = 0.02


def load_cases():
    with open(CASES_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_registry():
    with open(REGISTRY_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {eco: names for eco, names in data.items() if not eco.startswith("_")}


def _canonical(name, ecosystem):
    canon = getattr(pic, "_canonical", None)
    return canon(name, ecosystem) if canon else name.lower()


def make_stub(registry, offline=False):
    tables = {eco: {_canonical(n, eco): age for n, age in names.items()}
              for eco, names in registry.items()}

    def stub(ecosystem, name):
        if offline:
            return None  # what registry_lookup returns on timeout / DNS failure
        age = tables.get(ecosystem, {}).get(_canonical(name, ecosystem))
        if age is None:
            return pic.RegistryInfo(exists=False)
        return pic.RegistryInfo(exists=True, age_days=float(age))
    return stub


def _codes(result):
    """Reason codes of a result; falls back to the reason text for checkers
    that predate PkgCheckResult.codes (used to score older versions)."""
    if result is None:
        return set()
    codes = set(getattr(result, "codes", None) or [])
    if codes:
        return codes
    text = result.reason.lower()
    for code, needle in (("nonexistent", "does not exist"), ("typosquat", "typosquat"),
                         ("new_package", "days ago"), ("url_install", "unverifiable"),
                         ("pipe_to_shell", "pipes a remote script")):
        if needle in text:
            codes.add(code)
    return codes


def evaluate(module=pic, cases=None):
    """Run every case through module.check_command; return per-case rows."""
    cases = load_cases() if cases is None else cases
    registry = load_registry()
    rows = []
    with tempfile.TemporaryDirectory() as home, \
            mock.patch.dict(os.environ, {"HOME": home, "CTX_GUARD_PKG_CHECK": "1"}), \
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("network access in fixture test")):
        for case in cases:
            stub = make_stub(registry, offline=case.get("registry_offline", False))
            with mock.patch.object(module, "registry_lookup", side_effect=stub):
                result = module.check_command(case["command"], cwd=home)
            actual = result.action if result is not None else "allow"
            codes = _codes(result)
            detected = bool(codes & REASON_SIGNALS)
            category = case["category"]
            outcome = None
            if category in POSITIVE:
                outcome = "tp" if detected else "fn"
            elif category == "legit":
                stricter = RANK[actual] > RANK[case["expected"]]
                outcome = "fp" if (detected or stricter) else "tn"
            rows.append({**case, "actual": actual, "codes": sorted(codes),
                         "detected": detected, "outcome": outcome,
                         "message": result.reason if result is not None else ""})
    return rows


def _ratio(num, den):
    return num / den if den else None


def summarize(rows):
    def tally(selected):
        c = defaultdict(int)
        for r in selected:
            if r["outcome"]:
                c[r["outcome"]] += 1
        tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
        return {"cases": len(selected), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                "fp_rate": _ratio(fp, fp + tn),
                "decision_match": sum(1 for r in selected if r["actual"] == r["expected"])}

    by_eco = defaultdict(list)
    by_cat = defaultdict(list)
    for r in rows:
        by_eco[r["ecosystem"]].append(r)
        by_cat[r["category"]].append(r)
    return {"all": tally(rows),
            "by_ecosystem": {k: tally(v) for k, v in sorted(by_eco.items())},
            "by_category": {k: tally(v) for k, v in sorted(by_cat.items())}}


def print_report(rows, out=sys.stdout):
    s = summarize(rows)
    pct = lambda x: "     -" if x is None else f"{100 * x:5.1f}%"  # noqa: E731

    def line(label, t):
        out.write(f"{label:12s} {t['cases']:5d} {t['tp']:4d} {t['fp']:4d} {t['fn']:4d} {t['tn']:4d}"
                  f"  {pct(t['precision'])}  {pct(t['recall'])}  {pct(t['fp_rate'])}"
                  f"  {t['decision_match']}/{t['cases']}\n")

    header = (f"{'':12s} {'cases':>5s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'TN':>4s}"
              f"  {'prec':>6s}  {'recall':>6s}  {'FP rate':>6s}  decision==expected\n")
    out.write("Package install guard -- fixture benchmark (registry stubbed, no network)\n\n")
    out.write("By ecosystem\n" + header)
    for eco, t in s["by_ecosystem"].items():
        line(eco, t)
    line("ALL", s["all"])
    out.write("\nBy category\n" + header)
    for cat, t in s["by_category"].items():
        line(cat, t)
    misses = [r for r in rows if r["outcome"] in ("fn", "fp") or r["actual"] != r["expected"]]
    if misses:
        out.write("\nMisses and decision mismatches\n")
        for r in misses:
            tag = (r["outcome"] or "-").upper()
            gap = f"  [known gap: {r['known_gap']}]" if r.get("known_gap") else ""
            out.write(f"  {tag:3s} {r['category']:11s} expected={r['expected']:5s} actual={r['actual']:5s}"
                      f" codes={','.join(r['codes']) or '-'}  {r['command']}{gap}\n")


class TestPkgInstallFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = evaluate()
        cls.summary = summarize(cls.rows)

    def test_fixture_has_minimum_case_counts(self):
        cats = self.summary["by_category"]
        self.assertGreaterEqual(cats["legit"]["cases"], 40)
        self.assertGreaterEqual(cats["typosquat"]["cases"], 20)
        self.assertGreaterEqual(cats["nonexistent"]["cases"], 15)
        self.assertGreaterEqual(cats["risky"]["cases"], 10)
        for eco in ("npm", "pip", "cargo", "gem"):
            legit = [r for r in self.rows if r["category"] == "legit" and r["ecosystem"] == eco]
            self.assertTrue(legit, eco)

    def test_every_case_matches_expected_decision_except_known_gaps(self):
        wrong = [f"{r['command']!r}: expected {r['expected']}, got {r['actual']} ({r['message']})"
                 for r in self.rows if r["actual"] != r["expected"] and not r.get("known_gap")]
        self.assertEqual(wrong, [])

    def test_known_gaps_are_still_gaps(self):
        # If one of these starts passing, the checker improved: drop the
        # known_gap marker in cases.jsonl and update docs/benchmark.md.
        fixed = [r["command"] for r in self.rows if r.get("known_gap") and r["actual"] == r["expected"]]
        self.assertEqual(fixed, [])

    def test_false_positive_rate_on_legit_installs(self):
        legit = self.summary["by_category"]["legit"]
        self.assertLessEqual(legit["fp_rate"], MAX_FP_RATE)

    def test_recall_on_nonexistent_names(self):
        self.assertGreaterEqual(self.summary["by_category"]["nonexistent"]["recall"], MIN_RECALL)

    def test_recall_on_typosquats_outside_known_gaps(self):
        rows = [r for r in self.rows if r["category"] == "typosquat" and not r.get("known_gap")]
        caught = sum(1 for r in rows if r["detected"])
        self.assertGreaterEqual(_ratio(caught, len(rows)), MIN_RECALL)

    def test_risky_shapes_all_detected(self):
        self.assertEqual(self.summary["by_category"]["risky"]["fn"], 0)

    def test_block_message_is_single_line_with_suggestion(self):
        row = next(r for r in self.rows if r["command"] == "pip install reqeusts")
        self.assertEqual(row["message"],
                         "ctx-guard blocked pip install reqeusts: no such package on registry "
                         "(did you mean requests?). " + pic.ALLOWLIST_HINT)


if __name__ == "__main__":
    if "--report" in sys.argv:
        print_report(evaluate())
    else:
        unittest.main()
