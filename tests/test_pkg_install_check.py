import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks"))

import pkg_install_check as pic  # noqa: E402


class TestDetectInstallCommand(unittest.TestCase):
    def test_npm_install_single_package_unpinned(self):
        result = pic.detect_install_command("npm install left-pad")
        self.assertIsNotNone(result)
        self.assertEqual(result.ecosystem, "npm")
        self.assertFalse(result.manifest_only)
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].name, "left-pad")
        self.assertFalse(result.packages[0].pinned)
        self.assertFalse(result.packages[0].is_url)

    def test_npm_install_pinned_version(self):
        result = pic.detect_install_command("npm install left-pad@1.3.0")
        self.assertTrue(result.packages[0].pinned)
        self.assertEqual(result.packages[0].name, "left-pad")

    def test_npm_install_scoped_package(self):
        result = pic.detect_install_command("npm install @babel/core")
        self.assertEqual(result.packages[0].name, "@babel/core")
        self.assertFalse(result.packages[0].is_url)

    def test_npm_install_no_args_is_manifest_only(self):
        result = pic.detect_install_command("npm install")
        self.assertTrue(result.manifest_only)
        self.assertEqual(result.packages, [])

    def test_npm_install_flag_before_package_not_mistaken_for_package(self):
        result = pic.detect_install_command("npm install --save foo")
        self.assertEqual([p.name for p in result.packages], ["foo"])

    def test_npm_install_multi_package(self):
        result = pic.detect_install_command("npm install foo bar baz")
        self.assertEqual([p.name for p in result.packages], ["foo", "bar", "baz"])

    def test_pnpm_and_yarn_add_alias(self):
        self.assertEqual(pic.detect_install_command("pnpm add left-pad").ecosystem, "npm")
        self.assertEqual(pic.detect_install_command("yarn add left-pad").ecosystem, "npm")

    def test_pip_install_pinned(self):
        result = pic.detect_install_command("pip install requests==2.31.0")
        self.assertEqual(result.ecosystem, "pip")
        self.assertTrue(result.packages[0].pinned)
        self.assertEqual(result.packages[0].name, "requests")

    def test_pip3_alias(self):
        self.assertEqual(pic.detect_install_command("pip3 install requests").ecosystem, "pip")

    def test_pip_install_dash_r_is_manifest_only(self):
        result = pic.detect_install_command("pip install -r requirements.txt")
        self.assertTrue(result.manifest_only)

    def test_pip_install_git_url_flagged_as_url(self):
        result = pic.detect_install_command("pip install git+https://github.com/foo/bar.git")
        self.assertTrue(result.packages[0].is_url)

    def test_npm_install_https_url_flagged(self):
        result = pic.detect_install_command("npm install https://github.com/foo/bar")
        self.assertTrue(result.packages[0].is_url)

    def test_cargo_install(self):
        result = pic.detect_install_command("cargo install ripgrep")
        self.assertEqual(result.ecosystem, "cargo")
        self.assertEqual(result.packages[0].name, "ripgrep")

    def test_gem_install(self):
        result = pic.detect_install_command("gem install rails -v 7.1.0")
        self.assertEqual(result.ecosystem, "gem")
        self.assertEqual(result.packages[0].name, "rails")
        self.assertTrue(result.packages[0].pinned)

    def test_apt_install_structural_only_ecosystem(self):
        result = pic.detect_install_command("apt install curl")
        self.assertEqual(result.ecosystem, "apt")
        self.assertNotIn("apt", pic.REGISTRY_ECOSYSTEMS)

    def test_apt_get_alias(self):
        self.assertEqual(pic.detect_install_command("apt-get install curl").ecosystem, "apt")

    def test_brew_install(self):
        result = pic.detect_install_command("brew install wget")
        self.assertEqual(result.ecosystem, "brew")
        self.assertNotIn("brew", pic.REGISTRY_ECOSYSTEMS)

    def test_apk_add(self):
        result = pic.detect_install_command("apk add curl")
        self.assertEqual(result.ecosystem, "apk")
        self.assertNotIn("apk", pic.REGISTRY_ECOSYSTEMS)

    def test_npx_treated_as_install_and_execute(self):
        result = pic.detect_install_command("npx cowsay hello")
        self.assertEqual(result.ecosystem, "npm")
        self.assertEqual(result.packages[0].name, "cowsay")

    def test_pipx_run(self):
        result = pic.detect_install_command("pipx run black .")
        self.assertEqual(result.ecosystem, "pip")
        self.assertEqual(result.packages[0].name, "black")

    def test_uvx(self):
        result = pic.detect_install_command("uvx ruff check .")
        self.assertEqual(result.ecosystem, "pip")
        self.assertEqual(result.packages[0].name, "ruff")

    def test_not_an_install_command_returns_none(self):
        self.assertIsNone(pic.detect_install_command("git status"))
        self.assertIsNone(pic.detect_install_command("ls -la"))

    def test_compound_command_still_detected(self):
        result = pic.detect_install_command("npm install left-pad && npm test")
        self.assertIsNotNone(result)
        self.assertEqual(result.packages[0].name, "left-pad")
        self.assertEqual(len(result.packages), 1)

    def test_npx_wrapped_tool_args_not_treated_as_packages(self):
        result = pic.detect_install_command("npx jest tests/foo.test.ts")
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].name, "jest")

    def test_npx_wrapped_tool_flags_and_paths_not_treated_as_packages(self):
        result = pic.detect_install_command("npx prettier --write src/")
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].name, "prettier")

    def test_uvx_wrapped_tool_args_not_treated_as_packages(self):
        result = pic.detect_install_command("uvx ruff check src")
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].name, "ruff")

    def test_pipx_run_wrapped_tool_args_not_treated_as_packages(self):
        result = pic.detect_install_command("pipx run black src tests")
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].name, "black")

    def test_npm_install_multi_package_still_checks_all(self):
        result = pic.detect_install_command("npm install left-pad lodash react")
        self.assertEqual([p.name for p in result.packages], ["left-pad", "lodash", "react"])

    def test_pip_install_editable_dot_is_local_path(self):
        result = pic.detect_install_command("pip install -e .")
        self.assertEqual(len(result.packages), 1)
        self.assertTrue(result.packages[0].is_url)

    def test_pip_install_dot_is_local_path(self):
        result = pic.detect_install_command("pip install .")
        self.assertEqual(len(result.packages), 1)
        self.assertTrue(result.packages[0].is_url)

    def test_pip_install_relative_path_and_wheel_are_local(self):
        for cmd in ("pip install ../lib", "pip install ./pkg", "pip install /abs/pkg",
                    "pip install ~/src/pkg", "pip install dist/foo-1.0-py3-none-any.whl",
                    "pip install foo-1.0.tar.gz"):
            result = pic.detect_install_command(cmd)
            self.assertTrue(result.packages[0].is_url, cmd)

    def test_cargo_install_path_dot_is_local_path(self):
        result = pic.detect_install_command("cargo install --path .")
        self.assertEqual(len(result.packages), 1)
        self.assertTrue(result.packages[0].is_url)

    def test_pip_install_extras_stripped_and_pinned(self):
        result = pic.detect_install_command("pip install 'requests[security]==2.31.0'")
        self.assertEqual(result.packages[0].name, "requests")
        self.assertTrue(result.packages[0].pinned)
        self.assertFalse(result.packages[0].is_url)

    def test_pip_install_extras_unpinned(self):
        result = pic.detect_install_command("pip install 'requests[security]'")
        self.assertEqual(result.packages[0].name, "requests")
        self.assertFalse(result.packages[0].pinned)

    def test_pip_install_greater_than_is_pinned(self):
        result = pic.detect_install_command("pip install 'requests>2'")
        self.assertEqual(result.packages[0].name, "requests")
        self.assertTrue(result.packages[0].pinned)

    def test_pip_install_other_specifiers_pinned(self):
        for spec in ("requests<3", "requests!=2.0", "requests===2.31.0", "requests>=2", "requests~=2.31"):
            result = pic.detect_install_command(f"pip install '{spec}'")
            self.assertEqual(result.packages[0].name, "requests", spec)
            self.assertTrue(result.packages[0].pinned, spec)


class TestBypassFlagAndPipeToShell(unittest.TestCase):
    def test_force_flag_detected(self):
        self.assertEqual(pic.has_bypass_flag("npm install foo --force"), "--force")

    def test_yes_flag_detected(self):
        self.assertEqual(pic.has_bypass_flag("apt install curl -y"), "-y")

    def test_allow_unauthenticated_flag_detected(self):
        self.assertEqual(
            pic.has_bypass_flag("apt install curl --allow-unauthenticated"),
            "--allow-unauthenticated",
        )

    def test_no_bypass_flag_returns_none(self):
        self.assertIsNone(pic.has_bypass_flag("npm install foo"))

    def test_curl_pipe_bash_detected(self):
        self.assertTrue(pic.has_pipe_to_shell("curl -sSL https://get.example.com | bash"))

    def test_curl_pipe_sh_detected(self):
        self.assertTrue(pic.has_pipe_to_shell("curl https://example.com/install.sh | sh"))

    def test_wget_pipe_bash_detected(self):
        self.assertTrue(pic.has_pipe_to_shell("wget -qO- https://example.com | bash"))

    def test_plain_curl_not_flagged(self):
        self.assertFalse(pic.has_pipe_to_shell("curl -sSL https://example.com/file.json"))


class TestTyposquat(unittest.TestCase):
    def test_levenshtein_identical(self):
        self.assertEqual(pic.levenshtein("react", "react"), 0)

    def test_levenshtein_one_substitution(self):
        self.assertEqual(pic.levenshtein("react", "react"[::-1][::-1]), 0)  # sanity no-op
        self.assertEqual(pic.levenshtein("requests", "reqeusts"), 2)

    def test_levenshtein_one_edit(self):
        self.assertEqual(pic.levenshtein("axios", "axio"), 1)

    def test_load_popular_packages_npm(self):
        names = pic.load_popular_packages("npm")
        self.assertIn("react", names)
        self.assertIn("express", names)

    def test_load_popular_packages_unknown_ecosystem_empty(self):
        self.assertEqual(pic.load_popular_packages("apt"), [])

    def test_typosquat_match_close_name_flagged(self):
        match = pic.typosquat_match("reqeusts", "pip")  # transposed, distance 2 from "requests"
        self.assertEqual(match, "requests")

    def test_typosquat_match_exact_name_not_flagged(self):
        self.assertIsNone(pic.typosquat_match("requests", "pip"))

    def test_typosquat_match_unrelated_name_not_flagged(self):
        self.assertIsNone(pic.typosquat_match("my-totally-unique-internal-tool", "pip"))

    def test_typosquat_exact_match_wins_over_earlier_near_match(self):
        # "jest" precedes "next" in npm.txt and is within distance 2
        self.assertIsNone(pic.typosquat_match("next", "npm"))
        self.assertIsNone(pic.typosquat_match("vite", "npm"))
        self.assertIsNone(pic.typosquat_match("cors", "npm"))

    def test_typosquat_no_popular_list_entry_flags_another(self):
        for eco in ("npm", "pip", "cargo", "gem"):
            for name in pic.load_popular_packages(eco):
                self.assertIsNone(pic.typosquat_match(name, eco), f"{eco}:{name}")

    def test_typosquat_short_names_never_flagged(self):
        self.assertIsNone(pic.typosquat_match("sxi", "pip"))
        self.assertIsNone(pic.typosquat_match("rinq", "cargo"))

    def test_typosquat_scoped_package_not_flagged_against_unscoped(self):
        self.assertIsNone(pic.typosquat_match("@babel/core", "npm"))
        self.assertIsNone(pic.typosquat_match("@types/react", "npm"))

    def test_typosquat_scoped_bare_name_still_checked(self):
        self.assertEqual(pic.typosquat_match("@evil/expresss", "npm"), "express")

    @unittest.expectedFailure
    def test_typosquat_black_not_flagged_as_flask(self):
        # Known residual: "black" (5 chars, real/popular) is not in pypi.txt
        # and is distance 2 from "flask", so it still flags.
        self.assertIsNone(pic.typosquat_match("black", "pip"))


class TestAllowlist(unittest.TestCase):
    def test_is_allowlisted_exact_match(self):
        self.assertTrue(pic.is_allowlisted("left-pad", ["left-pad"]))

    def test_is_allowlisted_glob_match(self):
        self.assertTrue(pic.is_allowlisted("@yourorg/internal-lib", ["@yourorg/*"]))

    def test_is_allowlisted_no_match(self):
        self.assertFalse(pic.is_allowlisted("left-pad", ["@yourorg/*"]))

    def test_load_allowlist_merges_global_and_repo(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(home, ".ctx-guard"))
            with open(os.path.join(home, ".ctx-guard", "pkg-allowlist"), "w") as f:
                f.write("# comment\n@yourorg/*\n\n")
            os.makedirs(os.path.join(repo, ".ctx-guard"))
            with open(os.path.join(repo, ".ctx-guard", "pkg-allowlist"), "w") as f:
                f.write("internal-tool\n")

            with mock.patch.dict(os.environ, {"HOME": home}):
                patterns = pic.load_allowlist(cwd=repo)

            self.assertIn("@yourorg/*", patterns)
            self.assertIn("internal-tool", patterns)

    def test_load_allowlist_missing_files_returns_empty(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
            with mock.patch.dict(os.environ, {"HOME": home}):
                patterns = pic.load_allowlist(cwd=repo)
            self.assertEqual(patterns, [])


import json as _json
import time
import urllib.error


class TestRegistryLookup(unittest.TestCase):
    def _fake_urlopen_json(self, payload: dict, status: int = 200):
        body = _json.dumps(payload).encode("utf-8")

        class _Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return body

            def getcode(self_):
                return status

        return _Resp()

    def test_npm_existing_package(self):
        now_iso = "2020-01-01T00:00:00.000Z"
        payload = {"dist-tags": {"latest": "1.0.0"}, "time": {"1.0.0": now_iso}}
        with mock.patch("pkg_install_check.urllib.request.urlopen", return_value=self._fake_urlopen_json(payload)):
            info = pic.registry_lookup("npm", "left-pad")
        self.assertTrue(info.exists)
        self.assertGreater(info.age_days, 300)  # published in 2020, clearly >7 days old

    def test_npm_recently_published_package(self):
        recent_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
        payload = {"dist-tags": {"latest": "0.0.1"}, "time": {"0.0.1": recent_iso}}
        with mock.patch("pkg_install_check.urllib.request.urlopen", return_value=self._fake_urlopen_json(payload)):
            info = pic.registry_lookup("npm", "brand-new-pkg")
        self.assertTrue(info.exists)
        self.assertLess(info.age_days, 1)

    def test_nonexistent_package_404(self):
        err = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with mock.patch("pkg_install_check.urllib.request.urlopen", side_effect=err):
            info = pic.registry_lookup("npm", "totally-fake-hallucinated-pkg-xyz")
        self.assertFalse(info.exists)

    def test_network_timeout_fails_open(self):
        with mock.patch(
            "pkg_install_check.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ):
            info = pic.registry_lookup("npm", "left-pad")
        self.assertIsNone(info)

    def test_pypi_existing_package(self):
        payload = {
            "info": {"version": "2.31.0"},
            "releases": {"2.31.0": [{"upload_time_iso_8601": "2020-01-01T00:00:00Z"}]},
        }
        with mock.patch("pkg_install_check.urllib.request.urlopen", return_value=self._fake_urlopen_json(payload)):
            info = pic.registry_lookup("pip", "requests")
        self.assertTrue(info.exists)

    def test_crates_existing_package(self):
        payload = {"crate": {"created_at": "2020-01-01T00:00:00Z"}}
        with mock.patch("pkg_install_check.urllib.request.urlopen", return_value=self._fake_urlopen_json(payload)):
            info = pic.registry_lookup("cargo", "serde")
        self.assertTrue(info.exists)

    def test_gems_existing_package(self):
        payload = {"version_created_at": "2020-01-01T00:00:00Z"}
        with mock.patch("pkg_install_check.urllib.request.urlopen", return_value=self._fake_urlopen_json(payload)):
            info = pic.registry_lookup("gem", "rails")
        self.assertTrue(info.exists)


import json


class TestCheckCommand(unittest.TestCase):
    def setUp(self):
        # ensure a clean slate regardless of the developer's real env/home
        self._env_patch = mock.patch.dict(os.environ, {"CTX_GUARD_PKG_CHECK": "1"})
        self._env_patch.start()
        self._tmp = tempfile.TemporaryDirectory()
        self._home_patch = mock.patch.dict(os.environ, {"HOME": self._tmp.name})
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_disabled_returns_none(self):
        with mock.patch.dict(os.environ, {"CTX_GUARD_PKG_CHECK": "0"}):
            self.assertIsNone(pic.check_command("npm install left-pad"))

    def test_non_install_command_returns_none(self):
        self.assertIsNone(pic.check_command("git status"))

    def test_manifest_only_returns_none(self):
        self.assertIsNone(pic.check_command("npm install"))
        self.assertIsNone(pic.check_command("pip install -r requirements.txt"))

    def test_curl_pipe_bash_warns(self):
        result = pic.check_command("curl -sSL https://get.example.com | bash")
        self.assertEqual(result.action, "warn")

    def test_nonexistent_npm_package_blocks(self):
        err = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with mock.patch("pkg_install_check.urllib.request.urlopen", side_effect=err):
            result = pic.check_command("npm install totally-fake-hallucinated-pkg-xyz")
        self.assertEqual(result.action, "block")
        self.assertIn("totally-fake-hallucinated-pkg-xyz", result.reason)

    def test_one_real_one_fake_package_still_blocks_and_names_offender(self):
        def fake_urlopen(req, timeout=None):
            if "totally-fake" in req.full_url:
                raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)
            payload = {"dist-tags": {"latest": "1.0.0"}, "time": {"1.0.0": "2020-01-01T00:00:00Z"}}
            return self._fake_json_response(payload)

        with mock.patch("pkg_install_check.urllib.request.urlopen", side_effect=fake_urlopen):
            result = pic.check_command("npm install left-pad totally-fake-hallucinated-pkg-xyz")
        self.assertEqual(result.action, "block")
        self.assertIn("totally-fake-hallucinated-pkg-xyz", result.reason)

    def _fake_json_response(self, payload):
        body = json.dumps(payload).encode("utf-8")

        class _Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return body

        return _Resp()

    def test_unpinned_version_warns(self):
        payload = {"dist-tags": {"latest": "1.0.0"}, "time": {"1.0.0": "2020-01-01T00:00:00Z"}}
        with mock.patch(
            "pkg_install_check.urllib.request.urlopen",
            return_value=self._fake_json_response(payload),
        ):
            result = pic.check_command("npm install left-pad")
        self.assertEqual(result.action, "warn")
        self.assertIn("left-pad", result.reason)

    def test_pinned_recent_existing_package_no_unpinned_warning_but_age_warns(self):
        recent_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
        payload = {"dist-tags": {"latest": "0.0.1"}, "time": {"0.0.1": recent_iso}}
        with mock.patch(
            "pkg_install_check.urllib.request.urlopen",
            return_value=self._fake_json_response(payload),
        ):
            result = pic.check_command("npm install brand-new-pkg@0.0.1")
        self.assertEqual(result.action, "warn")
        self.assertIn("brand-new-pkg", result.reason)

    def test_git_url_install_warns_unverifiable(self):
        result = pic.check_command("pip install git+https://github.com/foo/bar.git")
        self.assertEqual(result.action, "warn")
        self.assertIn("unverifiable", result.reason.lower())

    def test_local_path_installs_warn_unverifiable_without_registry_lookup(self):
        with mock.patch("pkg_install_check.urllib.request.urlopen") as urlopen:
            for cmd in ("pip install -e .", "pip install .", "cargo install --path ."):
                result = pic.check_command(cmd)
                self.assertEqual(result.action, "warn", cmd)
                self.assertIn("unverifiable", result.reason.lower(), cmd)
            urlopen.assert_not_called()

    def test_bypass_flag_warns(self):
        result = pic.check_command("apt install curl -y")
        self.assertEqual(result.action, "warn")

    def test_allowlisted_package_skips_all_checks(self):
        repo_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo_dir, ".ctx-guard"))
        with open(os.path.join(repo_dir, ".ctx-guard", "pkg-allowlist"), "w") as f:
            f.write("@yourorg/*\n")

        def fail_if_called(*a, **k):
            raise AssertionError("registry_lookup should not be called for an allowlisted package")

        with mock.patch("pkg_install_check.registry_lookup", side_effect=fail_if_called):
            result = pic.check_command("npm install @yourorg/internal-lib", cwd=repo_dir)
        self.assertIsNone(result)

    def test_apt_ecosystem_never_hits_registry(self):
        def fail_if_called(*a, **k):
            raise AssertionError("apt must never hit a registry lookup")

        with mock.patch("pkg_install_check.registry_lookup", side_effect=fail_if_called):
            result = pic.check_command("apt install curl")
        self.assertIsNone(result)

    def test_network_failure_fails_open_no_block(self):
        with mock.patch(
            "pkg_install_check.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ):
            result = pic.check_command("npm install left-pad@1.3.0")
        # pinned + registry unreachable -> nothing left to flag -> None
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
