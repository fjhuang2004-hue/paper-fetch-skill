from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "onboard_from_manifests.py"
STATE_SCHEMA_PATH = REPO_ROOT / "docs" / "ai-onboarding" / "onboarding-state.schema.json"
HARD_CONSTRAINTS_PATH = REPO_ROOT / "docs" / "ai-onboarding" / "hard-constraints.md"
FAILURE_RECOVERY_PATH = REPO_ROOT / "docs" / "ai-onboarding" / "failure-recovery.md"
CENTRAL_PROVIDER_LOGIC_PATHS = {
    "src/paper_fetch/extraction/html/provider_rules.py",
    "src/paper_fetch/quality/html_signals.py",
    "src/paper_fetch/quality/html_availability.py",
}
REMOVED_CENTER_PATHS = {
    "src/paper_fetch/provider_rules.py",
    "src/paper_fetch/html_signals.py",
    "src/paper_fetch/html_availability.py",
}


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def test_help_includes_discover() -> None:
    result = run_cli("--help")

    assert "discover" in result.stdout
    assert "next" in result.stdout
    assert "verify" in result.stdout
    assert "advance" in result.stdout


def test_start_provider_dry_run_writes_dag_and_worker_briefs(tmp_path: Path) -> None:
    run_cli(
        "start",
        "--provider",
        "mdpi",
        "--domain",
        "mdpi.com",
        "--dry-run",
        "--output-dir",
        str(tmp_path),
    )

    dag_path = tmp_path / "task-dag.json"
    discover_brief_path = tmp_path / "briefs" / "discover-manifest.yml"
    implement_brief_path = tmp_path / "briefs" / "implement-provider.yml"
    dag = json.loads(dag_path.read_text(encoding="utf-8"))
    discover_brief = discover_brief_path.read_text(encoding="utf-8")
    implement_brief = yaml.safe_load(implement_brief_path.read_text(encoding="utf-8"))

    assert any(step["id"] == "discover-manifest" for step in dag["steps"])
    assert [step["id"] for step in dag["steps"]] == [
        "discover-manifest",
        "validate-manifest",
        "capture-fixtures",
        "scaffold",
        "implement-provider",
        "snapshot-expected",
        "manifest-sync-back",
        "provider-local-acceptance",
        "global-lint",
        "merge-ready",
    ]
    assert dag["manifest"] == "docs/ai-onboarding/manifests/mdpi.yml"
    assert dag["runtime"] == "coding-agent-subagent"
    assert discover_brief_path.is_file()
    assert implement_brief_path.is_file()
    assert "current_step: discover-manifest" in discover_brief
    assert "output_manifest: docs/ai-onboarding/manifests/mdpi.yml" in discover_brief
    assert "domain: mdpi.com" in discover_brief
    assert implement_brief["task_id"] == "mdpi-implement-provider"
    assert implement_brief["provider_manifest"] == "docs/ai-onboarding/manifests/mdpi.yml"
    assert implement_brief["current_step"] == "implement-provider"
    assert implement_brief["runtime"] == "coding-agent-subagent"
    assert implement_brief["hard_constraints"] == (
        "docs/ai-onboarding/hard-constraints.md"
    )
    assert HARD_CONSTRAINTS_PATH.is_file()
    assert implement_brief["no_commit"] is True
    assert implement_brief["markdown_review_loop"] == {
        "required": True,
        "fixture_source": "provider_manifest.fixtures.doi_samples",
        "require_each_non_null_purpose_asserted": True,
        "require_positive_and_negative_markdown_assertions": True,
        "forbid_skipped_scaffold_placeholder": True,
    }
    assert implement_brief["output_requirements"] == {
        "reviewed_fixtures": (
            "one entry per non-null "
            "provider_manifest.fixtures.doi_samples purpose"
        ),
        "reviewed_fixture_fields": [
            "fixture",
            "purpose",
            "issue",
            "assertion",
            "fix",
        ],
    }
    assert implement_brief["failure_recovery"]["policy"] == (
        "docs/ai-onboarding/failure-recovery.md"
    )
    assert FAILURE_RECOVERY_PATH.is_file()
    assert "acceptance" in implement_brief
    assert (
        "PYTHONPATH=src python3 -m pytest "
        "tests/unit/test_provider_markdown_review_contract.py -q"
    ) in implement_brief["acceptance"]["pytest"]
    assert "files_allowed_to_modify" in implement_brief
    assert "files_must_not_modify" in implement_brief
    grep_paths = set(implement_brief["acceptance"]["grep_must_be_empty"][0]["paths"])
    forbidden_paths = set(implement_brief["files_must_not_modify"])
    assert CENTRAL_PROVIDER_LOGIC_PATHS <= grep_paths
    assert CENTRAL_PROVIDER_LOGIC_PATHS <= forbidden_paths
    assert not (REMOVED_CENTER_PATHS & grep_paths)
    assert not (REMOVED_CENTER_PATHS & forbidden_paths)


def test_discover_prints_brief_with_requested_output_manifest() -> None:
    result = run_cli(
        "discover",
        "--provider",
        "mdpi",
        "--domain",
        "mdpi.com",
        "--output",
        "docs/ai-onboarding/manifests/mdpi.yml",
    )

    assert "task_id: mdpi-discover-manifest" in result.stdout
    assert "current_step: discover-manifest" in result.stdout
    assert "output_manifest: docs/ai-onboarding/manifests/mdpi.yml" in result.stdout


def test_start_manifest_replay_skips_discover_brief(tmp_path: Path) -> None:
    manifest_path = tmp_path / "custom.yml"
    manifest_path.write_text("name: custom_provider\n", encoding="utf-8")

    run_cli(
        "start",
        "--manifest",
        str(manifest_path),
        "--dry-run",
        "--output-dir",
        str(tmp_path),
    )

    dag = json.loads((tmp_path / "task-dag.json").read_text(encoding="utf-8"))
    assert all(step["id"] != "discover-manifest" for step in dag["steps"])
    assert dag["provider"] == "custom_provider"
    assert dag["manifest"] == str(manifest_path)
    assert not (tmp_path / "briefs" / "discover-manifest.yml").exists()
    assert (tmp_path / "briefs" / "implement-provider.yml").is_file()


def test_state_commands_persist_next_verify_and_advance(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    next_result = run_cli("next", "--provider", "mdpi", "--state", str(state_path))
    next_payload = json.loads(next_result.stdout)
    assert next_payload["current_step"] == "discover-manifest"

    verify_result = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "provider-local-acceptance",
        "--state",
        str(state_path),
    )
    verify_payload = json.loads(verify_result.stdout)
    assert verify_payload["dry_run"] is True
    assert verify_payload["result"] == "planned"
    assert verify_payload["commands"]

    advance_result = run_cli(
        "advance",
        "--provider",
        "mdpi",
        "--task",
        "discover-manifest",
        "--state",
        str(state_path),
    )
    advance_payload = json.loads(advance_result.stdout)
    assert advance_payload["advanced"] == "discover-manifest"
    assert advance_payload["next_step"] == "validate-manifest"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_state = state["providers"]["mdpi"]
    assert state["active_provider"] == "mdpi"
    assert provider_state["completed_steps"] == ["discover-manifest"]
    assert provider_state["task_statuses"]["validate-manifest"] == "in_progress"
    assert provider_state["verifications"]["provider-local-acceptance"]["dry_run"] is True


def test_verify_plan_uses_existing_tool_interfaces(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    sync_back = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "manifest-sync-back",
        "--state",
        str(state_path),
    )
    sync_back_commands = json.loads(sync_back.stdout)["commands"]
    assert [
        "python3",
        "scripts/manifest_sync_back.py",
        "--provider",
        "mdpi",
        "--manifest",
        "docs/ai-onboarding/manifests/mdpi.yml",
    ] in sync_back_commands

    capture = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "capture-fixtures",
        "--state",
        str(state_path),
    )
    capture_commands = json.loads(capture.stdout)["commands"]
    assert not any("--from-manifest" in command for command in capture_commands)

    snapshot = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "snapshot-expected",
        "--state",
        str(state_path),
    )
    snapshot_commands = json.loads(snapshot.stdout)["commands"]
    assert ["python3", "scripts/snapshot_expected.py", "--help"] in snapshot_commands

    implement = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "implement-provider",
        "--state",
        str(state_path),
    )
    implement_commands = json.loads(implement.stdout)["commands"]
    markdown_contract_command = [
        "PYTHONPATH=src",
        "python3",
        "-m",
        "pytest",
        "tests/unit/test_provider_markdown_review_contract.py",
        "-q",
    ]
    assert markdown_contract_command in implement_commands

    local_acceptance = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "provider-local-acceptance",
        "--state",
        str(state_path),
    )
    local_acceptance_commands = json.loads(local_acceptance.stdout)["commands"]
    assert markdown_contract_command in local_acceptance_commands


def test_written_state_matches_schema(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    run_cli("next", "--provider", "mdpi", "--state", str(state_path))

    schema = json.loads(STATE_SCHEMA_PATH.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(state),
        key=lambda error: error.json_path,
    )
    assert not errors


def test_state_rejects_two_in_progress_providers(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    run_cli("next", "--provider", "mdpi", "--state", str(state_path))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "next",
            "--provider",
            "arxiv",
            "--state",
            str(state_path),
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "another provider is already in_progress" in result.stderr


def test_onboard_script_does_not_import_llm_sdks() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8").lower()

    assert "anthropic" not in script
    assert "openai" not in script
