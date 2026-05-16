from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ._installer_support import write_executable as _write_executable


REPO_ROOT = Path(__file__).resolve().parents[2]


class SkillInstallerTests(unittest.TestCase):
    def test_gemini_installer_skips_mcp_when_cli_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_bin = root / "fake-bin"
            home = root / "home"
            home.mkdir()

            _write_executable(
                fake_bin / "python3",
                """\
                #!/usr/bin/env bash
                if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
                  exit 0
                fi
                if [[ "${1:-}" == "-c" ]]; then
                  echo "$0"
                  exit 0
                fi
                exit 0
                """,
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PATH"] = f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"

            result = subprocess.run(
                [str(REPO_ROOT / "scripts" / "install-gemini-skill.sh"), "--register-mcp"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / ".gemini" / "skills" / "paper-fetch-skill" / "SKILL.md").is_file())
            self.assertIn("skipped Gemini MCP registration", result.stderr)

    def test_gemini_installer_registers_mcp_with_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_bin = root / "fake-bin"
            home = root / "home"
            env_file = root / "paper-fetch.env"
            cli_log = root / "cli.log"
            home.mkdir()
            env_file.write_text('ELSEVIER_API_KEY="secret"\n', encoding="utf-8")

            _write_executable(
                fake_bin / "python3",
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
                  exit 0
                fi
                if [[ "${1:-}" == "-c" ]]; then
                  echo "$0"
                  exit 0
                fi
                exit 0
                """,
            )
            _write_executable(
                fake_bin / "gemini",
                f"""\
                #!/usr/bin/env bash
                {{
                  printf '%s' "$(basename "$0")"
                  for arg in "$@"; do
                    printf '\\t%s' "$arg"
                  done
                  printf '\\n'
                }} >> "{cli_log}"
                exit 0
                """,
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PATH"] = f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"

            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "install-gemini-skill.sh"),
                    "--register-mcp",
                    "--env-file",
                    str(env_file),
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / ".gemini" / "skills" / "paper-fetch-skill" / "SKILL.md").is_file())
            calls = [line.split("\t") for line in cli_log.read_text(encoding="utf-8").splitlines()]
            self.assertIn(["gemini", "mcp", "remove", "paper-fetch"], calls)
            gemini_add = next(call for call in calls if call[:3] == ["gemini", "mcp", "add"])
            self.assertIn("--env", gemini_add)
            self.assertIn(f"PAPER_FETCH_ENV_FILE={env_file}", gemini_add)
            self.assertEqual(
                gemini_add[-5:],
                [
                    "paper-fetch",
                    "--",
                    str(fake_bin / "python3"),
                    "-m",
                    "paper_fetch.mcp.server",
                ],
            )


if __name__ == "__main__":
    unittest.main()
