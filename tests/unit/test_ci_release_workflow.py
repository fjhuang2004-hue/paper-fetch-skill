from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


class CiReleaseWorkflowTests(unittest.TestCase):
    def test_phase8_release_workflow_input_is_absent_in_this_repository(self) -> None:
        self.assertFalse(RELEASE_WORKFLOW.exists())
        self.assertTrue(CI_WORKFLOW.exists())

    def test_release_workflow_has_no_active_flaresolverr_when_present(self) -> None:
        if not RELEASE_WORKFLOW.exists():
            return

        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        active_lines = [
            line
            for line in workflow.splitlines()
            if "flaresolverr" in line.lower() and "legacy" not in line.lower() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(active_lines, [])

    def test_release_workflow_cloakbrowser_runtime_smoke_when_present(self) -> None:
        if not RELEASE_WORKFLOW.exists():
            return

        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pip install cloakbrowser", workflow)
        self.assertIn("import cloakbrowser", workflow)


if __name__ == "__main__":
    unittest.main()
