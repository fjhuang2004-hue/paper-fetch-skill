from __future__ import annotations

import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from ._manifest_sync import (
    MANIFESTS_DIR,
    load_manifest_schema,
    load_yaml,
)


REQUIRED_DOI_PURPOSES = {"structure", "figure", "references"}
PLACEHOLDER_PATTERN = re.compile(r"\b(?:todo|tbd|unknown)\b", re.IGNORECASE)


def load_schema():
    return load_manifest_schema()


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
        manifest = load_yaml(manifest_path)
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
