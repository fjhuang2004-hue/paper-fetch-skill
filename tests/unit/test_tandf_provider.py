from __future__ import annotations

import re

from paper_fetch.provider_catalog import PROVIDER_CATALOG
from paper_fetch.providers._registry import provider_bundle
import paper_fetch.providers._tandf_html  # noqa: F401


def test_provider_bundle_round_trip() -> None:
    bundle = provider_bundle("tandf")
    assert bundle.catalog.name == "tandf"
    assert bundle.html_rules is not None
    assert bundle.html_rules.name == "tandf"


def test_provider_catalog_is_readable() -> None:
    assert PROVIDER_CATALOG["tandf"].name == "tandf"



def test_markdown_review_loop_contract_placeholder() -> None:
    assert False, (
        "Replace this scaffold placeholder with real fixture Markdown review "
        "assertions for every non-null manifest purpose, including positive "
        "Markdown assertions and negative site chrome assertions. "
        "First fixture slug: "
    )
