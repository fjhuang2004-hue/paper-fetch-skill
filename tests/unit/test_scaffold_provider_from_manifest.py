from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "scaffold_provider.py"
ARXIV_MANIFEST = REPO_ROOT / "docs" / "ai-onboarding" / "manifests" / "arxiv.yml"
WILEY_MANIFEST = REPO_ROOT / "docs" / "ai-onboarding" / "manifests" / "wiley.yml"


def _run_from_manifest(
    tmp_path: Path,
    manifest_path: Path = ARXIV_MANIFEST,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--from-manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _load_arxiv_manifest() -> dict[str, object]:
    data = yaml.safe_load(ARXIV_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_help_includes_from_manifest() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert "--from-manifest" in result.stdout


def test_from_manifest_generates_scaffold_and_json_summary(tmp_path: Path) -> None:
    result = _run_from_manifest(tmp_path)
    summary = json.loads(result.stdout)
    manifest = _load_arxiv_manifest()
    doi_samples = manifest["fixtures"]["doi_samples"]  # type: ignore[index]
    non_null_dois = {
        sample["doi"] for sample in doi_samples.values() if sample["doi"] is not None
    }

    assert summary["status"] == "OK"
    assert summary["provider"] == "arxiv"
    assert "src/paper_fetch/providers/_arxiv_html.py" in summary["generated_files"]
    assert "src/paper_fetch/providers/arxiv.py" in summary["generated_files"]
    assert "docs/ai-onboarding/capture-commands/arxiv.txt" in summary["generated_files"]

    assert (tmp_path / "src/paper_fetch/providers/_arxiv_html.py").is_file()
    assert (tmp_path / "src/paper_fetch/providers/arxiv.py").is_file()
    assert (tmp_path / "tests/unit/test_arxiv_provider.py").is_file()
    generated_test = (tmp_path / "tests/unit/test_arxiv_provider.py").read_text(
        encoding="utf-8"
    )
    assert "test_markdown_review_loop_contract_placeholder" in generated_test
    assert "pytest.mark.skip" not in generated_test
    assert "test_provider_golden_replay_placeholder" not in generated_test
    for doi in non_null_dois:
        assert (
            tmp_path
            / "tests"
            / "fixtures"
            / "golden_criteria"
            / doi.replace("/", "_")
            / ".gitkeep"
        ).is_file()

    generated_manifest = json.loads(
        (
            tmp_path / "tests/fixtures/golden_criteria/manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert set(generated_manifest["samples"]) == {
        doi.replace("/", "_") for doi in non_null_dois
    }
    assert generated_manifest["samples"]["10.48550_arxiv.2605.06663v1"][
        "fixture_purposes"
    ] == ["structure", "table", "references"]

    providers_doc = (tmp_path / "docs/providers.md").read_text(encoding="utf-8")
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    extraction_rules = (tmp_path / "docs/extraction-rules.md").read_text(
        encoding="utf-8"
    )
    assert manifest["docs"]["providers_md_capability_row"] in providers_doc  # type: ignore[index]
    assert "<!-- TODO(scaffold-arxiv): fill -->" in providers_doc
    assert manifest["docs"]["changelog_summary"] in changelog  # type: ignore[index]
    assert "skipped: manifest docs.extraction_rules_summary is null" in extraction_rules

    capture_commands = (
        tmp_path / "docs/ai-onboarding/capture-commands/arxiv.txt"
    ).read_text(encoding="utf-8")
    assert "--purpose structure" in capture_commands
    assert "--purpose pdf_fallback" in capture_commands
    assert "skipped: supplementary has null DOI in manifest" in capture_commands
    assert "--purpose supplementary" not in capture_commands

    client_text = (tmp_path / "src/paper_fetch/providers/arxiv.py").read_text(
        encoding="utf-8"
    )
    labels = re.findall(r'label="([^"]+)"', client_text)
    assert labels == manifest["main_path"]
    assert "arxiv_fetch_article_html_step" in client_text
    assert "arxiv_fetch_pdf_fallback_step" in client_text
    assert "arxiv_fetch_metadata_only_step" in client_text


def test_from_manifest_generated_provider_modules_import(tmp_path: Path) -> None:
    manifest = _load_arxiv_manifest()
    manifest["name"] = "newmanifest"
    manifest["display_source"] = "newmanifest_html"
    manifest["routing"]["publisher_aliases"] = ["newmanifest"]  # type: ignore[index]
    manifest_path = tmp_path / "newmanifest.yml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    out_dir = tmp_path / "out"

    _run_from_manifest(out_dir, manifest_path)

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
import importlib
from pathlib import Path

import paper_fetch.providers as provider_entries
from paper_fetch.providers._registry import provider_bundle

provider_entries.__path__ = [
    str(Path({str(out_dir)!r}) / "src/paper_fetch/providers"),
    *list(provider_entries.__path__),
]
importlib.import_module("paper_fetch.providers._newmanifest_html")
client_module = importlib.import_module("paper_fetch.providers.newmanifest")
bundle = provider_bundle("newmanifest")
assert bundle.catalog.name == "newmanifest"
assert bundle.catalog.doi_prefixes == ("10.48550/",)
assert bundle.catalog.asset_default == "body"
assert [step.label for step in client_module.NewmanifestClient.waterfall_steps] == [
    "article_html",
    "pdf_fallback",
    "metadata_only",
]
""",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )

    assert probe.returncode == 0, probe.stderr


def test_from_manifest_routing_and_probe_fields_enter_provider_spec(
    tmp_path: Path,
) -> None:
    _run_from_manifest(tmp_path)

    html_text = (tmp_path / "src/paper_fetch/providers/_arxiv_html.py").read_text(
        encoding="utf-8"
    )

    assert 'domains=("arxiv.org",)' in html_text
    assert 'doi_prefixes=("10.48550/",)' in html_text
    assert "domain_suffixes=()" in html_text
    assert 'publisher_aliases=("arxiv",)' in html_text
    assert 'asset_default="body"' in html_text
    assert "env_requirements=()" in html_text
    assert "requires_playwright=False" in html_text
    assert "requires_flaresolverr=False" in html_text
    assert "status_order=999" in html_text


def test_from_manifest_probe_requirements_enter_provider_spec(tmp_path: Path) -> None:
    _run_from_manifest(tmp_path, WILEY_MANIFEST)

    html_text = (tmp_path / "src/paper_fetch/providers/_wiley_html.py").read_text(
        encoding="utf-8"
    )

    assert 'env_requirements=("CROSSREF_MAILTO", "FLARESOLVERR_ENV_FILE")' in html_text
    assert "requires_playwright=True" in html_text
    assert "requires_flaresolverr=True" in html_text
    assert '# body=("figures", "body_tables", "formula_images")' in html_text
    assert '# all=("figures", "body_tables", "formula_images", "supplementary")' in (
        html_text
    )


@pytest.mark.parametrize(
    "extra_args, expected_flag",
    [
        (("--name", "arxiv"), "--name"),
        (("--doi", "10.48550/arxiv.2605.06663v1"), "--doi"),
        (("--source", "arxiv_html"), "--source"),
        (("--fulltext-client",), "--fulltext-client"),
        (("--html-capable=false",), "--html-capable"),
    ],
)
def test_from_manifest_rejects_mixed_legacy_flags(
    tmp_path: Path,
    extra_args: tuple[str, ...],
    expected_flag: str,
) -> None:
    def run_with(*extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--from-manifest",
                str(ARXIV_MANIFEST),
                "--output-dir",
                str(tmp_path),
                *extra_args,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    result = run_with(*extra_args)

    assert result.returncode != 0
    assert f"--from-manifest cannot be combined with {expected_flag}" in result.stderr


def test_invalid_manifest_outputs_json_stderr(tmp_path: Path) -> None:
    invalid_manifest = tmp_path / "invalid.yml"
    invalid_manifest.write_text("schema_version: 1\nname: invalid\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--from-manifest",
            str(invalid_manifest),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode != 0
    error = json.loads(result.stderr)
    assert error["status"] == "MANIFEST_SCHEMA_INVALID"
    assert error["reason"]


def test_from_manifest_does_not_modify_manifest_file(tmp_path: Path) -> None:
    before = ARXIV_MANIFEST.read_text(encoding="utf-8")

    _run_from_manifest(tmp_path)

    assert ARXIV_MANIFEST.read_text(encoding="utf-8") == before
