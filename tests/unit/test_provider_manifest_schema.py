from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
ONBOARDING_DIR = REPO_ROOT / "docs" / "ai-onboarding"
SCHEMA_PATH = ONBOARDING_DIR / "provider-manifest.schema.json"
MANIFESTS_DIR = ONBOARDING_DIR / "manifests"
REQUIRED_DOI_PURPOSES = {"structure", "figure", "references"}
PLACEHOLDER_PATTERN = re.compile(r"\b(?:todo|tbd|unknown)\b", re.IGNORECASE)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must load as a mapping"
    return data


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def iter_manifest_paths() -> list[Path]:
    return sorted(MANIFESTS_DIR.glob("*.yml"))


def test_provider_manifest_schema_is_valid_json_schema() -> None:
    schema = load_schema()

    Draft202012Validator.check_schema(schema)


def test_all_provider_manifests_pass_schema_and_local_invariants() -> None:
    schema = load_schema()
    validator = Draft202012Validator(schema)
    manifest_paths = iter_manifest_paths()
    assert manifest_paths

    for manifest_path in manifest_paths:
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle)
        errors = sorted(validator.iter_errors(manifest), key=lambda error: error.json_path)
        assert not errors, [
            f"{manifest_path}: {error.json_path}: {error.message}" for error in errors
        ]

        assert manifest["name"] == manifest_path.stem
        assert isinstance(manifest["main_path"], list)
        assert manifest["main_path"], f"{manifest_path}: main_path must not be empty"
        assert isinstance(manifest["docs"], dict)
        assert manifest["docs"]["providers_md_capability_row"]
        assert manifest["docs"]["changelog_summary"]
        doi_samples = manifest["fixtures"]["doi_samples"]
        for purpose in REQUIRED_DOI_PURPOSES:
            assert doi_samples[purpose]["doi"], f"{manifest_path}: {purpose} DOI is required"

        rendered = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=True)
        assert not PLACEHOLDER_PATTERN.search(rendered), manifest_path
