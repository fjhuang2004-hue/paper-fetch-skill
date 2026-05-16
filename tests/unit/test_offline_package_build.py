from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_OFFLINE_PACKAGE = REPO_ROOT / "scripts" / "build-offline-package.sh"
BUILD_OFFLINE_PACKAGE_WINDOWS = REPO_ROOT / "scripts" / "build-offline-package-windows.ps1"
VERIFY_OFFLINE_PACKAGE = REPO_ROOT / "scripts" / "verify-offline-package.sh"


class OfflinePackageBuildTests(unittest.TestCase):
    def test_linux_package_build_excludes_local_legacy_backup_and_requires_cloakbrowser_wheel(self) -> None:
        script = BUILD_OFFLINE_PACKAGE.read_text(encoding="utf-8")

        self.assertIn("--exclude='./legacy'", script)
        self.assertIn("cloakbrowser-*.whl", script)
        self.assertIn("Dependency wheelhouse is missing cloakbrowser-*.whl", script)
        self.assertNotIn("-m playwright install chromium", script)

    def test_linux_manifest_and_readme_document_cloakbrowser_binary_policy(self) -> None:
        script = BUILD_OFFLINE_PACKAGE.read_text(encoding="utf-8")
        manifest_block = script[script.index("payload = {") : script.index("(staging / \"offline-manifest.json\")")]

        self.assertIn('"cloakbrowser"', manifest_block)
        self.assertIn('"browser_binary": "not_bundled"', manifest_block)
        self.assertIn("README.offline.md", script)
        self.assertIn("CLOAKBROWSER_BINARY_PATH", script)
        self.assertNotIn('"playwright_browsers"', manifest_block)

    def test_linux_offline_verifier_uses_cloakbrowser_smoke(self) -> None:
        script = VERIFY_OFFLINE_PACKAGE.read_text(encoding="utf-8")

        self.assertIn("import cloakbrowser", script)
        self.assertIn('assert hasattr(cloakbrowser, "launch")', script)
        self.assertIn("CLOAKBROWSER_HEADLESS=true", script)
        self.assertNotIn("sessions.list", script)
        self.assertNotIn("playwright.sync_api", script)

    def test_windows_package_build_excludes_local_legacy_backup_and_playwright_bundles(self) -> None:
        script = BUILD_OFFLINE_PACKAGE_WINDOWS.read_text(encoding="utf-8")

        self.assertIn('Join-Path $RepoDir "legacy"', script)
        self.assertIn('Get-ChildItem -Path $wheelhouse -Filter "cloakbrowser-*.whl"', script)
        self.assertIn('browser_binary = "not_bundled"', script)
        self.assertIn("Write-OfflineReadme", script)
        self.assertNotIn("Add-PlaywrightChromium", script)
        self.assertNotIn("-m playwright install chromium", script)

    def test_windows_wrappers_and_manifest_publish_only_cloakbrowser_runtime(self) -> None:
        script = BUILD_OFFLINE_PACKAGE_WINDOWS.read_text(encoding="utf-8")
        wrapper_block = script[script.index("function Write-CmdWrappers") : script.index("function Add-SkillAgentManifest")]
        manifest_block = script[script.index("components = [ordered]@{") : script.index("installer = [ordered]@{")]

        self.assertIn("paper-fetch.cmd", wrapper_block)
        self.assertIn("paper-fetch-mcp.cmd", wrapper_block)
        self.assertIn("cloakbrowser = [ordered]@{", manifest_block)
        self.assertNotIn("playwright_browsers", manifest_block)


if __name__ == "__main__":
    unittest.main()
