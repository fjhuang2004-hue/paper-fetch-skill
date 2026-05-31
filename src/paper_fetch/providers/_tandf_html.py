"""Taylor & Francis provider-owned browser-workflow HTML rules."""

from __future__ import annotations

from functools import partial
from typing import Any

from bs4 import Tag

from ..utils import normalize_text
from ._html_authors import (
    ATYPON_AUTHOR_NOISE_TEXT,
    AuthorExtractionPipeline,
    AuthorStep,
    extract_meta_authors,
    extract_jsonld_authors,
)


def tandf_body_container(container: Any) -> None:
    """Locate hlFld-Fulltext and promote it as the sole body content."""
    if not isinstance(container, Tag):
        return

    fulltext = container.select_one(".hlFld-Fulltext")
    if fulltext is None or not isinstance(fulltext, Tag):
        return

    text_len = len(fulltext.get_text(" ", strip=True))
    if text_len < 500:
        return

    # Replace container content with fulltext
    fulltext.extract()
    container.clear()
    container.append(fulltext)


def tandf_asset_body_container(container: Any) -> None:
    tandf_body_container(container)


_TANDF_AUTHOR_PIPELINE = AuthorExtractionPipeline(
    AuthorStep("meta", partial(extract_meta_authors, keys={"citation_author"})),
    AuthorStep("jsonld", partial(extract_jsonld_authors, article_types={"Article"})),
    AuthorStep("dom", lambda _html: []),
)


def extract_authors(html_text: str) -> list[str]:
    return _TANDF_AUTHOR_PIPELINE(html_text)
