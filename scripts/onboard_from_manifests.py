#!/usr/bin/env python3
"""Generate provider onboarding task DAGs and worker briefs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _structured_errors import ToolError, emit_error, error_payload  # noqa: E402


PROVIDER_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
SCHEMA_PATH = "docs/ai-onboarding/provider-manifest.schema.json"
HARD_CONSTRAINTS_PATH = "docs/ai-onboarding/hard-constraints.md"
FAILURE_RECOVERY_PATH = "docs/ai-onboarding/failure-recovery.md"
STATE_SCHEMA_PATH = "docs/ai-onboarding/onboarding-state.schema.json"
DEFAULT_STATE_PATH = "docs/ai-onboarding/onboarding-state.json"
AGENT_CLI_ENV = "PROVIDER_ONBOARDING_AGENT_CLI"
DISCOVER_STEP = "discover-manifest"
IMPLEMENT_STEP = "implement-provider"
MAX_WORKER_RETRIES = 3
ROUTING_REQUIREMENTS = [
    "doi_prefixes",
    "domains",
    "domain_suffixes",
    "crossref_publisher",
]
DOI_SAMPLE_PURPOSES = [
    "structure",
    "table",
    "formula",
    "figure",
    "supplementary",
    "references",
    "pdf_fallback",
    "abstract_only",
    "access_gate",
    "empty_shell",
]
FILES_MUST_NOT_MODIFY = [
    "src/",
    "tests/",
    "docs/providers.md",
    "CHANGELOG.md",
]
SHARED_FILES_MUST_NOT_MODIFY = [
    "docs/ai-onboarding/known-providers.yml",
    "docs/providers.md",
    "docs/extraction-rules.md",
    "CHANGELOG.md",
]
CENTRAL_PROVIDER_LOGIC_PATHS = [
    "src/paper_fetch/extraction/html/provider_rules.py",
    "src/paper_fetch/quality/html_signals.py",
    "src/paper_fetch/quality/html_availability.py",
]


class CoordinatorArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit_error(
            error_payload(
                "TASK_BRIEF_INVALID",
                message,
                provider=None,
                manifest=None,
                task_id="coordinator-parse-args",
                retryable=False,
                details={"reason": message},
            )
        )
        raise SystemExit(2)


class DagStep(NamedTuple):
    id: str
    type: str
    owner: str
    brief: str | None = None
    command: tuple[str, ...] = ()


TASK_DAG: tuple[DagStep, ...] = (
    DagStep(
        id=DISCOVER_STEP,
        type="worker-brief",
        owner="coordinator-subagent",
        brief="briefs/discover-manifest.yml",
    ),
    DagStep(id="validate-manifest", type="coordinator-check", owner="coordinator"),
    DagStep(id="capture-fixtures", type="coordinator-action", owner="coordinator"),
    DagStep(id="scaffold", type="coordinator-action", owner="coordinator"),
    DagStep(
        id=IMPLEMENT_STEP,
        type="worker-brief",
        owner="coordinator-subagent",
        brief="briefs/implement-provider.yml",
    ),
    DagStep(id="snapshot-expected", type="coordinator-action", owner="coordinator"),
    DagStep(id="manifest-sync-back", type="coordinator-action", owner="coordinator"),
    DagStep(id="provider-local-acceptance", type="coordinator-check", owner="coordinator"),
    DagStep(id="global-lint", type="coordinator-check", owner="coordinator"),
    DagStep(id="merge-ready", type="coordinator-action", owner="coordinator"),
)


class OnboardingSource(NamedTuple):
    provider: str
    manifest: str
    include_discovery: bool
    manifest_yaml: str | None


def _provider_slug(provider: str) -> str:
    slug = provider.strip().lower()
    if not slug:
        raise ValueError("provider must not be empty")
    if not PROVIDER_RE.fullmatch(slug):
        raise ValueError("provider must be snake_case starting with a lowercase letter")
    return slug


def default_manifest_path(provider: str) -> str:
    return f"docs/ai-onboarding/manifests/{_provider_slug(provider)}.yml"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ToolError(
            "MANIFEST_NOT_FOUND",
            "Provider manifest was not found.",
            retryable=False,
            manifest=path.as_posix(),
            task_id="start-validate-manifest",
            details={"path": path.as_posix()},
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ToolError(
            "MANIFEST_SCHEMA_INVALID",
            "Manifest YAML is invalid.",
            retryable=False,
            manifest=path.as_posix(),
            task_id="start-validate-manifest",
            details={"reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise ToolError(
            "MANIFEST_SCHEMA_INVALID",
            "Manifest root must be a mapping.",
            retryable=False,
            manifest=path.as_posix(),
            task_id="start-validate-manifest",
            details={"path": path.as_posix()},
        )
    return data


def _manifest_source(path_value: str) -> OnboardingSource:
    manifest_path = Path(path_value)
    if not manifest_path.is_absolute():
        manifest_path = _repo_root() / manifest_path
    manifest = _read_manifest(manifest_path)
    provider_value = manifest.get("name")
    if not isinstance(provider_value, str):
        raise ToolError(
            "MANIFEST_SCHEMA_INVALID",
            "Manifest must contain string name.",
            retryable=False,
            manifest=path_value,
            task_id="start-validate-manifest",
            details={"field": "name", "expected": "string"},
        )
    provider = _provider_slug(provider_value)
    manifest_yaml = manifest_path.read_text(encoding="utf-8")
    return OnboardingSource(
        provider=provider,
        manifest=path_value,
        include_discovery=False,
        manifest_yaml=manifest_yaml,
    )


def _provider_source(
    *,
    provider: str,
    domain: str | None,
    doi_prefix: str | None,
) -> OnboardingSource:
    del domain, doi_prefix
    provider_name = _provider_slug(provider)
    return OnboardingSource(
        provider=provider_name,
        manifest=default_manifest_path(provider_name),
        include_discovery=True,
        manifest_yaml=None,
    )


def build_discover_brief(
    *,
    provider: str,
    domain: str | None,
    doi_prefix: str | None,
    output_manifest: str,
) -> dict[str, Any]:
    """Build the worker input for the manifest discovery task."""
    provider_name = _provider_slug(provider)
    return {
        "task_id": f"{provider_name}-{DISCOVER_STEP}",
        "current_step": DISCOVER_STEP,
        "runtime": "coding-agent-subagent",
        "provider_seed": {
            "name": provider_name,
            "domain": domain,
            "doi_prefix_hint": doi_prefix,
        },
        "output_manifest": output_manifest,
        "schema": SCHEMA_PATH,
        "hard_constraints": HARD_CONSTRAINTS_PATH,
        "search_requirements": {
            "routing": ROUTING_REQUIREMENTS,
            "doi_sample_purposes": DOI_SAMPLE_PURPOSES,
        },
        "output_requirements": {
            "generation_generated_by": "ai_discovery",
            "doi_sample_evidence_keys": [
                "doi",
                "evidence_url",
                "evidence_reason",
                "observed_signals",
                "confidence",
            ],
            "required_non_null_sample_purposes": [
                "structure",
                "figure",
                "references",
            ],
            "retry_error_code": "UNSUITABLE_DOI_SAMPLE",
        },
        "files_allowed_to_modify": [output_manifest],
        "files_must_not_modify": FILES_MUST_NOT_MODIFY,
        "no_commit": True,
    }


def _implementation_allowed_files(provider: str) -> list[str]:
    provider_name = _provider_slug(provider)
    return [
        f"src/paper_fetch/providers/{provider_name}.py",
        f"src/paper_fetch/providers/_{provider_name}_html.py",
        f"tests/unit/test_{provider_name}_provider.py",
    ]


def _implementation_forbidden_files(manifest: str) -> list[str]:
    return [
        manifest,
        *SHARED_FILES_MUST_NOT_MODIFY,
        "src/paper_fetch/provider_catalog.py",
        *CENTRAL_PROVIDER_LOGIC_PATHS,
    ]


def build_implementation_brief(
    *,
    provider: str,
    manifest: str,
    manifest_yaml: str | None = None,
) -> dict[str, Any]:
    """Build the worker input for provider implementation."""
    provider_name = _provider_slug(provider)
    brief: dict[str, Any] = {
        "task_id": f"{provider_name}-{IMPLEMENT_STEP}",
        "provider_manifest": manifest,
        "current_step": IMPLEMENT_STEP,
        "runtime": "coding-agent-subagent",
        "upstream_artifacts": {
            "task_dag": "task-dag.json",
            "capture_commands": f"docs/ai-onboarding/capture-commands/{provider_name}.txt",
            "scaffold_summary": f"docs/ai-onboarding/scaffold/{provider_name}.json",
        },
        "hard_constraints": HARD_CONSTRAINTS_PATH,
        "markdown_review_loop": {
            "required": True,
            "fixture_source": "provider_manifest.fixtures.doi_samples",
            "require_each_non_null_purpose_asserted": True,
            "require_positive_and_negative_markdown_assertions": True,
            "forbid_skipped_scaffold_placeholder": True,
        },
        "output_requirements": {
            "reviewed_fixtures": "one entry per non-null provider_manifest.fixtures.doi_samples purpose",
            "reviewed_fixture_fields": [
                "fixture",
                "purpose",
                "issue",
                "assertion",
                "fix",
            ],
        },
        "acceptance": {
            "pytest": [
                f"PYTHONPATH=src python3 -m pytest tests/unit/test_{provider_name}_provider.py -q",
                "PYTHONPATH=src python3 -m pytest "
                "tests/unit/test_provider_markdown_review_contract.py -q",
                "PYTHONPATH=src python3 -m pytest "
                "tests/unit/test_provider_bundle_completeness.py "
                "tests/unit/test_provider_owner_reuse.py -q",
            ],
            "grep_must_be_empty": [
                {
                    "pattern": provider_name,
                    "paths": CENTRAL_PROVIDER_LOGIC_PATHS,
                }
            ],
        },
        "files_allowed_to_modify": _implementation_allowed_files(provider_name),
        "files_must_not_modify": _implementation_forbidden_files(manifest),
        "failure_recovery": {
            "policy": FAILURE_RECOVERY_PATH,
            "max_retries": MAX_WORKER_RETRIES,
            "forbidden_write_code": "WORKER_MODIFIED_FORBIDDEN_FILE",
            "acceptance_failure_retry_task": IMPLEMENT_STEP,
            "blocked_after_retry_exhaustion": True,
        },
        "no_commit": True,
    }
    if manifest_yaml is not None:
        brief["manifest_yaml"] = manifest_yaml
    return brief


def build_dag(
    *,
    provider: str | None,
    manifest: str | None,
    include_discovery: bool,
    dry_run: bool,
) -> dict[str, Any]:
    provider_name = _provider_slug(provider) if provider else None
    steps: list[dict[str, Any]] = []
    previous_step: str | None = None
    for step in TASK_DAG:
        if step.id == DISCOVER_STEP and not include_discovery:
            continue
        item: dict[str, Any] = {
            "id": step.id,
            "type": step.type,
            "owner": step.owner,
            "depends_on": [previous_step] if previous_step else [],
            "retry_limit": MAX_WORKER_RETRIES if step.type == "worker-brief" else 0,
        }
        if step.brief is not None:
            item["brief"] = step.brief
        if step.command:
            item["command"] = list(step.command)
        if step.id == DISCOVER_STEP and manifest is not None:
            item["produces"] = [manifest]
        steps.append(item)
        previous_step = step.id
    return {
        "provider": provider_name,
        "manifest": manifest,
        "dry_run": dry_run,
        "runtime": "coding-agent-subagent",
        "agent_cli_env": AGENT_CLI_ENV,
        "state_schema": STATE_SCHEMA_PATH,
        "serial": {
            "single_provider": True,
            "single_task": True,
            "no_matrix": True,
        },
        "steps": steps,
    }


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    if any(char in text for char in [":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", '"']):
        return json.dumps(text)
    if text.lower() in {"null", "true", "false", "yes", "no"}:
        return json.dumps(text)
    return text


def to_yaml(data: Any, *, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(to_yaml(value, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
    elif isinstance(data, list):
        if not data:
            lines.append(f"{prefix}[]")
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(to_yaml(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
    else:
        lines.append(f"{prefix}{_yaml_scalar(data)}")
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _state_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _repo_root() / path


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "agent_cli": os.environ.get(AGENT_CLI_ENV),
        "active_provider": None,
        "providers": {},
    }


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"state root must be an object: {path}")
    data.setdefault("schema_version", 1)
    data.setdefault("agent_cli", os.environ.get(AGENT_CLI_ENV))
    data.setdefault("active_provider", None)
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise ValueError(f"state providers must be an object: {path}")
    return data


def _dag_step_ids(include_discovery: bool) -> tuple[str, ...]:
    return tuple(
        step.id for step in TASK_DAG if include_discovery or step.id != DISCOVER_STEP
    )


def _task_statuses(step_ids: tuple[str, ...]) -> dict[str, str]:
    return {
        step_id: "in_progress" if index == 0 else "pending"
        for index, step_id in enumerate(step_ids)
    }


def _ensure_single_active_provider(state: dict[str, Any], provider: str) -> None:
    active_provider = state.get("active_provider")
    if active_provider not in {None, provider}:
        providers = state.get("providers", {})
        active_state = providers.get(active_provider, {})
        if active_state.get("status") == "in_progress":
            raise ToolError(
                "TASK_BRIEF_INVALID",
                "another provider is already in_progress: "
                f"{active_provider}; finish or block it before starting {provider}",
                retryable=False,
                provider=provider,
                task_id=f"{provider}-coordinator-state-conflict",
                details={"active_provider": active_provider},
            )


def _ensure_provider_state(
    state: dict[str, Any],
    *,
    provider: str,
    manifest: str | None = None,
    include_discovery: bool = True,
) -> dict[str, Any]:
    provider_name = _provider_slug(provider)
    _ensure_single_active_provider(state, provider_name)
    providers = state["providers"]
    current = providers.get(provider_name)
    if isinstance(current, dict):
        return current
    step_ids = _dag_step_ids(include_discovery)
    provider_state = {
        "provider": provider_name,
        "manifest": manifest or default_manifest_path(provider_name),
        "status": "in_progress",
        "current_step": step_ids[0],
        "steps": list(step_ids),
        "completed_steps": [],
        "task_statuses": _task_statuses(step_ids),
        "retry_counts": {step_id: 0 for step_id in step_ids},
        "verifications": {},
    }
    providers[provider_name] = provider_state
    state["active_provider"] = provider_name
    return provider_state


def _next_pending_step(provider_state: dict[str, Any]) -> str | None:
    task_statuses = provider_state["task_statuses"]
    for step_id in provider_state["steps"]:
        if task_statuses.get(step_id) == "in_progress":
            return str(step_id)
    for step_id in provider_state["steps"]:
        if task_statuses.get(step_id) == "pending":
            task_statuses[step_id] = "in_progress"
            provider_state["current_step"] = step_id
            return str(step_id)
    provider_state["current_step"] = None
    return None


def _verify_commands(provider: str, task: str) -> list[list[str]]:
    provider_name = _provider_slug(provider)
    command_map: dict[str, list[list[str]]] = {
        "validate-manifest": [
            [
                "PYTHONPATH=src",
                "python3",
                "-m",
                "pytest",
                "tests/unit/test_provider_manifest_schema.py",
                "tests/unit/test_known_providers_sync.py",
                "-q",
            ]
        ],
        "capture-fixtures": [
            [
                "test",
                "-f",
                f"docs/ai-onboarding/capture-commands/{provider_name}.txt",
            ]
        ],
        "scaffold": [
            [
                "python3",
                "scripts/scaffold_provider.py",
                "--from-manifest",
                default_manifest_path(provider_name),
            ]
        ],
        IMPLEMENT_STEP: [
            [
                "PYTHONPATH=src",
                "python3",
                "-m",
                "pytest",
                f"tests/unit/test_{provider_name}_provider.py",
                "-q",
            ],
            [
                "PYTHONPATH=src",
                "python3",
                "-m",
                "pytest",
                "tests/unit/test_provider_markdown_review_contract.py",
                "-q",
            ],
            [
                "git",
                "grep",
                "-n",
                provider_name,
                "--",
                *CENTRAL_PROVIDER_LOGIC_PATHS,
            ],
        ],
        "snapshot-expected": [
            ["python3", "scripts/snapshot_expected.py", "--help"]
        ],
        "manifest-sync-back": [
            [
                "python3",
                "scripts/manifest_sync_back.py",
                "--provider",
                provider_name,
                "--manifest",
                default_manifest_path(provider_name),
            ]
        ],
        "provider-local-acceptance": [
            [
                "PYTHONPATH=src",
                "python3",
                "-m",
                "pytest",
                f"tests/unit/test_{provider_name}_provider.py",
                "-q",
            ],
            [
                "PYTHONPATH=src",
                "python3",
                "-m",
                "pytest",
                "tests/unit/test_provider_markdown_review_contract.py",
                "-q",
            ],
            [
                "git",
                "grep",
                "-n",
                provider_name,
                "--",
                *CENTRAL_PROVIDER_LOGIC_PATHS,
            ],
        ],
        "global-lint": [
            [
                "PYTHONPATH=src",
                "python3",
                "-m",
                "pytest",
                "tests/unit/test_manifest_bundle_sync.py",
                "tests/unit/test_provider_owner_reuse.py",
                "tests/unit/test_provider_bundle_completeness.py",
                "tests/unit/test_import_boundaries.py",
                "tests/unit/test_extraction_rules_validator.py",
                "-q",
            ]
        ],
        "merge-ready": [
            [
                "git",
                "diff",
                "--",
                default_manifest_path(provider_name),
                "docs/ai-onboarding/known-providers.yml",
                "docs/providers.md",
                "CHANGELOG.md",
            ]
        ],
    }
    return command_map.get(task, [])


def run_discover(args: argparse.Namespace) -> int:
    brief = build_discover_brief(
        provider=args.provider,
        domain=args.domain,
        doi_prefix=args.doi_prefix,
        output_manifest=args.output,
    )
    print(to_yaml(brief))
    return 0


def run_start(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    if args.manifest:
        source = _manifest_source(args.manifest)
    else:
        source = _provider_source(
            provider=args.provider,
            domain=args.domain,
            doi_prefix=args.doi_prefix,
        )

    dag = build_dag(
        provider=source.provider,
        manifest=source.manifest,
        include_discovery=source.include_discovery,
        dry_run=args.dry_run,
    )
    implementation_brief = build_implementation_brief(
        provider=source.provider,
        manifest=source.manifest,
        manifest_yaml=source.manifest_yaml,
    )
    write_text(
        output_dir / "task-dag.json",
        json.dumps(dag, indent=2, sort_keys=True) + "\n",
    )
    write_text(
        output_dir / "briefs" / "implement-provider.yml",
        to_yaml(implementation_brief) + "\n",
    )

    if source.include_discovery:
        discover_brief = build_discover_brief(
            provider=source.provider,
            domain=args.domain,
            doi_prefix=args.doi_prefix,
            output_manifest=source.manifest,
        )
        write_text(
            output_dir / "briefs" / "discover-manifest.yml",
            to_yaml(discover_brief) + "\n",
        )
    if args.dry_run:
        return 0

    state_path = _state_path(args.state)
    state = _load_state(state_path)
    _ensure_provider_state(
        state,
        provider=source.provider,
        manifest=source.manifest,
        include_discovery=source.include_discovery,
    )
    _write_json(state_path, state)
    return 0


def run_next(args: argparse.Namespace) -> int:
    provider = _provider_slug(args.provider)
    state_path = _state_path(args.state)
    state = _load_state(state_path)
    provider_state = _ensure_provider_state(state, provider=provider)
    step_id = _next_pending_step(provider_state)
    _write_json(state_path, state)
    print(
        json.dumps(
            {
                "provider": provider,
                "status": provider_state["status"],
                "current_step": step_id,
                "state": str(state_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def run_verify(args: argparse.Namespace) -> int:
    provider = _provider_slug(args.provider)
    if args.task not in _dag_step_ids(include_discovery=True):
        raise ToolError(
            "TASK_BRIEF_INVALID",
            f"unknown task for provider {provider}: {args.task}",
            retryable=False,
            provider=provider,
            task_id=f"{provider}-verify-{args.task}",
            details={"task": args.task},
        )
    state_path = _state_path(args.state)
    state = _load_state(state_path)
    provider_state = _ensure_provider_state(state, provider=provider)
    commands = _verify_commands(provider, args.task)
    verifications = provider_state.setdefault("verifications", {})
    verifications[args.task] = {
        "dry_run": True,
        "commands": commands,
        "result": "planned",
    }
    _write_json(state_path, state)
    print(
        json.dumps(
            {
                "provider": provider,
                "task": args.task,
                "dry_run": True,
                "commands": commands,
                "result": "planned",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def run_advance(args: argparse.Namespace) -> int:
    provider = _provider_slug(args.provider)
    state_path = _state_path(args.state)
    state = _load_state(state_path)
    provider_state = _ensure_provider_state(state, provider=provider)
    task_statuses = provider_state["task_statuses"]
    if args.task not in task_statuses:
        raise ToolError(
            "TASK_BRIEF_INVALID",
            f"unknown task for provider {provider}: {args.task}",
            retryable=False,
            provider=provider,
            task_id=f"{provider}-advance-{args.task}",
            details={"task": args.task},
        )
    task_statuses[args.task] = "completed"
    completed_steps = provider_state["completed_steps"]
    if args.task not in completed_steps:
        completed_steps.append(args.task)
    provider_state["current_step"] = None
    next_step = _next_pending_step(provider_state)
    if next_step is None:
        provider_state["status"] = "merge_ready"
        state["active_provider"] = None
    else:
        provider_state["status"] = "in_progress"
        state["active_provider"] = provider
    _write_json(state_path, state)
    print(
        json.dumps(
            {
                "provider": provider,
                "advanced": args.task,
                "status": provider_state["status"],
                "next_step": next_step,
                "state": str(state_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = CoordinatorArgumentParser(
        description="Generate manifest-driven provider onboarding dry-run artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=CoordinatorArgumentParser)

    discover = subparsers.add_parser(
        "discover",
        help="print a manifest discovery worker brief",
    )
    discover.add_argument("--provider", required=True, help="provider name seed")
    discover.add_argument("--domain", help="provider domain seed")
    discover.add_argument("--doi-prefix", help="DOI prefix seed")
    discover.add_argument(
        "--output",
        required=True,
        help="manifest path the discovery worker is allowed to write",
    )
    discover.set_defaults(func=run_discover)

    start = subparsers.add_parser(
        "start",
        help="write a dry-run onboarding DAG and worker briefs",
    )
    source = start.add_mutually_exclusive_group(required=True)
    source.add_argument("--provider", help="provider name seed")
    source.add_argument("--manifest", help="existing manifest path for replay mode")
    start.add_argument("--domain", help="provider domain seed")
    start.add_argument("--doi-prefix", help="DOI prefix seed")
    start.add_argument("--dry-run", action="store_true", help="write planned artifacts only")
    start.add_argument("--output-dir", required=True, help="directory for dry-run artifacts")
    start.add_argument(
        "--state",
        default=DEFAULT_STATE_PATH,
        help="coordinator state JSON path",
    )
    start.set_defaults(func=run_start)

    next_task = subparsers.add_parser(
        "next",
        help="print and persist the next serial task for one provider",
    )
    next_task.add_argument("--provider", required=True, help="provider name")
    next_task.add_argument(
        "--state",
        default=DEFAULT_STATE_PATH,
        help="coordinator state JSON path",
    )
    next_task.set_defaults(func=run_next)

    verify = subparsers.add_parser(
        "verify",
        help="write dry-run verification plan for a provider task",
    )
    verify.add_argument("--provider", required=True, help="provider name")
    verify.add_argument("--task", required=True, help="task id to verify")
    verify.add_argument(
        "--state",
        default=DEFAULT_STATE_PATH,
        help="coordinator state JSON path",
    )
    verify.set_defaults(func=run_verify)

    advance = subparsers.add_parser(
        "advance",
        help="mark a task complete and persist the next serial task",
    )
    advance.add_argument("--provider", required=True, help="provider name")
    advance.add_argument("--task", required=True, help="task id to mark complete")
    advance.add_argument(
        "--state",
        default=DEFAULT_STATE_PATH,
        help="coordinator state JSON path",
    )
    advance.set_defaults(func=run_advance)

    return parser


def _provider_from_args(args: argparse.Namespace) -> str | None:
    provider = getattr(args, "provider", None)
    if isinstance(provider, str):
        try:
            return _provider_slug(provider)
        except ValueError:
            return provider
    return None


def _manifest_from_args(args: argparse.Namespace) -> str | None:
    manifest = getattr(args, "manifest", None)
    return manifest if isinstance(manifest, str) else None


def _task_id_from_args(args: argparse.Namespace) -> str:
    provider = _provider_from_args(args)
    command = getattr(args, "command", None) or "coordinator"
    task = getattr(args, "task", None)
    if provider and task:
        return f"{provider}-{command}-{task}"
    if provider:
        return f"{provider}-{command}"
    return str(command)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ToolError as exc:
        emit_error(
            error_payload(
                exc.code,
                exc.message,
                provider=exc.provider or _provider_from_args(args),
                manifest=exc.manifest or _manifest_from_args(args),
                task_id=exc.task_id or _task_id_from_args(args),
                retryable=exc.retryable,
                details=exc.details,
            )
        )
        return 1
    except ValueError as exc:
        emit_error(
            error_payload(
                "TASK_BRIEF_INVALID",
                str(exc),
                provider=_provider_from_args(args),
                manifest=_manifest_from_args(args),
                task_id=_task_id_from_args(args),
                retryable=False,
                details={"reason": str(exc)},
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
