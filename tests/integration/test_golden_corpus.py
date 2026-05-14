from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os

import pytest

from tests.golden_corpus import (
    GoldenCorpusFixture,
    build_article_from_fixture,
    expected_summary_from_article,
    iter_golden_corpus_fixtures,
    iter_golden_corpus_representative_fixtures,
    lightweight_positive_summary_from_fixture,
)


FULL_GOLDEN_ENV = "PAPER_FETCH_RUN_FULL_GOLDEN"


@dataclass(frozen=True)
class ProviderGoldenContract:
    route_kind: str
    content_prefix: str
    source: str
    primary_marker: str


PROVIDER_GOLDEN_CONTRACTS = {
    "ams": ProviderGoldenContract(
        route_kind="html",
        content_prefix="text/html",
        source="ams_html",
        primary_marker="fulltext:ams_html_ok",
    ),
    "elsevier": ProviderGoldenContract(
        route_kind="official",
        content_prefix="text/xml",
        source="elsevier_xml",
        primary_marker="fulltext:elsevier_xml_ok",
    ),
    "springer": ProviderGoldenContract(
        route_kind="html",
        content_prefix="text/html",
        source="springer_html",
        primary_marker="fulltext:springer_html_ok",
    ),
    "science": ProviderGoldenContract(
        route_kind="html",
        content_prefix="text/html",
        source="science",
        primary_marker="fulltext:science_html_ok",
    ),
    "wiley": ProviderGoldenContract(
        route_kind="html",
        content_prefix="text/html",
        source="wiley_browser",
        primary_marker="fulltext:wiley_html_ok",
    ),
    "pnas": ProviderGoldenContract(
        route_kind="html",
        content_prefix="text/html",
        source="pnas",
        primary_marker="fulltext:pnas_html_ok",
    ),
    "ieee": ProviderGoldenContract(
        route_kind="html",
        content_prefix="text/html",
        source="ieee_html",
        primary_marker="fulltext:ieee_html_ok",
    ),
    "copernicus": ProviderGoldenContract(
        route_kind="xml",
        content_prefix="application/xml",
        source="copernicus_xml",
        primary_marker="fulltext:copernicus_xml_ok",
    ),
}


def _golden_contract_for_fixture(fixture: GoldenCorpusFixture) -> ProviderGoldenContract:
    if fixture.provider == "ams" and fixture.route_kind == "pdf_fallback":
        return ProviderGoldenContract(
            route_kind="pdf_fallback",
            content_prefix="application/pdf",
            source="ams_pdf",
            primary_marker="fulltext:ams_pdf_fallback_ok",
        )
    if fixture.provider == "copernicus" and fixture.route_kind == "pdf_fallback":
        return ProviderGoldenContract(
            route_kind="pdf_fallback",
            content_prefix="application/pdf",
            source="copernicus_pdf",
            primary_marker="fulltext:copernicus_pdf_fallback_ok",
        )
    return PROVIDER_GOLDEN_CONTRACTS[fixture.provider]


GOLDEN_CORPUS_FIXTURES = iter_golden_corpus_fixtures()
REPRESENTATIVE_GOLDEN_CORPUS_FIXTURES = iter_golden_corpus_representative_fixtures()


def _fixture_id(fixture: GoldenCorpusFixture) -> str:
    return f"{fixture.provider}:{fixture.doi}"


def test_golden_corpus_is_balanced_across_publishers() -> None:
    assert len(GOLDEN_CORPUS_FIXTURES) == 82
    assert Counter(fixture.provider for fixture in GOLDEN_CORPUS_FIXTURES) == Counter(
        {
            "ams": 11,
            "copernicus": 12,
            "elsevier": 10,
            "ieee": 7,
            "pnas": 10,
            "science": 11,
            "springer": 11,
            "wiley": 10,
        }
    )


@pytest.mark.parametrize("fixture", GOLDEN_CORPUS_FIXTURES, ids=_fixture_id)
def test_golden_corpus_lightweight_contracts_hold_across_full_corpus(fixture: GoldenCorpusFixture) -> None:
    expected = fixture.load_expected()
    actual = lightweight_positive_summary_from_fixture(fixture)
    contract = _golden_contract_for_fixture(fixture)

    assert fixture.route_kind == contract.route_kind
    assert fixture.content_type.startswith(contract.content_prefix)
    assert fixture.source_url
    assert actual["doi"] == fixture.doi

    for field_name in actual["validated_fields"]:
        if expected["has"][field_name]:
            assert actual["has"][field_name], f"Expected {field_name} for {fixture.doi}"

    if fixture.provider in {"ams", "science", "pnas", "wiley"} and fixture.route_kind == "html":
        assert list(actual["blocking_fallback_signals"]) == [], (
            f"Positive fixture leaked paywall signals for {fixture.doi}"
        )
        assert actual["source_candidate_hit"], (
            f"Expected generated HTML candidates to include source URL for {fixture.doi}"
        )


def test_golden_corpus_representative_fixtures_cover_primary_fulltext_paths_by_provider() -> None:
    assert len(REPRESENTATIVE_GOLDEN_CORPUS_FIXTURES) == 8
    assert Counter(fixture.provider for fixture in REPRESENTATIVE_GOLDEN_CORPUS_FIXTURES) == Counter(
        {
            "ams": 1,
            "copernicus": 1,
            "elsevier": 1,
            "ieee": 1,
            "pnas": 1,
            "science": 1,
            "springer": 1,
            "wiley": 1,
        }
    )


@pytest.mark.parametrize("fixture", REPRESENTATIVE_GOLDEN_CORPUS_FIXTURES, ids=_fixture_id)
def test_golden_corpus_representative_fixture_matches_primary_fulltext_path(fixture: GoldenCorpusFixture) -> None:
    article = build_article_from_fixture(fixture)
    actual = expected_summary_from_article(article)
    expected = fixture.load_expected()
    contract = _golden_contract_for_fixture(fixture)

    assert article.source == contract.source
    assert contract.primary_marker in article.quality.source_trail
    assert article.quality.content_kind == "fulltext"
    assert actual["expected_content_kind"] == "fulltext"
    assert expected["expected_content_kind"] == "fulltext"

    for field_name, expected_present in expected["has"].items():
        if expected_present:
            assert actual["has"][field_name], f"Expected {field_name} for {fixture.doi}"

    for count_name, expected_count in expected["counts"].items():
        if expected_count > 0:
            assert actual["counts"][count_name] > 0, f"Expected positive {count_name} count for {fixture.doi}"


@pytest.mark.skipif(
    os.environ.get(FULL_GOLDEN_ENV) != "1",
    reason=f"Set {FULL_GOLDEN_ENV}=1 to run full 82-fixture golden corpus regression.",
)
@pytest.mark.parametrize("fixture", GOLDEN_CORPUS_FIXTURES, ids=_fixture_id)
def test_golden_corpus_expected_summary_matches_current_extractor(fixture: GoldenCorpusFixture) -> None:
    article = build_article_from_fixture(fixture)
    actual = expected_summary_from_article(article)
    expected = fixture.load_expected()

    assert actual["expected_content_kind"] == "fulltext"
    assert expected["expected_content_kind"] == "fulltext"
    assert actual == expected
