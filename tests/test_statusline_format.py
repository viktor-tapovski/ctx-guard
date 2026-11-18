import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks", "lib"))

import statusline_format as sf  # noqa: E402

STATUSLINE = os.path.join(REPO_ROOT, "bin", "ctx-guard-statusline")
# 2.0k (~1.0k) today, 13.0k (~1.0k) total, in tokens (bytes/4).
TOTALS = {"today": [8000, 4000], "total": [52000, 4000], "pkg_flagged": 5, "redacted": 2}
SESSION = {"model": {"display_name": "Opus"}, "cwd": "/work/app",
           "context_window": {"used_percentage": 72}}


def opts(config=None, agent="claude-code"):
    return sf.resolve(config or {}, agent)


def seg(config=None, totals=TOTALS, session=None, agent="claude-code"):
    info = sf.session_info(session) if session else {}
    return sf.format_segment(totals, info, agent, opts(config, agent))


class TestTemplate(unittest.TestCase):

    def test_default_reproduces_todays_segment(self):
        self.assertEqual(seg(), "ctx-guard ⬇ today 2.0k (~1.0k) · total 13.0k (~1.0k)")
        no_est = {"today": [8000, 0], "total": [52000, 0]}
        self.assertEqual(seg(totals=no_est), "ctx-guard ⬇ today 2.0k · total 13.0k")

    def test_every_field(self):
        template = " ".join(f"{name}={{{name}}}" for name in sorted(sf.FIELDS))
        out = seg({"template": template}, session=SESSION)
        for expected in ("today=2.0k", "total=13.0k", "today_est=1.0k", "total_est=1.0k",
                         "today_all=3.0k", "total_all=14.0k", "today_pair=2.0k (~1.0k)",
                         "pkg_flagged=5", "redacted=2", "model=Opus", "context_pct=72%",
                         "context_bar=███████░░░", "cwd=/work/app", "agent=claude-code"):
            self.assertIn(expected, out)

    def test_cwd_under_home_is_shortened(self):
        home = os.path.expanduser("~")
        info = sf.session_info({"workspace": {"current_dir": home + "/src"}})
        self.assertEqual(info["cwd"], "~/src")

    def test_context_from_token_counts(self):
        info = sf.session_info({"context_window": {
            "context_window_size": 200000,
            "current_usage": {"input_tokens": 50000, "cache_read_input_tokens": 50000,
                              "output_tokens": 999}}})
        self.assertEqual(info["context_pct"], "50%")

    def test_session_fields_missing_render_empty(self):
        self.assertEqual(seg({"template": "[{model}|{context_pct}|{cwd}] {today}"}),
                         "[||] 2.0k")
        self.assertEqual(sf.session_info("not a dict"), {})

    def test_literal_braces(self):
        self.assertEqual(seg({"template": "{{{today}}}"}), "{2.0k}")

    def test_unknown_field_and_stray_brace_rejected(self):
        for bad in ("{nope}", "{today", "today}", "{today.__class__}", "{0}", "{}"):
            with self.assertRaises(ValueError, msg=bad):
                sf.parse(bad)

    def test_no_attribute_access(self):
        with self.assertRaises(ValueError):
            opts({"template": "{agent.__class__.__init__.__globals__}"})


class TestOptions(unittest.TestCase):

    def test_estimated_hide(self):
        self.assertEqual(seg({"estimated": "hide"}), "ctx-guard ⬇ today 2.0k · total 13.0k")
        self.assertEqual(seg({"estimated": "hide", "template": "{today_all}|{today_est}"}),
                         "2.0k|")

    def test_units_bytes(self):
        self.assertEqual(seg({"units": "bytes", "template": "{today} {total_pair}"}),
                         "7.8KB 50.8KB (~3.9KB)")

    def test_hide_when_zero(self):
        self.assertEqual(seg(totals=None), "")
        self.assertEqual(seg({"hide_when_zero": False}, totals=None),
                         "ctx-guard ⬇ today 0 · total 0")
        # Only the count fields the template uses decide.
        security_only = {"today": [0, 0], "total": [0, 0], "pkg_flagged": 1, "redacted": 0}
        self.assertEqual(seg(totals=security_only), "")
        self.assertEqual(seg({"template": "🛡 {pkg_flagged}"}, totals=security_only), "🛡 1")
        # A template with no count field never hides.
        self.assertEqual(seg({"template": "{agent}"}, totals=None), "claude-code")

    def test_color_name(self):
        self.assertEqual(seg({"color": "cyan", "template": "{today}"}), "\033[36m2.0k\033[0m")
        self.assertEqual(seg({"color": "none", "template": "{today}"}), "2.0k")

    def test_color_threshold(self):
        def at(pct):
            return seg({"color": "threshold", "template": "{today}"},
                       session={"context_window": {"used_percentage": pct}})
        self.assertEqual(at(20), "2.0k")
        self.assertEqual(at(50), "\033[32m2.0k\033[0m")
        self.assertEqual(at(70), "\033[33m2.0k\033[0m")
        self.assertEqual(at(85), "\033[31m2.0k\033[0m")
        self.assertEqual(seg({"color": "threshold", "template": "{today}"}), "2.0k")

    def test_separator_option(self):
        self.assertEqual(opts()["separator"], " │ ")
        self.assertEqual(opts({"separator": " | "})["separator"], " | ")

    def test_agent_override_applies_to_that_agent_only(self):
        config = {"preset": "minimal", "color": "red",
                  "agents": {"copilot-cli": {"template": "{agent}: {today}", "color": "none"}}}
        self.assertEqual(seg(config, agent="copilot-cli"), "copilot-cli: 2.0k")
        self.assertEqual(seg(config, agent="claude-code"), "\033[31m⬇ 3.0k\033[0m")
        self.assertEqual(seg({"agents": {"codex-cli": {"preset": "minimal"}}}),
                         seg())

    def test_override_preset_beats_top_level_template(self):
        config = {"template": "{agent}", "agents": {"codex-cli": {"preset": "minimal"}}}
        self.assertEqual(seg(config, agent="codex-cli"), "⬇ 3.0k")

    def test_invalid_configs_raise(self):
        for bad in ([], {"preset": "nope"}, {"template": "{nope}"}, {"estimated": "maybe"},
                    {"units": "furlongs"}, {"separator": 1}, {"hide_when_zero": "yes"},
                    {"color": "mauve"}, {"agents": []}, {"agents": {"x": {"agents": {}}}},
                    {"bogus": 1}, {"template": 5}):
            with self.assertRaises(ValueError, msg=bad):
                sf.resolve(bad, "claude-code")


class TestPresets(unittest.TestCase):

    def test_preset_outputs(self):
        self.assertEqual(seg({"preset": "default"}),
                         "ctx-guard ⬇ today 2.0k (~1.0k) · total 13.0k (~1.0k)")
        self.assertEqual(seg({"preset": "minimal"}), "⬇ 3.0k")
        self.assertEqual(seg({"preset": "security"}), "⬇ 3.0k · 🛡 5")
        self.assertEqual(seg({"preset": "full"}, session=SESSION),
                         "Opus ███████░░░ 72% · ⬇ today 2.0k (~1.0k) · total 13.0k (~1.0k)")
        self.assertEqual(seg({"preset": "powerline"}),
                         "ctx-guard  ⬇ today 2.0k (~1.0k)  total 13.0k (~1.0k)")
        self.assertEqual(opts({"preset": "powerline"})["separator"], "  ")

    def test_full_shows_before_any_savings_unless_overridden(self):
        self.assertTrue(seg({"preset": "full"}, totals=None, session=SESSION).startswith("Opus"))
        self.assertEqual(seg({"preset": "full", "hide_when_zero": True}, totals=None), "")

    def test_every_preset_parses(self):
        for name in sf.PRESETS:
            sf.parse(sf.PRESETS[name]["template"])


class TestConfigFile(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "ctx-guard", "statusline.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_is_default(self):
        self.assertEqual(sf.load_options("claude-code", self.path), opts())

    def test_unreadable_or_bad_json_raises(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        with self.assertRaises(ValueError):
            sf.load_options("claude-code", self.path)

    def test_write_keeps_other_options_and_uninstall_removes_only_created(self):
        manifest = {}
        sf.write_config(manifest, preset="minimal", path=self.path)
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"preset": "minimal"})
        self.assertTrue(manifest["statusline_config"]["created"])
        with open(self.path, "w") as f:
            json.dump({"preset": "minimal", "color": "red"}, f)
        sf.write_config(manifest, template="⬇ {today}", path=self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"color": "red", "template": "⬇ {today}"})
        self.assertTrue(manifest["statusline_config"]["created"])  # still ours
        with contextlib.redirect_stdout(io.StringIO()):
            sf.remove_config(manifest)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))

    def test_preexisting_file_is_kept_on_uninstall(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"units": "bytes"}, f)
        manifest = {}
        sf.write_config(manifest, preset="security", path=self.path)
        self.assertFalse(manifest["statusline_config"]["created"])
        with contextlib.redirect_stdout(io.StringIO()):
            sf.remove_config(manifest)
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"units": "bytes", "preset": "security"})

    def test_changed_created_file_is_kept(self):
        manifest = {}
        sf.write_config(manifest, preset="minimal", path=self.path)
        with open(self.path, "w") as f:
            f.write('{"preset": "full"}')
        with contextlib.redirect_stdout(io.StringIO()):
            sf.remove_config(manifest)
        self.assertTrue(os.path.exists(self.path))

    def test_write_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            sf.write_config({}, preset="nope", path=self.path)
        with self.assertRaises(ValueError):
            sf.write_config({}, template="{nope}", path=self.path)
        self.assertFalse(os.path.exists(self.path))


class TestStatuslineScript(unittest.TestCase):
    """bin/ctx-guard-statusline end to end, from an install-like copy so the
    manifest next to it can chain a previous status line."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.dest = os.path.join(self.root, "dest")
        os.makedirs(os.path.join(self.dest, "bin"))
        shutil.copy(STATUSLINE, os.path.join(self.dest, "bin"))
        shutil.copytree(os.path.join(REPO_ROOT, "hooks", "lib"),
                        os.path.join(self.dest, "hooks", "lib"))
        self.script = os.path.join(self.dest, "bin", "ctx-guard-statusline")
        self.stats = os.path.join(self.root, "stats.jsonl")
        self.config_home = os.path.join(self.root, "config")
        now = time.time()
        with open(self.stats, "w") as f:
            for ev in ({"kind": "ctx_guard_run", "original_bytes": 8400, "returned_bytes": 400,
                        "redacted": 3},
                       {"kind": "rewrite_estimate", "est_saved_bytes": 4000},
                       {"kind": "pkg_guard", "decision": "block"},
                       {"kind": "pkg_guard", "decision": "allow"},
                       {"kind": "pkg_warned"},
                       {"kind": "ctx_guard_run", "original_bytes": 400, "returned_bytes": 400,
                        "redacted": 0}):
                f.write(json.dumps({"ts": now, "agent": "claude-code", **ev}) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_line(self, *args, stdin="", env=None):
        environ = {k: v for k, v in os.environ.items()
                   if not k.startswith("CTX_GUARD")}
        environ.update(CTX_GUARD_STATS_FILE=self.stats, XDG_CONFIG_HOME=self.config_home,
                       **(env or {}))
        result = subprocess.run([sys.executable, self.script, *args], input=stdin.encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environ, timeout=30)
        self.assertEqual(result.stderr, b"")
        return result.stdout.decode("utf-8")

    def write_config(self, config):
        os.makedirs(os.path.join(self.config_home, "ctx-guard"), exist_ok=True)
        with open(os.path.join(self.config_home, "ctx-guard", "statusline.json"), "w") as f:
            f.write(config if isinstance(config, str) else json.dumps(config))

    def chain(self, command):
        with open(os.path.join(self.dest, "install-manifest.json"), "w") as f:
            json.dump({"statusline": {"claude-code": {
                "previous": {"type": "command", "command": command}}}}, f)

    def test_no_config_output_is_pinned(self):
        self.assertEqual(self.run_line("claude-code"),
                         "ctx-guard ⬇ today 2.0k (~1.0k) · total 2.0k (~1.0k)\n")

    def test_bad_config_falls_back_to_default(self):
        default = self.run_line("claude-code")
        for bad in ("{not json", '{"preset": "nope"}', '{"template": "{nope}"}',
                    '{"color": 3}', "[]"):
            self.write_config(bad)
            self.assertEqual(self.run_line("claude-code", "--segment"), default, bad)

    def test_rollup_counts_security_events(self):
        self.write_config({"template": "{pkg_flagged} {redacted}"})
        self.assertEqual(self.run_line("claude-code"), "2 1\n")
        with open(os.path.splitext(self.stats)[0] + ".rollup.json") as f:
            rollup = json.load(f)
        self.assertEqual(rollup["agents"]["claude-code"]["pkg_flagged"], 2)
        self.assertEqual(rollup["offset"], os.path.getsize(self.stats))

    def test_session_fields_from_stdin(self):
        self.write_config({"preset": "full"})
        self.assertEqual(self.run_line("claude-code", stdin=json.dumps(SESSION)),
                         "Opus ███████░░░ 72% · ⬇ today 2.0k (~1.0k) · total 2.0k (~1.0k)\n")

    def test_separator_between_chained_and_ours(self):
        self.chain("echo base")
        self.write_config({"separator": " :: ", "preset": "minimal"})
        self.assertEqual(self.run_line("claude-code"), "base :: ⬇ 3.0k\n")

    def test_segment_never_chains(self):
        self.chain("echo base")
        self.assertEqual(self.run_line("claude-code", "--segment"),
                         "ctx-guard ⬇ today 2.0k (~1.0k) · total 2.0k (~1.0k)\n")

    def test_segment_inside_chained_script_prints_once(self):
        user_script = os.path.join(self.root, "statusline.sh")
        with open(user_script, "w") as f:
            f.write(f'#!/bin/sh\ninput=$(cat)\nprintf "base %s" "$(printf %s "$input" | wc -c '
                    f'| tr -d " ")"\nprintf " | %s" "$("{sys.executable}" "{self.script}" '
                    f'claude-code --segment)"\n')
        self.chain(f"sh {user_script}")
        segment = "ctx-guard ⬇ today 2.0k (~1.0k) · total 2.0k (~1.0k)"
        out = self.run_line("claude-code", stdin="{}")
        self.assertEqual(out.count("ctx-guard ⬇"), 1)
        self.assertEqual(out, f"base 2 |  │ {segment}\n")
        # Run directly by the agent (ctx-guard's line replaced), it embeds ours.
        direct = subprocess.run(["sh", user_script], input=b"{}", stdout=subprocess.PIPE,
                                env={**os.environ, "CTX_GUARD_STATS_FILE": self.stats,
                                     "XDG_CONFIG_HOME": self.config_home}, timeout=30)
        self.assertEqual(direct.stdout.decode(), f"base 2 | {segment}")

    def test_chaining_ourselves_never_loops(self):
        self.chain(f"{sys.executable} {self.script} claude-code")
        out = self.run_line("claude-code")
        self.assertEqual(out, "ctx-guard ⬇ today 2.0k (~1.0k) · total 2.0k (~1.0k)\n")

    def test_marker_silences_any_run(self):
        out = self.run_line("claude-code", "--segment",
                            env={"CTX_GUARD_STATUSLINE_CHAINING": "1"})
        self.assertEqual(out, "")

    def test_preview_lists_every_preset(self):
        self.write_config({"preset": "security"})
        out = self.run_line("--preview")
        for name in sf.PRESETS:
            self.assertIn(f"  {name} ", out)
        self.assertIn("⬇ 3.0k · 🛡 2", out)
        self.assertIn("yours: ⬇ 3.0k · 🛡 2", out)
        self.write_config("{bad")
        self.assertIn("can't be used", self.run_line("--preview", "claude-code"))


if __name__ == "__main__":
    unittest.main()
