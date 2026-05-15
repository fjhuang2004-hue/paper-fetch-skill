from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
LINUX_INSTALLER = REPO_ROOT / "install-offline.sh"
WINDOWS_INSTALLER = REPO_ROOT / "install-offline.ps1"
WINDOWS_INSTALLER_HELPER = REPO_ROOT / "scripts" / "windows-installer-helper.ps1"


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_file(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_recording_cli(path: Path, log_path: Path, *, exit_code: int = 0) -> None:
    _write_executable(
        path,
        f"""\
        #!/usr/bin/env bash
        {{
          printf '%s' "$(basename "$0")"
          for arg in "$@"; do
            printf '\\t%s' "$arg"
          done
          printf '\\n'
        }} >> "{log_path}"
        exit {exit_code}
        """,
    )


def _python_tag(version: str) -> str:
    major, minor, _micro = version.split(".")
    return f"cp{major}{minor}"


def _fake_python_script(version: str) -> str:
    tag = _python_tag(version)
    return f"""\
    #!/usr/bin/env bash
    set -euo pipefail
    VERSION="{version}"
    TAG="{tag}"

    if [[ "${{1:-}}" == "-c" ]]; then
      code="${{2:-}}"
      if [[ "$code" == *'join(map(str, sys.version_info[:3]))'* ]]; then
        echo "$VERSION"
        exit 0
      fi
      if [[ "$code" == *'cp{{sys.version_info.major}}{{sys.version_info.minor}}'* ]]; then
        echo "$TAG"
        exit 0
      fi
      if [[ "$code" == *'json.load'* && "$code" == *'python_tag'* ]]; then
        manifest="${{3:-}}"
        if [[ -f "$manifest" ]]; then
          grep -oE '"python_tag"[[:space:]]*:[[:space:]]*"[^"]+"' "$manifest" | head -n 1 | sed -E 's/.*"python_tag"[[:space:]]*:[[:space:]]*"([^"]+)".*/\\1/'
        fi
        exit 0
      fi
      if [[ "$code" == *'installer_manifest_values'* ]]; then
        cat <<'OUT'
    installer_manifest_values
    # BEGIN paper-fetch offline managed
    # END paper-fetch offline managed
    # BEGIN paper-fetch installer managed
    # END paper-fetch installer managed
    paper-fetch-skill
    paper-fetch
    PYTHONUTF8
    PYTHONIOENCODING
    PAPER_FETCH_ENV_FILE
    PAPER_FETCH_MCP_PYTHON_BIN
    PAPER_FETCH_DOWNLOAD_DIR
    PAPER_FETCH_FORMULA_TOOLS_DIR
    PLAYWRIGHT_BROWSERS_PATH
    FLARESOLVERR_URL
    FLARESOLVERR_ENV_FILE
    FLARESOLVERR_SOURCE_DIR
    OUT
        exit 0
      fi
      if [[ "$code" == *'cloakbrowser'* ]]; then
        exit 0
      fi
      exit 0
    fi

    if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
      venv_dir="$3"
      mkdir -p "$venv_dir/bin"
      cp "$0" "$venv_dir/bin/python"
      chmod +x "$venv_dir/bin/python"
      cat > "$venv_dir/bin/paper-fetch" <<'SH'
    #!/usr/bin/env bash
    if [[ "${1:-}" == "--help" ]]; then
      exit 0
    fi
    exit 0
    SH
      chmod +x "$venv_dir/bin/paper-fetch"
      cat > "$venv_dir/bin/paper-fetch-mcp" <<'SH'
    #!/usr/bin/env bash
    exit 0
    SH
      chmod +x "$venv_dir/bin/paper-fetch-mcp"
      exit 0
    fi

    if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "pip" ]]; then
      exit 0
    fi

    exit 0
    """


def _write_checksums(root: Path) -> None:
    lines: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "sha256sums.txt"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(root).as_posix()
        lines.append(f"{digest}  ./{relative}\n")
    (root / "sha256sums.txt").write_text("".join(lines), encoding="utf-8")


class OfflineInstallTests(unittest.TestCase):
    def _create_bundle(
        self,
        root: Path,
        *,
        python_version: str = "3.11.9",
        manifest_python_tag: str | None = None,
    ) -> tuple[Path, Path, Path]:
        bundle = root / "bundle"
        bundle.mkdir()
        shutil.copy2(REPO_ROOT / "install-offline.sh", bundle / "install-offline.sh")
        (bundle / "install-offline.sh").chmod(0o755)
        shutil.copytree(REPO_ROOT / "installer", bundle / "installer")

        manifest_python_tag = manifest_python_tag or _python_tag(python_version)
        _write_file(
            bundle / "offline-manifest.json",
            f'{{"target": {{"platform": "linux", "arch": "x86_64", "python_tag": "{manifest_python_tag}"}}}}\n',
        )
        _write_file(bundle / ".env.example", 'ELSEVIER_API_KEY=""\n')
        _write_file(bundle / "dist" / "paper_fetch_skill-1.4.1-py3-none-any.whl")
        _write_file(bundle / "wheelhouse" / "cloakbrowser-0.3.28-py3-none-any.whl")
        _write_file(bundle / "wheelhouse" / "dependency-1.0.0-py3-none-any.whl")
        _write_file(bundle / "skills" / "paper-fetch-skill" / "SKILL.md", "# Paper fetch skill\n")
        _write_file(
            bundle / "skills" / "paper-fetch-skill" / "references" / "tool-contract.md",
            "Tool contract\n",
        )
        _write_executable(bundle / "formula-tools" / "bin" / "texmath", "#!/usr/bin/env bash\nexit 0\n")

        fake_bin = root / "fake-bin"
        _write_executable(fake_bin / "python3", _fake_python_script(python_version))

        _write_checksums(bundle)
        home = root / "home"
        home.mkdir()
        return bundle, fake_bin, home

    def _run_installer(
        self,
        bundle: Path,
        fake_bin: Path,
        home: Path,
        *args: str,
        shell: str | None = "/bin/bash",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"
        env["PAPER_FETCH_OFFLINE_PYTHON_BIN"] = str(fake_bin / "python3")
        if shell is None:
            env.pop("SHELL", None)
        else:
            env["SHELL"] = shell
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(bundle / "install-offline.sh"), "--skip-smoke", *args],
            cwd=bundle,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_default_install_writes_cloakbrowser_env_without_legacy_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle, fake_bin, home = self._create_bundle(Path(tmpdir))
            user_env = home / ".config" / "paper-fetch" / ".env"
            _write_file(user_env, 'ELSEVIER_API_KEY="secret"\n')

            result = self._run_installer(bundle, fake_bin, home)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(user_env.read_text(encoding="utf-8"), 'ELSEVIER_API_KEY="secret"\n')
            offline_env = (bundle / "offline.env").read_text(encoding="utf-8")
            self.assertIn('CLOAKBROWSER_HEADLESS="true"', offline_env)
            self.assertIn("CLOAKBROWSER_BINARY_PATH", offline_env)
            self.assertNotIn("FLARESOLVERR", offline_env)
            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", offline_env)
            self.assertIn("CloakBrowser headless: true", result.stdout)

    def test_shell_startup_blocks_use_cloakbrowser_headless(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle, fake_bin, home = self._create_bundle(Path(tmpdir))

            bash_result = self._run_installer(bundle, fake_bin, home, shell="/bin/bash")
            fish_result = self._run_installer(bundle, fake_bin, home, shell="/usr/bin/fish")

            self.assertEqual(bash_result.returncode, 0, bash_result.stderr)
            self.assertEqual(fish_result.returncode, 0, fish_result.stderr)
            bashrc = (home / ".bashrc").read_text(encoding="utf-8")
            fish_config = (home / ".config" / "fish" / "conf.d" / "paper-fetch-offline.fish").read_text(encoding="utf-8")
            self.assertIn(f'export PAPER_FETCH_ENV_FILE="{bundle / "offline.env"}"', bashrc)
            self.assertIn('export CLOAKBROWSER_HEADLESS="true"', bashrc)
            self.assertIn(f'set -gx PAPER_FETCH_ENV_FILE "{bundle / "offline.env"}"', fish_config)
            self.assertIn('set -gx CLOAKBROWSER_HEADLESS "true"', fish_config)
            self.assertNotIn("FLARESOLVERR", bashrc + fish_config)
            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", bashrc + fish_config)

    def test_wslg_preset_sets_headful_cloakbrowser_and_requires_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle, fake_bin, home = self._create_bundle(Path(tmpdir))

            missing_display = self._run_installer(
                bundle,
                fake_bin,
                home,
                "--preset=wslg",
                extra_env={"DISPLAY": "", "WAYLAND_DISPLAY": ""},
            )
            self.assertNotEqual(missing_display.returncode, 0)
            self.assertIn("DISPLAY or WAYLAND_DISPLAY is required", missing_display.stderr)

            result = self._run_installer(bundle, fake_bin, home, "--preset=wslg", extra_env={"DISPLAY": ":0"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('CLOAKBROWSER_HEADLESS="false"', (bundle / "offline.env").read_text(encoding="utf-8"))

    def test_cli_registration_uses_cloakbrowser_env_and_filters_legacy_manifest_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, fake_bin, home = self._create_bundle(root)
            cli_log = root / "cli.log"
            _write_recording_cli(fake_bin / "codex", cli_log)
            _write_recording_cli(fake_bin / "claude", cli_log)
            _write_recording_cli(fake_bin / "gemini", cli_log)

            result = self._run_installer(bundle, fake_bin, home)

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [line.split("\t") for line in cli_log.read_text(encoding="utf-8").splitlines()]
            codex_add = next(call for call in calls if call[:3] == ["codex", "mcp", "add"])
            self.assertIn(f"PAPER_FETCH_ENV_FILE={bundle / 'offline.env'}", codex_add)
            self.assertIn(f"PAPER_FETCH_MCP_PYTHON_BIN={bundle / '.venv' / 'bin' / 'python'}", codex_add)
            self.assertIn("CLOAKBROWSER_HEADLESS=true", codex_add)
            self.assertFalse(any("FLARESOLVERR" in arg for arg in codex_add))
            self.assertFalse(any("PLAYWRIGHT_BROWSERS_PATH" in arg for arg in codex_add))

    def test_missing_codex_cli_writes_config_toml_without_legacy_runtime_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle, fake_bin, home = self._create_bundle(Path(tmpdir))

            result = self._run_installer(bundle, fake_bin, home)

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
            self.assertIn("# BEGIN paper-fetch installer managed", config)
            self.assertIn("[mcp_servers.paper-fetch]", config)
            self.assertIn(f'PAPER_FETCH_ENV_FILE = "{bundle / "offline.env"}"', config)
            self.assertIn('CLOAKBROWSER_HEADLESS = "true"', config)
            self.assertNotIn("FLARESOLVERR", config)
            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", config)

    def test_reuse_env_file_keeps_file_untouched_and_activate_script_sets_cloakbrowser_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, fake_bin, home = self._create_bundle(root)
            reused_env = root / "shared" / "offline.env"
            reused_payload = 'ELSEVIER_API_KEY="secret"\n'
            _write_file(reused_env, reused_payload)

            result = self._run_installer(bundle, fake_bin, home, "--reuse-env-file", str(reused_env))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(reused_env.read_text(encoding="utf-8"), reused_payload)
            self.assertFalse((bundle / "offline.env").exists())

            probe = subprocess.run(
                [
                    "bash",
                    "-lc",
                    (
                        f'source "{bundle / "activate-offline.sh"}"; '
                        'printf "%s\\n%s\\n%s\\n" '
                        '"$PAPER_FETCH_ENV_FILE" "$PAPER_FETCH_DOWNLOAD_DIR" "$CLOAKBROWSER_HEADLESS"'
                    ),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(probe.stdout.splitlines(), [str(reused_env), str(bundle / "downloads"), "true"])

    def test_uninstall_removes_user_level_integrations_without_deleting_bundle_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle, fake_bin, home = self._create_bundle(root)
            cli_log = root / "cli.log"
            _write_recording_cli(fake_bin / "codex", cli_log)
            _write_recording_cli(fake_bin / "claude", cli_log)
            _write_recording_cli(fake_bin / "gemini", cli_log)

            _write_file(bundle / "offline.env", 'ELSEVIER_API_KEY="secret"\n')
            _write_file(bundle / ".venv" / "bin" / "paper-fetch", "installed\n")
            _write_file(home / ".codex" / "skills" / "paper-fetch-skill" / "SKILL.md", "codex\n")
            managed = textwrap.dedent(
                """
                # BEGIN paper-fetch offline managed
                export PAPER_FETCH_ENV_FILE="/old/offline.env"
                # END paper-fetch offline managed
                """
            ).lstrip()
            _write_file(home / ".bashrc", f"keep bash before\n{managed}keep bash after\n")

            result = self._run_installer(bundle, fake_bin, home, "--uninstall")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(bundle.exists())
            self.assertEqual((bundle / "offline.env").read_text(encoding="utf-8"), 'ELSEVIER_API_KEY="secret"\n')
            self.assertFalse((home / ".codex" / "skills" / "paper-fetch-skill").exists())
            self.assertEqual((home / ".bashrc").read_text(encoding="utf-8"), "keep bash before\nkeep bash after\n")
            calls = [line.split("\t") for line in cli_log.read_text(encoding="utf-8").splitlines()]
            self.assertIn(["codex", "mcp", "remove", "paper-fetch"], calls)
            self.assertFalse(any(call[:3] == ["codex", "mcp", "add"] for call in calls))
            self.assertIn("Bundle files were left in place", result.stdout)

    def test_matching_manifest_and_interpreter_tags_are_accepted(self) -> None:
        for python_version, python_tag in (("3.11.9", "cp311"), ("3.12.7", "cp312"), ("3.13.3", "cp313")):
            with self.subTest(python_tag=python_tag), tempfile.TemporaryDirectory() as tmpdir:
                bundle, fake_bin, home = self._create_bundle(
                    Path(tmpdir),
                    python_version=python_version,
                    manifest_python_tag=python_tag,
                )

                result = self._run_installer(bundle, fake_bin, home)

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_mismatched_manifest_and_interpreter_tag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle, fake_bin, home = self._create_bundle(
                Path(tmpdir),
                python_version="3.12.1",
                manifest_python_tag="cp313",
            )

            result = self._run_installer(bundle, fake_bin, home)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bundle requires CPython cp313", result.stderr)
            self.assertIn("detected Python 3.12.1 (cp312)", result.stderr)

    def test_installers_do_not_call_playwright_browser_install_or_flaresolverr_wrappers(self) -> None:
        linux_script = LINUX_INSTALLER.read_text(encoding="utf-8")
        windows_script = WINDOWS_INSTALLER.read_text(encoding="utf-8")

        combined = linux_script + windows_script
        self.assertNotIn("python -m playwright install chromium", combined)
        self.assertNotIn("-m playwright install chromium", combined)
        self.assertNotIn("install_flaresolverr_venv", combined)
        self.assertNotIn("setup_flaresolverr_source", combined)
        self.assertNotIn("flaresolverr-up", combined)
        self.assertIn("cloakbrowser.ensure_runtime()", combined)

    def test_windows_installer_helper_uses_cloakbrowser_smoke_and_optional_launch_probe(self) -> None:
        script = WINDOWS_INSTALLER_HELPER.read_text(encoding="utf-8")

        self.assertIn("[switch]$ProbeLaunch", script)
        self.assertIn("import cloakbrowser", script)
        self.assertIn('assert hasattr(cloakbrowser, "launch")', script)
        self.assertIn("CLOAKBROWSER_BINARY_PATH", script)
        self.assertIn("probe-launch", script)
        self.assertNotIn("sessions.list", script)
        self.assertNotIn("playwright.sync_api", script)
        self.assertNotIn("FLARESOLVERR_SOURCE_DIR =", script)

    def test_windows_offline_installer_declares_cloakbrowser_env_without_legacy_runtime_paths(self) -> None:
        script = WINDOWS_INSTALLER.read_text(encoding="utf-8")

        self.assertIn("CLOAKBROWSER_HEADLESS", script)
        self.assertIn("CLOAKBROWSER_BINARY_PATH", script)
        self.assertIn("Test-CloakBrowserPackage", script)
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH =", script)
        self.assertNotIn("FLARESOLVERR_SOURCE_DIR =", script)


if __name__ == "__main__":
    unittest.main()
