from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = REPO_ROOT / "docs" / "ai-onboarding" / "manifests"

PLACEHOLDER_PATTERNS = (
    "test_provider_golden_replay_placeholder",
    "test_markdown_review_loop_contract_placeholder",
    "TODO: add recorded golden fixture assets before enabling replay",
)
MARKDOWN_TARGET_PATTERN = re.compile(
    r"\b(markdown|rendered|to_ai_markdown|markdown_text)\b",
    re.IGNORECASE,
)
POSITIVE_ASSERTION_PATTERN = re.compile(
    r"\b(?:self\.)?assert(?:In|Regex|True)\s*\(|\bassert\s+.+\s+in\s+",
)
NEGATIVE_ASSERTION_PATTERN = re.compile(
    r"\b(?:self\.)?assertNot(?:In|Regex)\s*\(|\bassert\s+.+\s+not\s+in\s+",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must load as a mapping"
    return data


def _manifest_paths() -> tuple[Path, ...]:
    paths = tuple(sorted(MANIFESTS_DIR.glob("*.yml")))
    assert paths, "provider Markdown review contract needs manifest fixtures"
    return paths


def _doi_slug(doi: str) -> str:
    return doi.replace("/", "_")


def _assertion_mentions_markdown(text: str, pattern: re.Pattern[str]) -> bool:
    return any(
        pattern.search(line) and MARKDOWN_TARGET_PATTERN.search(line)
        for line in text.splitlines()
    )


def test_manifest_provider_tests_enforce_markdown_review_loop_contract() -> None:
    for manifest_path in _manifest_paths():
        manifest = _load_yaml(manifest_path)
        provider = str(manifest["name"])
        test_path = REPO_ROOT / "tests" / "unit" / f"test_{provider}_provider.py"
        assert test_path.is_file(), (
            f"{manifest_path.relative_to(REPO_ROOT)}: expected provider-local test "
            f"{test_path.relative_to(REPO_ROOT)}"
        )

        test_text = test_path.read_text(encoding="utf-8")
        for placeholder in PLACEHOLDER_PATTERNS:
            assert placeholder not in test_text, (
                f"{test_path.relative_to(REPO_ROOT)} still contains scaffold "
                f"placeholder {placeholder!r}"
            )

        doi_samples = manifest["fixtures"]["doi_samples"]
        assert isinstance(doi_samples, dict)
        for purpose, sample in doi_samples.items():
            if not isinstance(sample, dict) or not sample.get("doi"):
                continue
            doi = str(sample["doi"])
            accepted_markers = (str(purpose), _doi_slug(doi))
            assert any(marker in test_text for marker in accepted_markers), (
                f"{test_path.relative_to(REPO_ROOT)} must name non-null fixture "
                f"purpose {purpose!r} or DOI slug {_doi_slug(doi)!r}"
            )

        assert _assertion_mentions_markdown(test_text, POSITIVE_ASSERTION_PATTERN), (
            f"{test_path.relative_to(REPO_ROOT)} must include a positive Markdown "
            "assertion such as assertIn(..., markdown)"
        )
        assert _assertion_mentions_markdown(test_text, NEGATIVE_ASSERTION_PATTERN), (
            f"{test_path.relative_to(REPO_ROOT)} must include a negative Markdown "
            "assertion such as assertNotIn(..., markdown)"
        )
