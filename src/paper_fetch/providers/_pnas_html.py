"""PNAS provider-owned browser-workflow rules."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable, Mapping

from ..provider_catalog import (
    provider_base_domains,
    provider_crossref_pdf_position,
    provider_domains,
    provider_html_path_templates,
    provider_pdf_path_templates,
)
from ..extraction.html.provider_rules import (
    PNAS_SITE_RULE_OVERRIDES,
    provider_html_rules,
)
from ..quality.html_profiles import pnas_blocking_fallback_signals
from ..utils import normalize_text
from ._html_authors import (
    ATYPON_AUTHOR_COUNT_PATTERN,
    ATYPON_AUTHOR_COLLAPSE_UI_TEXT,
    ATYPON_AUTHOR_NOISE_TEXT,
    AuthorExtractionPipeline,
    extract_meta_authors,
    extract_property_authors,
)
from ._html_references import extract_numbered_references_from_html

HOSTS: tuple[str, ...] = provider_domains("pnas")
BASE_HOSTS: tuple[str, ...] = provider_base_domains("pnas")
HTML_PATH_TEMPLATES: tuple[str, ...] = provider_html_path_templates("pnas")
PDF_PATH_TEMPLATES: tuple[str, ...] = provider_pdf_path_templates("pnas")
CROSSREF_PDF_POSITION = provider_crossref_pdf_position("pnas")
NOISE_PROFILE = provider_html_rules("pnas").noise_profile
SITE_RULE_OVERRIDES: dict[str, Any] = PNAS_SITE_RULE_OVERRIDES
PNAS_AUTHOR_COUNT_PATTERN = ATYPON_AUTHOR_COUNT_PATTERN
PNAS_IGNORED_AUTHOR_TEXT = {
    *ATYPON_AUTHOR_NOISE_TEXT,
    *ATYPON_AUTHOR_COLLAPSE_UI_TEXT,
}


blocking_fallback_signals = pnas_blocking_fallback_signals


def _extract_dom_authors(html_text: str) -> list[str]:
    return extract_property_authors(
        html_text,
        selectors=".contributors [property='author'], #tab-contributors [property='author']",
        ignored_text=PNAS_IGNORED_AUTHOR_TEXT,
        count_pattern=PNAS_AUTHOR_COUNT_PATTERN,
        reject_email=True,
    )


_AUTHOR_EXTRACTION_PIPELINE = AuthorExtractionPipeline(
    _extract_dom_authors,
    partial(extract_meta_authors, keys={"citation_author", "dc.creator"}),
)


def extract_authors(html_text: str) -> list[str]:
    return _AUTHOR_EXTRACTION_PIPELINE(html_text)


def dom_postprocess(container: Any, *, stage: str | None = None) -> None:
    if normalize_text(stage).lower() != "before_block_normalization":
        return

    from .atypon_browser_workflow.profile import _drop_promotional_blocks, _promo_block_tokens

    _drop_promotional_blocks(container, promo_block_tokens=_promo_block_tokens("pnas"))


def markdown_postprocess(
    markdown_text: str,
    *,
    stage: str | None = None,
    original_markdown: str | None = None,
    has_heading: Callable[[str, str], bool] | None = None,
    **context: Any,
) -> str:
    del context
    if stage == "missing_abstract" and has_heading is not None:
        source_markdown = original_markdown or ""
        if has_heading(source_markdown, "significance") and has_heading(
            source_markdown, "abstract"
        ):
            return ""
    return markdown_text


def extract_asset_html_scopes(
    body_container: Any,
    supplementary_container: Any,
    *,
    publisher: str,
    content_fragment_html,
    atypon_browser_workflow_supplementary_sections,
) -> tuple[str, str]:
    for node in list(atypon_browser_workflow_supplementary_sections(body_container)):
        node.decompose()

    supplementary_html = "\n".join(
        str(node)
        for node in atypon_browser_workflow_supplementary_sections(supplementary_container)
        if normalize_text(node.get_text(" ", strip=True))
    )
    return content_fragment_html(
        body_container, publisher=publisher
    ), supplementary_html


def select_content_nodes(
    container: Any,
    *,
    structural_abstract_nodes,
    nodes_from_selectors,
    content_abstract_selectors,
    content_body_selectors,
    select_availability_nodes,
    dedupe_top_level_nodes,
    is_tag,
) -> list[Any]:
    del content_body_selectors

    body_nodes: list[Any] = []
    for selector in (
        "#bodymatter [data-extent='bodymatter'][property='articleBody']",
        "#bodymatter [property='articleBody']",
        "#bodymatter [data-extent='bodymatter']",
        "#bodymatter",
    ):
        try:
            body_nodes = [node for node in container.select(selector) if is_tag(node)]
        except Exception:
            body_nodes = []
        if body_nodes:
            break
    if not body_nodes:
        return []

    selected: list[Any] = []
    abstract_nodes = structural_abstract_nodes(container) or nodes_from_selectors(
        container, content_abstract_selectors
    )
    availability_nodes = select_availability_nodes(container, body_nodes)
    selected.extend(abstract_nodes)
    selected.extend(body_nodes)
    selected.extend(availability_nodes)
    return dedupe_top_level_nodes(selected)


def finalize_extraction(
    html_text: str,
    source_url: str,
    markdown_text: str,
    extraction: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    del source_url, metadata
    finalized = dict(extraction)
    extracted_authors = extract_authors(html_text)
    if extracted_authors:
        finalized["extracted_authors"] = extracted_authors
    extracted_references = extract_numbered_references_from_html(html_text)
    if extracted_references:
        finalized["references"] = extracted_references
    return markdown_text, finalized


def scoped_asset_extractor(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
    from .atypon_browser_workflow.asset_scopes import extract_scoped_html_assets

    return extract_scoped_html_assets(*args, **kwargs)
