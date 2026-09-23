import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
