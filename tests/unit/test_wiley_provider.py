from __future__ import annotations

from functools import lru_cache
from typing import Any

from paper_fetch.providers import wiley as wiley_provider
from tests.golden_criteria import golden_criteria_asset


MARKDOWN_REVIEWED_FIXTURES = {
    "structure": "10.1111_gcb.16414",
    "table": "10.1111_cas.16395",
    "formula": "10.1111_gcb.15322",
    "figure": "10.1111_gcb.16414",
    "supplementary": "10.1111_gcb.16414",
    "references": "10.1111_gcb.16998",
    "pdf_fallback": "10.1111_cas.16395",
    "abstract_only": "10.1111_gcb.16998",
}


@lru_cache(maxsize=None)
def _extract_fixture_markdown(doi: str) -> tuple[str, dict[str, Any]]:
    client = wiley_provider.WileyClient(transport=None, env={})
    html = golden_criteria_asset(doi, "original.html").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    return client.extract_markdown(
        html,
        f"https://onlinelibrary.wiley.com/doi/full/{doi}",
        metadata={"doi": doi, "title": ""},
    )


def test_markdown_review_loop_structure_figure_and_supplementary_fixture() -> None:
    markdown, extraction = _extract_fixture_markdown("10.1111/gcb.16414")

    assert "## Abstract" in markdown
    assert "## 1 INTRODUCTION" in markdown
    assert "**Figure 1.** Conceptual diagram of velocity" in markdown
    assert "DATA AVAILABILITY STATEMENT" in markdown
    assert len(extraction["references"]) >= 50
    assert "Open in figure viewer" not in markdown
    assert "PowerPoint" not in markdown


def test_markdown_review_loop_table_and_pdf_fallback_fixture() -> None:
    markdown, extraction = _extract_fixture_markdown("10.1111/cas.16395")

    assert "## 1 INTRODUCTION" in markdown
    assert "**Table 1.** AI-SaMD approved as a medical device" in markdown
    assert "| Research area" in markdown
    assert len(extraction["references"]) >= 80
    assert "Open in figure viewer" not in markdown
    assert "PowerPoint" not in markdown


def test_markdown_review_loop_formula_references_and_abstract_only_fixture() -> None:
    formula_markdown, _ = _extract_fixture_markdown("10.1111/gcb.15322")
    references_markdown, references_extraction = _extract_fixture_markdown(
        "10.1111/gcb.16998"
    )

    assert "![Formula]" in formula_markdown
    assert "## Abstract" in references_markdown
    assert len(references_extraction["references"]) >= 70
    assert "Drought thresholds" in references_markdown
    assert "Open in figure viewer" not in references_markdown
    assert "PowerPoint" not in references_markdown
