from __future__ import annotations

import json
import os
import subprocess
import sys
import argparse
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from tests.script_modules import load_script_module


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "onboard_from_manifests.py"
STATE_SCHEMA_PATH = REPO_ROOT / "onboarding" / "onboarding-state.schema.json"
ACCESS_REVIEW_SCHEMA_PATH = REPO_ROOT / "onboarding" / "access-review.schema.json"
HARD_CONSTRAINTS_PATH = REPO_ROOT / "onboarding" / "hard-constraints.md"
FAILURE_RECOVERY_PATH = REPO_ROOT / "onboarding" / "failure-recovery.md"
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


def _write_executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{content.lstrip()}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _prepend_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    current = os.environ.get("PATH")
    value = str(path) if not current else f"{path}{os.pathsep}{current}"
    monkeypatch.setenv("PATH", value)


def _write_fake_codex_wrapper(bin_dir: Path, agent: Path, *, repo_root: Path = REPO_ROOT) -> Path:
    return _write_executable(
        bin_dir / "codex",
        f"""
from __future__ import annotations

import runpy
import sys

assert sys.argv[1:] == [
    "exec",
    "--cd",
    {str(repo_root)!r},
    "--sandbox",
    "workspace-write",
    "-c",
    'approval_policy="never"',
    "-",
]
sys.argv = [{str(agent)!r}]
runpy.run_path({str(agent)!r}, run_name="__main__")
""",
    )


def test_help_includes_discover() -> None:
    result = run_cli("--help")

    assert "discover" in result.stdout
    assert "prepare-discovery" in result.stdout
    assert "autofix-manifest" in result.stdout
    assert "inspect-discovery" in result.stdout
    assert "run" in result.stdout
    assert "diagnose" in result.stdout
    assert "resume-blocked" in result.stdout
    assert "summarize" in result.stdout
    assert "next" in result.stdout
    assert "verify" in result.stdout
    assert "run-checks" in result.stdout
    assert "repair-markdown-quality" in result.stdout
    assert "check-cleaning-proposal" in result.stdout
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
        "operator-access-preflight",
        "discover-manifest",
        "validate-manifest",
        "capture-fixtures",
        "propose-cleaning-chain",
        "scaffold",
        "implement-provider",
        "shared-integration",
        "snapshot-expected",
        "manifest-sync-back",
        "provider-local-acceptance",
        "global-lint",
        "merge-ready",
    ]
    assert dag["manifest"] == "onboarding/manifests/mdpi.yml"
    assert dag["runtime"] == "coding-agent-subagent"
    assert discover_brief_path.is_file()
    assert implement_brief_path.is_file()
    assert "current_step: discover-manifest" in discover_brief
    assert "output_manifest: onboarding/manifests/mdpi.yml" in discover_brief
    assert "evidence_pack:" in discover_brief
    assert "contract_templates:" in discover_brief
    assert "autofix_policy:" in discover_brief
    assert "domain: mdpi.com" in discover_brief
    assert implement_brief["task_id"] == "mdpi-implement-provider"
    assert implement_brief["provider_manifest"] == "onboarding/manifests/mdpi.yml"
    assert implement_brief["current_step"] == "implement-provider"
    assert implement_brief["runtime"] == "coding-agent-subagent"
    assert implement_brief["upstream_artifacts"]["cleaning_proposal"] == (
        "onboarding/cleaning-chain-proposals/mdpi.yml"
    )
    assert implement_brief["cleaning_proposal"]["producer_task"] == "propose-cleaning-chain"
    assert implement_brief["access_review"] == (
        "onboarding/access-reviews/mdpi.yml"
    )
    assert implement_brief["access_policy_constraints"]["do_not_auto_login"] is True
    assert implement_brief["access_policy_constraints"]["do_not_solve_captcha"] is True
    assert implement_brief["hard_constraints"] == (
        "onboarding/hard-constraints.md"
    )
    assert HARD_CONSTRAINTS_PATH.is_file()
    assert implement_brief["no_commit"] is True
    assert implement_brief["markdown_review_loop"] == {
        "required": True,
        "fixture_source": (
            "provider_manifest.fixtures.doi_samples + "
            "provider_manifest.extra_fixtures"
        ),
        "route_contract_source": "provider_manifest.route_contract",
        "markdown_contract_source": "provider_manifest.markdown_contract",
        "require_each_non_null_purpose_asserted": True,
        "require_positive_and_negative_markdown_assertions": True,
        "forbid_skipped_scaffold_placeholder": True,
    }
    assert implement_brief["coordinator_integration_scope"] == {
        "route_sources": (
            "provider_manifest.route_sources maps main_path steps to "
            "runtime sources."
        ),
        "extra_fixtures": (
            "provider_manifest.extra_fixtures extends capture and Markdown "
            "review beyond fixed purpose slots."
        ),
        "post_worker_integrations": [
            "golden corpus adapter wiring",
            "runtime source/schema registration",
            "manifest/bundle sync-back",
        ],
    }
    assert implement_brief["output_requirements"] == {
        "review_artifact": "onboarding/reviews/mdpi.yml",
        "reviewed_fixtures": (
            "one entry per non-null provider_manifest.fixtures.doi_samples "
            "purpose and per provider_manifest.extra_fixtures item"
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
        "onboarding/failure-recovery.md"
    )
    assert FAILURE_RECOVERY_PATH.is_file()
    assert "acceptance" in implement_brief
    assert implement_brief["acceptance"]["cleaning_contract_gate"] == [
        "python3 scripts/onboard_from_manifests.py check-cleaning-proposal --provider mdpi",
        "python3 scripts/propose_cleaning_chain.py --provider mdpi --check-contract",
    ]
    assert implement_brief["acceptance"]["live_review"] == {
        "required_for_provider_acceptance": True,
        "policy": (
            "Future providers default to one provider subset live assets review; "
            "legacy non-risk providers are exempt."
        ),
        "command": (
            "PAPER_FETCH_RUN_LIVE=1 python3 "
            "scripts/run_golden_criteria_live_review.py --providers mdpi"
        ),
        "source_contract": "provider_manifest.route_sources",
        "markdown_contract": "provider_manifest.markdown_contract",
    }
    assert (
        "PYTHONPATH=src python3 -m pytest "
        "tests/unit/test_provider_markdown_review_contract.py -q"
    ) in implement_brief["acceptance"]["pytest"]
    assert (
        "PYTHONPATH=src python3 -m pytest "
        "tests/unit/test_provider_asset_contract.py -q"
    ) in implement_brief["acceptance"]["pytest"]
    assert (
        "PYTHONPATH=src python3 -m pytest "
        "tests/unit/test_provider_route_contract.py -q"
    ) in implement_brief["acceptance"]["pytest"]
    assert "files_allowed_to_modify" in implement_brief
    assert "files_must_not_modify" in implement_brief
    assert "onboarding/manifests/mdpi.yml" in implement_brief["files_allowed_to_modify"]
    assert implement_brief["manifest_adjustment_policy"]["allowed_only_for_failure_code"] == (
        "MARKDOWN_CONTRACT_DRIFT"
    )
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
        "onboarding/manifests/mdpi.yml",
    )

    assert "task_id: mdpi-discover-manifest" in result.stdout
    assert "current_step: discover-manifest" in result.stdout
    assert "output_manifest: onboarding/manifests/mdpi.yml" in result.stdout
    assert "access_review: onboarding/access-reviews/mdpi.yml" in result.stdout
    assert "producer: prepare-discovery" in result.stdout


def test_prepare_discovery_cli_no_network_writes_evidence_pack(tmp_path: Path) -> None:
    result = run_cli(
        "prepare-discovery",
        "--provider",
        "newpub",
        "--domain",
        "newpub.example",
        "--doi-prefix",
        "10.4242",
        "--output-dir",
        str(tmp_path),
        "--no-network",
    )
    payload = json.loads(result.stdout)
    pack = json.loads((tmp_path / "discovery" / "evidence-pack.json").read_text(encoding="utf-8"))

    assert payload["provider"] == "newpub"
    assert payload["network_enabled"] is False
    assert pack["provider_seed"]["domain"] == "newpub.example"
    assert len(pack["query_plan"]["table"]) == 3


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
    assert dag["steps"][0]["id"] == "operator-access-preflight"
    assert dag["provider"] == "custom_provider"
    assert dag["manifest"] == str(manifest_path)
    assert not (tmp_path / "briefs" / "discover-manifest.yml").exists()
    assert (tmp_path / "briefs" / "implement-provider.yml").is_file()


def test_state_commands_persist_next_verify_and_advance(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    next_result = run_cli("next", "--provider", "mdpi", "--state", str(state_path))
    next_payload = json.loads(next_result.stdout)
    assert next_payload["current_step"] == "operator-access-preflight"

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
        "operator-access-preflight",
        "--state",
        str(state_path),
    )
    advance_payload = json.loads(advance_result.stdout)
    assert advance_payload["advanced"] == "operator-access-preflight"
    assert advance_payload["next_step"] == "discover-manifest"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_state = state["providers"]["mdpi"]
    assert state["active_provider"] == "mdpi"
    assert provider_state["completed_steps"] == ["operator-access-preflight"]
    assert provider_state["task_statuses"]["discover-manifest"] == "in_progress"
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
        "onboarding/manifests/mdpi.yml",
        "--sync-docs",
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
    assert [
        "python3",
        "scripts/capture_fixture.py",
        "--from-manifest",
        "onboarding/manifests/mdpi.yml",
        "--all",
        "--auto-via",
        "--fail-fast",
        "--dry-run",
    ] in capture_commands

    proposal = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "propose-cleaning-chain",
        "--state",
        str(state_path),
    )
    proposal_commands = json.loads(proposal.stdout)["commands"]
    assert [
        "python3",
        "scripts/propose_cleaning_chain.py",
        "--provider",
        "mdpi",
        "--write",
    ] in proposal_commands

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
    assert [
        "PYTHONPATH=src",
        "python3",
        "scripts/snapshot_expected.py",
        "--doi",
        "10.3390/membranes15030093",
        "--review",
    ] in snapshot_commands
    assert [
        "PYTHONPATH=src",
        "python3",
        "scripts/snapshot_expected.py",
        "--doi",
        "10.3390/membranes15030093",
    ] in snapshot_commands
    assert [
        "PYTHONPATH=src",
        "python3",
        "scripts/onboard_from_manifests.py",
        "check-snapshot",
        "--provider",
        "mdpi",
        "--doi",
        "10.3390/membranes15030093",
    ] in snapshot_commands
    assert ["python3", "scripts/snapshot_expected.py", "--help"] not in snapshot_commands

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
    asset_contract_command = [
        "PYTHONPATH=src",
        "python3",
        "-m",
        "pytest",
        "tests/unit/test_provider_asset_contract.py",
        "-q",
    ]
    assert asset_contract_command in implement_commands
    route_contract_command = [
        "PYTHONPATH=src",
        "python3",
        "-m",
        "pytest",
        "tests/unit/test_provider_route_contract.py",
        "-q",
    ]
    assert route_contract_command in implement_commands

    shared_integration = run_cli(
        "verify",
        "--provider",
        "mdpi",
        "--task",
        "shared-integration",
        "--state",
        str(state_path),
    )
    shared_commands = json.loads(shared_integration.stdout)["commands"]
    assert [
        "PYTHONPATH=src",
        "python3",
        "-m",
        "pytest",
        "tests/unit/test_manifest_bundle_sync.py",
        "tests/unit/test_golden_corpus_adapters.py",
        "tests/unit/test_provider_benchmark_samples.py",
        "tests/devtools/test_golden_criteria_live.py",
        "-q",
    ] in shared_commands

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
    assert [
        "python3",
        "scripts/onboard_from_manifests.py",
        "check-cleaning-proposal",
        "--provider",
        "mdpi",
    ] in local_acceptance_commands
    assert [
        "python3",
        "scripts/propose_cleaning_chain.py",
        "--provider",
        "mdpi",
        "--check-contract",
    ] in local_acceptance_commands
    assert markdown_contract_command in local_acceptance_commands
    assert asset_contract_command in local_acceptance_commands
    assert route_contract_command in local_acceptance_commands
    assert [
        "PAPER_FETCH_RUN_LIVE=1",
        "python3",
        "scripts/run_golden_criteria_live_review.py",
        "--providers",
        "mdpi",
    ] in local_acceptance_commands


def test_live_review_policy_defaults_to_future_providers_and_exempts_legacy_non_risk() -> None:
    module = load_script_module("onboard_from_manifests")

    future_live_command = [
        "PAPER_FETCH_RUN_LIVE=1",
        "python3",
        "scripts/run_golden_criteria_live_review.py",
        "--providers",
        "futurepublisher",
    ]
    mdpi_live_command = [
        "PAPER_FETCH_RUN_LIVE=1",
        "python3",
        "scripts/run_golden_criteria_live_review.py",
        "--providers",
        "mdpi",
    ]
    springer_live_command = [
        "PAPER_FETCH_RUN_LIVE=1",
        "python3",
        "scripts/run_golden_criteria_live_review.py",
        "--providers",
        "springer",
    ]

    assert future_live_command in module._verify_commands(
        "futurepublisher",
        "provider-local-acceptance",
    )
    assert mdpi_live_command in module._verify_commands(
        "mdpi",
        "provider-local-acceptance",
    )
    assert springer_live_command not in module._verify_commands(
        "springer",
        "provider-local-acceptance",
    )
    assert future_live_command not in module._verify_commands(
        "futurepublisher",
        "provider-local-acceptance",
        include_live=False,
    )


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


def test_access_review_schema_accepts_required_operator_fields() -> None:
    schema = json.loads(ACCESS_REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
    review = yaml.safe_load(
        (
            REPO_ROOT
            / "onboarding"
            / "access-reviews"
            / "mdpi.yml"
        ).read_text(encoding="utf-8")
    )

    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(review),
        key=lambda error: error.json_path,
    )
    assert not errors
    assert review["status"] == "approved"
    assert review["may_continue"] is True
    assert {"http", "browser"} <= set(review["allowed_runtimes"])


def test_missing_access_review_blocks_discovery_verify(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "verify",
            "--provider",
            "newpub",
            "--task",
            "discover-manifest",
            "--state",
            str(state_path),
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "ACCESS_REVIEW_NOT_FOUND" in result.stderr


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


def test_run_checks_executes_single_task_and_records_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    result = run_cli(
        "run-checks",
        "--provider",
        "mdpi",
        "--task",
        "operator-access-preflight",
        "--state",
        str(state_path),
    )
    payload = json.loads(result.stdout)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    run = state["providers"]["mdpi"]["runs"]["operator-access-preflight"]

    assert payload["result"] == "passed"
    assert run["dry_run"] is False
    assert run["result"] == "passed"
    assert ["test", "-f", "onboarding/access-reviews/mdpi.yml"] in run["commands"]

    schema = json.loads(STATE_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(state),
        key=lambda error: error.json_path,
    )
    assert not errors


def test_run_until_access_preflight_executes_serial_prefix(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    output_dir = tmp_path / "run"

    result = run_cli(
        "run",
        "--manifest",
        "onboarding/manifests/mdpi.yml",
        "--until",
        "operator-access-preflight",
        "--state",
        str(state_path),
        "--output-dir",
        str(output_dir),
    )
    payload = json.loads(result.stdout)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert payload["executed"] == ["operator-access-preflight"]
    assert payload["current_step"] == "validate-manifest"
    assert (output_dir / "task-dag.json").is_file()
    assert (output_dir / "briefs" / "implement-provider.yml").is_file()
    provider_state = state["providers"]["mdpi"]
    assert provider_state["completed_steps"] == ["operator-access-preflight"]
    assert provider_state["task_statuses"]["validate-manifest"] == "in_progress"


def test_run_dispatches_worker_through_agent_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_marker = tmp_path / "codex-called.txt"
    _write_executable(
        tmp_path / "bin" / "codex",
        f"""
from pathlib import Path
import sys

Path({str(codex_marker)!r}).write_text("called", encoding="utf-8")
sys.exit(13)
""",
    )
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        """
from __future__ import annotations

import sys

prompt = sys.stdin.read()
assert "mdpi-discover-manifest" in prompt
print("worker ok")
""",
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    output_dir = tmp_path / "run"
    _prepend_path(monkeypatch, tmp_path / "bin")
    monkeypatch.setenv("PROVIDER_ONBOARDING_AGENT_CLI", f"{sys.executable} {fake_agent}")

    result = run_cli(
        "run",
        "--provider",
        "mdpi",
        "--domain",
        "mdpi.com",
        "--until",
        "discover-manifest",
        "--state",
        str(state_path),
        "--output-dir",
        str(output_dir),
    )
    payload = json.loads(result.stdout)

    assert payload["executed"] == ["operator-access-preflight", "discover-manifest"]
    assert (output_dir / "discovery" / "evidence-pack.json").is_file()
    assert (output_dir / "workers" / "discover-manifest-attempt-1.prompt.md").is_file()
    assert (
        output_dir / "workers" / "discover-manifest-attempt-1.stdout.log"
    ).read_text(encoding="utf-8") == "worker ok\n"
    assert not codex_marker.exists()


def test_run_uses_default_codex_dispatcher_when_agent_cli_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_path = tmp_path / "codex-record.json"
    _write_executable(
        tmp_path / "bin" / "codex",
        f"""
from __future__ import annotations

import json
import sys
from pathlib import Path

prompt = sys.stdin.read()
Path({str(record_path)!r}).write_text(
    json.dumps({{"argv": sys.argv[1:], "prompt": prompt}}, indent=2, sort_keys=True) + "\\n",
    encoding="utf-8",
)
assert sys.argv[1:] == [
    "exec",
    "--cd",
    {str(REPO_ROOT)!r},
    "--sandbox",
    "workspace-write",
    "-c",
    'approval_policy="never"',
    "-",
]
assert "mdpi-discover-manifest" in prompt
print("codex worker ok")
""",
    )
    state_path = tmp_path / "state.json"
    output_dir = tmp_path / "run"
    monkeypatch.delenv("PROVIDER_ONBOARDING_AGENT_CLI", raising=False)
    _prepend_path(monkeypatch, tmp_path / "bin")

    result = run_cli(
        "run",
        "--provider",
        "mdpi",
        "--domain",
        "mdpi.com",
        "--until",
        "discover-manifest",
        "--state",
        str(state_path),
        "--output-dir",
        str(output_dir),
    )
    payload = json.loads(result.stdout)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert payload["executed"] == ["operator-access-preflight", "discover-manifest"]
    assert record["argv"][0] == "exec"
    assert record["argv"][-1] == "-"
    assert "mdpi-discover-manifest" in record["prompt"]
    assert "Discovery Evidence Pack Summary" in record["prompt"]
    assert (output_dir / "discovery" / "evidence-pack.json").is_file()
    assert state["agent_cli"].startswith("codex exec --cd ")
    assert (
        output_dir / "workers" / "discover-manifest-attempt-1.stdout.log"
    ).read_text(encoding="utf-8") == "codex worker ok\n"


def test_validate_manifest_runs_pre_and_targeted_autofix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    calls: list[bool] = []

    def fake_autofix(**kwargs: object) -> dict[str, object]:
        calls.append(bool(kwargs.get("targeted")))
        return {
            "changed": bool(kwargs.get("targeted")),
            "changed_paths": ["fixtures.discovery_proof.table"],
            "targeted": bool(kwargs.get("targeted")),
        }

    results = iter(
        [
            subprocess.CompletedProcess(
                ["pytest"],
                1,
                "",
                json.dumps({"code": "MANIFEST_SCHEMA_INVALID", "retryable": True}),
            ),
            subprocess.CompletedProcess(["pytest"], 0, "ok\n", ""),
        ]
    )
    monkeypatch.setattr(module, "_autofix_manifest_for_runner", fake_autofix)
    monkeypatch.setattr(module, "_run_env_command", lambda command: next(results))

    module._execute_local_task(
        provider="mdpi",
        task="validate-manifest",
        provider_state={"manifest": "onboarding/manifests/mdpi.yml"},
        output_dir=tmp_path,
    )

    assert calls == [False, True]


def test_run_checks_emits_structured_failure_for_missing_access_review(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "run-checks",
            "--provider",
            "newpub",
            "--task",
            "operator-access-preflight",
            "--state",
            str(state_path),
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    payload = json.loads(result.stderr)
    assert payload["code"] == "ACCESS_REVIEW_NOT_FOUND"
    assert payload["retryable"] is False
    state = json.loads(state_path.read_text(encoding="utf-8"))
    failure = state["providers"]["newpub"]["runs"]["operator-access-preflight"]["failure"]
    assert failure["code"] == "ACCESS_REVIEW_NOT_FOUND"
    assert failure["structured_error"]["code"] == "ACCESS_REVIEW_NOT_FOUND"


def test_run_access_preflight_failure_is_diagnosable(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    output_dir = tmp_path / "run"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "run",
            "--provider",
            "newpub",
            "--domain",
            "example.org",
            "--until",
            "operator-access-preflight",
            "--state",
            str(state_path),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    diagnosis_result = run_cli("diagnose", "--provider", "newpub", "--state", str(state_path))
    diagnosis = json.loads(diagnosis_result.stdout)["providers"][0]

    assert result.returncode != 0
    assert json.loads(result.stderr)["code"] == "ACCESS_REVIEW_NOT_FOUND"
    assert diagnosis["failure"]["task"] == "operator-access-preflight"
    assert diagnosis["failure"]["code"] == "ACCESS_REVIEW_NOT_FOUND"
    assert diagnosis["operator_required"] is True


def test_check_cleaning_proposal_detects_stale_digest(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.html"
    raw_path.write_text("<article>fresh</article>", encoding="utf-8")
    proposal_path = tmp_path / "proposal.yml"
    proposal_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "provider": "mdpi",
                "fixtures_digest": [
                    {
                        "purpose": "structure",
                        "doi": "10.0000/example",
                        "raw_path": raw_path.as_posix(),
                        "sha256": "0" * 64,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "check-cleaning-proposal",
            "--provider",
            "mdpi",
            "--proposal",
            str(proposal_path),
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    payload = json.loads(result.stderr)
    assert payload["code"] == "MARKDOWN_CONTRACT_DRIFT"
    assert payload["retryable"] is True
    assert payload["details"]["recovery_task"] == "propose-cleaning-chain"
    assert payload["details"]["stale_fixtures_digest"][0]["reason"] == "sha256_mismatch"


def _write_blocked_state(
    path: Path,
    *,
    provider: str = "mdpi",
    task: str = "capture-fixtures",
    code: str = "NETWORK_TRANSIENT",
) -> None:
    steps = [
        "operator-access-preflight",
        "discover-manifest",
        "validate-manifest",
        "capture-fixtures",
        "propose-cleaning-chain",
        "scaffold",
        "implement-provider",
        "shared-integration",
        "snapshot-expected",
        "manifest-sync-back",
        "provider-local-acceptance",
        "global-lint",
        "merge-ready",
    ]
    statuses = {step: "pending" for step in steps}
    statuses[task] = "failed"
    completed = steps[: steps.index(task)]
    state = {
        "schema_version": 1,
        "agent_cli": None,
        "active_provider": provider,
        "providers": {
            provider: {
                "provider": provider,
                "manifest": f"onboarding/manifests/{provider}.yml",
                "status": "blocked",
                "current_step": task,
                "steps": steps,
                "completed_steps": completed,
                "task_statuses": statuses,
                "retry_counts": {step: 0 for step in steps},
                "verifications": {
                    "provider-local-acceptance": {
                        "result": "planned",
                        "commands": [
                            [
                                "python3",
                                "-m",
                                "pytest",
                                "tests/unit/test_mdpi_provider.py",
                                "-q",
                            ]
                        ],
                    }
                },
                "runs": {
                    task: {
                        "dry_run": False,
                        "commands": [["fake-command"]],
                        "result": "failed",
                        "failure": {
                            "code": code,
                            "command": ["fake-command"],
                            "returncode": 1,
                        },
                    }
                },
            }
        },
    }
    path.write_text(json.dumps(state), encoding="utf-8")


def test_diagnose_reports_retryable_failure_and_recovery_action(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_blocked_state(state_path, code="NETWORK_TRANSIENT")

    result = run_cli("diagnose", "--provider", "mdpi", "--state", str(state_path))
    payload = json.loads(result.stdout)
    diagnosis = payload["providers"][0]

    assert diagnosis["provider"] == "mdpi"
    assert diagnosis["status"] == "blocked"
    assert diagnosis["failure"]["task"] == "capture-fixtures"
    assert diagnosis["failure"]["code"] == "NETWORK_TRANSIENT"
    assert diagnosis["failure"]["retryable"] is True
    assert "retry budget" in diagnosis["failure"]["action"]


def test_resume_blocked_requires_pdf_fallback_sample_replacement(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_blocked_state(state_path, code="NON_PDF_FALLBACK_CONTENT")

    result = run_cli(
        "resume-blocked",
        "--provider",
        "mdpi",
        "--state",
        str(state_path),
        "--dry-run",
    )
    payload = json.loads(result.stdout)
    plan = payload["resume_plan"]

    assert plan["resumable"] is False
    assert "failed pdf_fallback DOI sample must be replaced before retry" in plan["blockers"]


def test_diagnose_ignores_stale_failure_for_completed_task(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_blocked_state(
        state_path,
        task="snapshot-expected",
        code="WORKER_AGENT_CLI_MISSING",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_state = state["providers"]["mdpi"]
    provider_state["status"] = "merge_ready"
    provider_state["current_step"] = None
    provider_state["completed_steps"] = list(provider_state["steps"])
    provider_state["task_statuses"] = {step: "completed" for step in provider_state["steps"]}
    state["active_provider"] = None
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("diagnose", "--provider", "mdpi", "--state", str(state_path))
    diagnosis = json.loads(result.stdout)["providers"][0]

    assert diagnosis["status"] == "merge_ready"
    assert diagnosis["failure"]["task"] is None
    assert diagnosis["failure"]["code"] is None
    assert diagnosis["operator_required"] is False


def test_markdown_contract_drift_recovery_targets_implementation(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_blocked_state(
        state_path,
        task="provider-local-acceptance",
        code="MARKDOWN_CONTRACT_DRIFT",
    )

    diagnosis_result = run_cli("diagnose", "--provider", "mdpi", "--state", str(state_path))
    diagnosis = json.loads(diagnosis_result.stdout)["providers"][0]
    resume_result = run_cli(
        "resume-blocked",
        "--provider",
        "mdpi",
        "--dry-run",
        "--state",
        str(state_path),
    )
    resume_plan = json.loads(resume_result.stdout)["resume_plan"]

    assert diagnosis["failure"]["code"] == "MARKDOWN_CONTRACT_DRIFT"
    assert diagnosis["failure"]["retryable"] is True
    assert "implement-provider" in diagnosis["failure"]["action"]
    assert resume_plan["next_task"] == "implement-provider"


def test_resume_blocked_dry_run_requires_approved_access_review(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_blocked_state(state_path, provider="newpub", code="NETWORK_TRANSIENT")
    before = state_path.read_text(encoding="utf-8")

    result = run_cli(
        "resume-blocked",
        "--provider",
        "newpub",
        "--dry-run",
        "--state",
        str(state_path),
    )
    payload = json.loads(result.stdout)

    assert payload["resume_plan"]["resumable"] is False
    assert any(
        "access review is not approved" in blocker
        for blocker in payload["resume_plan"]["blockers"]
    )
    assert state_path.read_text(encoding="utf-8") == before


def test_resume_blocked_executes_retryable_prefix_after_preconditions(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    output_dir = tmp_path / "run"
    _write_blocked_state(
        state_path,
        provider="mdpi",
        task="operator-access-preflight",
        code="LOCAL_CHECK_FAILED",
    )

    result = run_cli(
        "resume-blocked",
        "--provider",
        "mdpi",
        "--until",
        "operator-access-preflight",
        "--state",
        str(state_path),
        "--output-dir",
        str(output_dir),
    )
    payload = json.loads(result.stdout)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_state = state["providers"]["mdpi"]

    assert payload["resume_plan"]["resumable"] is True
    assert payload["run"]["executed"] == ["operator-access-preflight"]
    assert provider_state["status"] == "in_progress"
    assert provider_state["task_statuses"]["operator-access-preflight"] == "completed"
    assert provider_state["task_statuses"]["discover-manifest"] == "in_progress"


def test_run_refuses_merge_ready_state_with_failed_task(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_blocked_state(
        state_path,
        provider="mdpi",
        task="snapshot-expected",
        code="WORKER_AGENT_FAILED",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_state = state["providers"]["mdpi"]
    steps = provider_state["steps"]
    provider_state["status"] = "merge_ready"
    provider_state["current_step"] = None
    provider_state["completed_steps"] = [step for step in steps if step != "snapshot-expected"]
    provider_state["task_statuses"] = {step: "completed" for step in steps}
    provider_state["task_statuses"]["snapshot-expected"] = "failed"
    state["active_provider"] = None
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "run",
            "--manifest",
            "onboarding/manifests/mdpi.yml",
            "--until",
            "merge-ready",
            "--state",
            str(state_path),
            "--output-dir",
            str(tmp_path / "run"),
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stderr)
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    updated_provider = updated["providers"]["mdpi"]

    assert result.returncode != 0
    assert payload["code"] == "WORKER_AGENT_FAILED"
    assert updated["active_provider"] == "mdpi"
    assert updated_provider["status"] == "blocked"
    assert updated_provider["current_step"] == "snapshot-expected"
    assert updated_provider["task_statuses"]["snapshot-expected"] == "failed"


def test_summarize_outputs_json_and_markdown_without_fabricated_passes(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    markdown_path = tmp_path / "summary.md"
    _write_blocked_state(state_path, code="NETWORK_TRANSIENT")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["providers"]["mdpi"]["repairs"] = {
        "markdown_quality": [
            {
                "provider": "mdpi",
                "doi": "10.3390/su12072826",
                "sample_id": "10.3390_su12072826",
                "attempts": 2,
                "status": "failed",
                "issue_ids": ["broken-table"],
                "changed_paths": ["tests/unit/test_mdpi_provider.py"],
                "commands": [],
                "quality_status": "fail",
                "run_dir": ".paper-fetch-runs/mdpi-markdown-repair/markdown-quality/10.3390_su12072826",
                "failure": {"code": "MARKDOWN_QUALITY_REPAIR_FAILED"},
            }
        ]
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "summarize",
        "--provider",
        "mdpi",
        "--state",
        str(state_path),
    )
    payload = json.loads(result.stdout)
    diagnosis_result = run_cli("diagnose", "--provider", "mdpi", "--state", str(state_path))
    diagnosis = json.loads(diagnosis_result.stdout)["providers"][0]

    assert payload["provider"] == "mdpi"
    assert payload["status"] == "blocked"
    assert payload["access_review"]["status"] == "approved"
    assert payload["fixture_coverage"]
    assert "confidence" in payload["fixture_coverage"][0]
    assert "observed_signals" in payload["fixture_coverage"][0]
    assert "evidence_url" in payload["fixture_coverage"][0]
    assert "raw_path" in payload["fixture_coverage"][0]
    assert "extracted_markdown_path" in payload["fixture_coverage"][0]
    assert "markdown_quality_status" in payload["fixture_coverage"][0]
    assert "proof_status" in payload["fixture_coverage"][0]
    assert payload["markdown_quality_repairs"][0]["issue_ids"] == ["broken-table"]
    assert diagnosis["recent_markdown_quality_repair"]["status"] == "failed"
    assert payload["run_checks"][0]["result"] == "failed"
    assert payload["run_checks"][0]["failure_code"] == "NETWORK_TRANSIENT"

    run_cli(
        "summarize",
        "--provider",
        "mdpi",
        "--format",
        "markdown",
        "--output",
        str(markdown_path),
        "--state",
        str(state_path),
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# mdpi onboarding summary" in markdown
    assert "- failed_task: capture-fixtures" in markdown
    assert "failure_recovery_action:" in markdown
    assert "## Review Artifact" in markdown
    assert "onboarding/reviews/mdpi.yml" in markdown
    assert "## Markdown Quality Repairs" in markdown
    assert "doi=10.3390/su12072826 status=failed attempts=2 quality=fail" in markdown
    assert "tests/fixtures/golden_criteria/10.3390_membranes15030093/structure" in markdown
    assert "issue_ids=[] fix_ids=[] tests=[]" in markdown
    assert "## Run Checks" in markdown
    assert "- command: `fake-command`" in markdown
    assert "## Verification Plans" in markdown
    assert "- provider-local-acceptance: result=planned" in markdown
    assert "- command: `python3 -m pytest tests/unit/test_mdpi_provider.py -q`" in markdown
    assert "no recorded run-check results" not in markdown


def _agent_quality_report(
    *,
    status: str,
    issue: dict[str, object] | None = None,
) -> dict[str, object]:
    issues = [issue] if issue is not None else []
    report: dict[str, object] = {
        "schema_version": 2,
        "review_method": "agent_prompt",
        "provider": "newpub",
        "doi": "10.1234/sample",
        "sample_id": "10.1234_sample",
        "markdown_path": "tests/fixtures/golden_criteria/10.1234_sample/extracted.md",
        "prompt_path": "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality-prompt.md",
        "status": status,
        "issues": issues,
        "blocking_issue_count": sum(1 for item in issues if item.get("blocking") is True),
    }
    if status != "pending_agent_review":
        report["reviewed_by"] = "codex-agent"
        report["reviewed_at"] = "2026-05-23T00:00:00Z"
    return report


def _write_check_snapshot_fixture(
    root: Path,
    *,
    quality_status: str = "pass",
    include_prompt_asset: bool = True,
) -> None:
    manifest_path = root / "onboarding" / "manifests" / "newpub.yml"
    fixture_dir = root / "tests" / "fixtures" / "golden_criteria" / "10.1234_sample"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        """
name: newpub
fixtures:
  doi_samples:
    structure:
      doi: 10.1234/sample
""",
        encoding="utf-8",
    )
    (fixture_dir / "expected.json").write_text('{"expected_content_kind":"fulltext"}\n', encoding="utf-8")
    (fixture_dir / "extracted.md").write_text("# Demo\n", encoding="utf-8")
    (fixture_dir / "markdown-quality-prompt.md").write_text("Review prompt\n", encoding="utf-8")
    issue = None
    if quality_status == "fail":
        issue = {
            "id": "broken-table",
            "severity": "high",
            "blocking": True,
            "summary": "Broken table.",
        }
    (fixture_dir / "markdown-quality.json").write_text(
        json.dumps(_agent_quality_report(status=quality_status, issue=issue)) + "\n",
        encoding="utf-8",
    )
    assets = {
        "expected.json": "tests/fixtures/golden_criteria/10.1234_sample/expected.json",
        "extracted.md": "tests/fixtures/golden_criteria/10.1234_sample/extracted.md",
        "markdown-quality.json": "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json",
    }
    if include_prompt_asset:
        assets["markdown-quality-prompt.md"] = (
            "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality-prompt.md"
        )
    golden_manifest = {
        "samples": {
            "10.1234_sample": {
                "doi": "10.1234/sample",
                "publisher": "newpub",
                "fixture_family": "golden",
                "expected_outcome": "fulltext",
                "assets": assets,
            }
        }
    }
    (fixture_dir.parent / "manifest.json").write_text(
        json.dumps(golden_manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_fake_fresh_quality_agent(
    root: Path,
    *,
    status: str = "pass",
    issue_id: str = "fresh-broken-table",
) -> Path:
    agent = root / f"fake_fresh_quality_{status}.py"
    issues = []
    if status == "fail":
        issues = [
            {
                "id": issue_id,
                "severity": "high",
                "blocking": True,
                "summary": "Fresh review found broken Markdown.",
                "evidence": "| orphan | row |",
            }
        ]
    agent.write_text(
        f"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

prompt = sys.stdin.read()
match = re.search(r"Fresh report to write: `([^`]+)`", prompt)
if not match:
    print("missing fresh report path", file=sys.stderr)
    sys.exit(2)
report_path = Path(match.group(1))
report_path.parent.mkdir(parents=True, exist_ok=True)
issues = json.loads({json.dumps(json.dumps(issues))})
report = {{
    "schema_version": 2,
    "review_method": "agent_prompt",
    "provider": "newpub",
    "doi": "10.1234/sample",
    "sample_id": "10.1234_sample",
    "markdown_path": "tests/fixtures/golden_criteria/10.1234_sample/extracted.md",
    "prompt_path": "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality-prompt.md",
    "status": "{status}",
    "issues": issues,
    "blocking_issue_count": sum(1 for issue in issues if issue.get("blocking") is True),
    "reviewed_by": "fake-fresh-agent",
    "reviewed_at": "2026-05-23T00:00:00Z",
    "fresh_review": True,
}}
report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )
    return agent


def test_check_snapshot_requires_prompt_asset_and_agent_pass_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    fake_agent = _write_fake_fresh_quality_agent(tmp_path)
    monkeypatch.setenv("PROVIDER_ONBOARDING_AGENT_CLI", f"{sys.executable} {fake_agent}")
    args = argparse.Namespace(provider="newpub", doi="10.1234/sample")

    _write_check_snapshot_fixture(tmp_path)
    assert module.run_check_snapshot(args) == 0

    _write_check_snapshot_fixture(tmp_path, include_prompt_asset=False)
    with pytest.raises(module.ToolError) as prompt_missing:
        module.run_check_snapshot(args)
    assert prompt_missing.value.code == "EXPECTED_SNAPSHOT_FAILED"
    assert prompt_missing.value.details["missing_assets"] == ["markdown-quality-prompt.md"]

    _write_check_snapshot_fixture(tmp_path, quality_status="pending_agent_review")
    with pytest.raises(module.ToolError) as pending:
        module.run_check_snapshot(args)
    assert pending.value.code == "MARKDOWN_QUALITY_FAILED"
    assert pending.value.details["status"] == "pending_agent_review"

    _write_check_snapshot_fixture(tmp_path, quality_status="fail")
    with pytest.raises(module.ToolError) as failed:
        module.run_check_snapshot(args)
    assert failed.value.code == "MARKDOWN_QUALITY_FAILED"
    assert failed.value.details["issues"][0]["id"] == "broken-table"


def test_check_snapshot_uses_default_codex_dispatcher_for_fresh_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    fake_agent = _write_fake_fresh_quality_agent(tmp_path)
    _write_fake_codex_wrapper(tmp_path / "bin", fake_agent, repo_root=tmp_path)
    monkeypatch.delenv("PROVIDER_ONBOARDING_AGENT_CLI", raising=False)
    _prepend_path(monkeypatch, tmp_path / "bin")
    _write_check_snapshot_fixture(tmp_path)

    assert module.run_check_snapshot(argparse.Namespace(provider="newpub", doi="10.1234/sample")) == 0

    fresh_reports = list(
        (tmp_path / ".paper-fetch-runs" / "newpub-markdown-quality-audit").glob(
            "10.1234_sample/attempt-*/fresh-markdown-quality.json"
        )
    )
    assert len(fresh_reports) == 1
    report = json.loads(fresh_reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "pass"


def test_check_snapshot_fresh_review_blocks_stale_pass_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    fake_agent = _write_fake_fresh_quality_agent(tmp_path, status="fail", issue_id="fresh-empty-figures")
    monkeypatch.setenv("PROVIDER_ONBOARDING_AGENT_CLI", f"{sys.executable} {fake_agent}")
    _write_check_snapshot_fixture(tmp_path, quality_status="pass")

    with pytest.raises(module.ToolError) as stale_pass:
        module.run_check_snapshot(argparse.Namespace(provider="newpub", doi="10.1234/sample"))

    assert stale_pass.value.code == "MARKDOWN_QUALITY_FAILED"
    assert stale_pass.value.details["markdown_quality_status"] == "pass"
    assert stale_pass.value.details["fresh_markdown_quality_status"] == "fail"
    assert stale_pass.value.details["issues"][0]["id"] == "fresh-empty-figures"


def _write_repair_fixture(
    root: Path,
    *,
    quality_status: str = "fail",
    issue_id: str = "broken-table",
    summary: str = "Table rows are semantically unusable.",
    evidence: str = "| orphan | row |",
) -> None:
    manifest_path = root / "onboarding" / "manifests" / "newpub.yml"
    review_path = root / "onboarding" / "reviews" / "newpub.yml"
    fixture_dir = root / "tests" / "fixtures" / "golden_criteria" / "10.1234_sample"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        """
name: newpub
markdown_contract:
  structure:
    doi: 10.1234/sample
    must_include:
    - "## Abstract"
    must_not_include:
    - Download PDF
fixtures:
  doi_samples:
    structure:
      doi: 10.1234/sample
""",
        encoding="utf-8",
    )
    (fixture_dir / "expected.json").write_text('{"expected_content_kind":"fulltext"}\n', encoding="utf-8")
    (fixture_dir / "extracted.md").write_text("# Demo\n\n## Abstract\n\n| orphan | row |\n", encoding="utf-8")
    (fixture_dir / "markdown-quality-prompt.md").write_text("Review prompt\n", encoding="utf-8")
    issue = None
    if quality_status == "fail":
        issue = {
            "id": issue_id,
            "severity": "high",
            "blocking": True,
            "summary": summary,
            "evidence": evidence,
        }
    (fixture_dir / "markdown-quality.json").write_text(
        json.dumps(_agent_quality_report(status=quality_status, issue=issue), indent=2) + "\n",
        encoding="utf-8",
    )
    (fixture_dir.parent / "manifest.json").write_text(
        json.dumps(
            {
                "samples": {
                    "10.1234_sample": {
                        "doi": "10.1234/sample",
                        "publisher": "newpub",
                        "fixture_family": "golden",
                        "expected_outcome": "fulltext",
                        "assets": {
                            "expected.json": "tests/fixtures/golden_criteria/10.1234_sample/expected.json",
                            "extracted.md": "tests/fixtures/golden_criteria/10.1234_sample/extracted.md",
                            "markdown-quality-prompt.md": (
                                "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality-prompt.md"
                            ),
                            "markdown-quality.json": (
                                "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json"
                            ),
                        },
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    review_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "provider": "newpub",
                "reviewed_at": "2026-05-23T00:00:00Z",
                "reviewed_by": "operator",
                "fixtures": [
                    {
                        "fixture": "tests/fixtures/golden_criteria/10.1234_sample",
                        "purpose": "structure",
                        "doi": "10.1234/sample",
                        "baseline_markdown_path": (
                            "tests/fixtures/golden_criteria/10.1234_sample/extracted.md"
                        ),
                        "baseline_markdown_sha256": "0" * 64,
                        "markdown_quality_path": (
                            "tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json"
                        ),
                        "markdown_quality_sha256": "1" * 64,
                        "review_notes": "Pending repair.",
                        "sample_representative": True,
                        "markdown_semantic_reviewed": False,
                        "issues": [],
                        "assertions": [],
                        "fixes": [],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_repair_markdown_quality_brief_includes_issue_scope_and_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    _write_repair_fixture(tmp_path)

    ctx = module._load_markdown_repair_context("newpub", "10.1234/sample")
    issues = module._markdown_repair_issues(ctx.quality_report)
    domains = module._infer_markdown_repair_domains(issues)
    allowed = module._markdown_repair_allowed_scope(ctx, domains)
    brief = module._markdown_repair_brief(
        ctx,
        attempt=1,
        max_attempts=3,
        domains=domains,
        allowed_scope=allowed,
    )

    assert "table" in brief["repair_domains"]
    assert brief["quality_issues"][0]["evidence"] == "| orphan | row |"
    assert "tests/fixtures/golden_criteria/10.1234_sample/**" in brief["files_allowed_to_modify"]
    assert "tests/unit/test_newpub_provider.py" in brief["files_allowed_to_modify"]
    assert "src/paper_fetch/extraction/markdown_render.py" in brief["files_allowed_to_modify"]
    assert "onboarding/known-providers.yml" in brief["files_must_not_modify"]
    assert any("scripts/snapshot_expected.py" in command for command in brief["verification_commands"][1])
    assert brief["required_order"][0].startswith("Add or update a provider-local regression test")


def test_repair_markdown_quality_requires_agent_for_fresh_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    (tmp_path / "empty-bin").mkdir()
    monkeypatch.delenv("PROVIDER_ONBOARDING_AGENT_CLI", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    _write_repair_fixture(tmp_path, quality_status="pending_agent_review")

    with pytest.raises(module.ToolError) as missing_agent:
        module.run_repair_markdown_quality(
            argparse.Namespace(
                provider="newpub",
                doi="10.1234/sample",
                state=str(tmp_path / "state.json"),
                output_dir=str(tmp_path / "run"),
                max_attempts=3,
            )
        )

    assert missing_agent.value.code == "WORKER_AGENT_CLI_MISSING"
    assert "install codex" in missing_agent.value.message
    assert missing_agent.value.details["default_dispatcher"].startswith("codex exec")


def test_repair_markdown_quality_fake_agent_success_records_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    _write_repair_fixture(tmp_path)
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        """
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

prompt = sys.stdin.read()
quality = Path("tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json")
if "Fresh Markdown Quality Review" in prompt:
    match = re.search(r"Fresh report to write: `([^`]+)`", prompt)
    report = json.loads(quality.read_text(encoding="utf-8"))
    report["status"] = "fail"
    report["issues"] = [{
        "id": "broken-table",
        "severity": "high",
        "blocking": True,
        "summary": "Fresh review still sees the broken table.",
        "evidence": "| orphan | row |"
    }]
    report["blocking_issue_count"] = 1
    report["reviewed_by"] = "fake-fresh-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    report["fresh_review"] = True
    Path(match.group(1)).parent.mkdir(parents=True, exist_ok=True)
    Path(match.group(1)).write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    print("fresh fail")
elif "Markdown quality repair review" in prompt:
    report = json.loads(quality.read_text(encoding="utf-8"))
    report["status"] = "pass"
    report["issues"] = []
    report["blocking_issue_count"] = 0
    report["reviewed_by"] = "fake-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    quality.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    print("quality pass")
else:
    Path("tests/unit").mkdir(parents=True, exist_ok=True)
    Path("tests/unit/test_newpub_provider.py").write_text("def test_regression():\\n    assert True\\n", encoding="utf-8")
    print("repair ok")
""",
        encoding="utf-8",
    )

    def fake_run_env(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setenv("PROVIDER_ONBOARDING_AGENT_CLI", f"{sys.executable} {fake_agent}")
    monkeypatch.setattr(module, "_run_env_command", fake_run_env)

    result = module.run_repair_markdown_quality(
        argparse.Namespace(
            provider="newpub",
            doi="10.1234/sample",
            state=str(tmp_path / "state.json"),
            output_dir=str(tmp_path / "run"),
            max_attempts=3,
        )
    )
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    repair = state["providers"]["newpub"]["repairs"]["markdown_quality"][0]
    review = yaml.safe_load((tmp_path / "onboarding" / "reviews" / "newpub.yml").read_text(encoding="utf-8"))

    assert result == 0
    assert repair["status"] == "passed"
    assert repair["attempts"] == 1
    assert repair["issue_ids"] == ["broken-table"]
    assert repair["quality_status"] == "pass"
    assert repair["review_artifact_updated"] is True
    assert (tmp_path / "run" / "markdown-quality" / "10.1234_sample" / "attempt-1" / "repair-brief.yml").is_file()
    assert review["fixtures"][0]["markdown_semantic_reviewed"] is False
    assert review["fixtures"][0]["markdown_quality_sha256"] != "1" * 64


def test_repair_markdown_quality_uses_fresh_failure_when_persistent_report_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    _write_repair_fixture(tmp_path, quality_status="pass")
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        """
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

prompt = sys.stdin.read()
quality = Path("tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json")
if "Fresh Markdown Quality Review" in prompt:
    match = re.search(r"Fresh report to write: `([^`]+)`", prompt)
    report = json.loads(quality.read_text(encoding="utf-8"))
    report["status"] = "fail"
    report["issues"] = [{
        "id": "fresh-broken-table",
        "severity": "high",
        "blocking": True,
        "summary": "Fresh review found a broken table.",
        "evidence": "| orphan | row |"
    }]
    report["blocking_issue_count"] = 1
    report["reviewed_by"] = "fake-fresh-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    report["fresh_review"] = True
    Path(match.group(1)).parent.mkdir(parents=True, exist_ok=True)
    Path(match.group(1)).write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
elif "Markdown quality repair review" in prompt:
    report = json.loads(quality.read_text(encoding="utf-8"))
    report["status"] = "pass"
    report["issues"] = []
    report["blocking_issue_count"] = 0
    report["reviewed_by"] = "fake-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    quality.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
else:
    Path("tests/unit").mkdir(parents=True, exist_ok=True)
    Path("tests/unit/test_newpub_provider.py").write_text("def test_regression():\\n    assert True\\n", encoding="utf-8")
print("ok")
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("PROVIDER_ONBOARDING_AGENT_CLI", f"{sys.executable} {fake_agent}")
    monkeypatch.setattr(
        module,
        "_run_env_command",
        lambda command: subprocess.CompletedProcess(command, 0, "ok\n", ""),
    )

    assert (
        module.run_repair_markdown_quality(
            argparse.Namespace(
                provider="newpub",
                doi="10.1234/sample",
                state=str(tmp_path / "state.json"),
                output_dir=str(tmp_path / "run"),
                max_attempts=3,
            )
        )
        == 0
    )
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    repair = state["providers"]["newpub"]["repairs"]["markdown_quality"][0]

    assert repair["issue_ids"] == ["fresh-broken-table"]
    assert repair["status"] == "passed"


def test_repair_markdown_quality_rejects_forbidden_agent_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    _write_repair_fixture(tmp_path)
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        """
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

prompt = sys.stdin.read()
if "Fresh Markdown Quality Review" in prompt:
    match = re.search(r"Fresh report to write: `([^`]+)`", prompt)
    report = json.loads(Path("tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json").read_text(encoding="utf-8"))
    report["status"] = "fail"
    report["issues"] = [{
        "id": "broken-table",
        "severity": "high",
        "blocking": True,
        "summary": "Fresh review found broken Markdown.",
        "evidence": "| orphan | row |"
    }]
    report["blocking_issue_count"] = 1
    report["reviewed_by"] = "fake-fresh-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    report["fresh_review"] = True
    Path(match.group(1)).parent.mkdir(parents=True, exist_ok=True)
    Path(match.group(1)).write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print("ok")
""",
        encoding="utf-8",
    )
    snapshots = iter([set(), set(), set(), {"docs/providers.md"}])
    _write_fake_codex_wrapper(tmp_path / "bin", fake_agent, repo_root=tmp_path)
    monkeypatch.delenv("PROVIDER_ONBOARDING_AGENT_CLI", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(module, "_workspace_changed_paths", lambda: next(snapshots))

    with pytest.raises(module.ToolError) as forbidden:
        module.run_repair_markdown_quality(
            argparse.Namespace(
                provider="newpub",
                doi="10.1234/sample",
                state=str(tmp_path / "state.json"),
                output_dir=str(tmp_path / "run"),
                max_attempts=3,
            )
        )

    assert forbidden.value.code == "WORKER_MODIFIED_FORBIDDEN_FILE"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    repair = state["providers"]["newpub"]["repairs"]["markdown_quality"][0]
    assert repair["failure"]["forbidden_paths"] == ["docs/providers.md"]


def test_repair_markdown_quality_fails_after_max_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("onboard_from_manifests")
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    _write_repair_fixture(tmp_path)
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        """
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

prompt = sys.stdin.read()
quality = Path("tests/fixtures/golden_criteria/10.1234_sample/markdown-quality.json")
if "Fresh Markdown Quality Review" in prompt:
    match = re.search(r"Fresh report to write: `([^`]+)`", prompt)
    report = json.loads(quality.read_text(encoding="utf-8"))
    report["status"] = "fail"
    report["issues"] = [{
        "id": "broken-table",
        "severity": "high",
        "blocking": True,
        "summary": "Fresh review still sees the broken table.",
        "evidence": "| orphan | row |"
    }]
    report["blocking_issue_count"] = 1
    report["reviewed_by"] = "fake-fresh-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    report["fresh_review"] = True
    Path(match.group(1)).parent.mkdir(parents=True, exist_ok=True)
    Path(match.group(1)).write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
elif "Markdown quality repair review" in prompt:
    report = json.loads(quality.read_text(encoding="utf-8"))
    report["status"] = "fail"
    report["issues"] = [{
        "id": "broken-table",
        "severity": "high",
        "blocking": True,
        "summary": "Still broken.",
        "evidence": "| orphan | row |"
    }]
    report["blocking_issue_count"] = 1
    report["reviewed_by"] = "fake-agent"
    report["reviewed_at"] = "2026-05-23T00:00:00Z"
    quality.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print("done")
""",
        encoding="utf-8",
    )

    def fake_run_env(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setenv("PROVIDER_ONBOARDING_AGENT_CLI", f"{sys.executable} {fake_agent}")
    monkeypatch.setattr(module, "_run_env_command", fake_run_env)

    with pytest.raises(module.ToolError) as failed:
        module.run_repair_markdown_quality(
            argparse.Namespace(
                provider="newpub",
                doi="10.1234/sample",
                state=str(tmp_path / "state.json"),
                output_dir=str(tmp_path / "run"),
                max_attempts=3,
            )
        )

    assert failed.value.code == "MARKDOWN_QUALITY_REPAIR_FAILED"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    repair = state["providers"]["newpub"]["repairs"]["markdown_quality"][0]
    assert repair["status"] == "failed"
    assert repair["attempts"] == 3
    assert repair["quality_status"] == "fail"


def test_onboard_script_does_not_import_llm_sdks() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8").lower()

    assert "anthropic" not in script
    assert "openai" not in script
