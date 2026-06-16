"""Markdown extraction entrypoints for Atypon browser workflows."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from ...metadata.types import ProviderMetadata
from ...extraction.html.parsing import choose_parser
from ...extraction.html.renderer import clean_rendered_markdown
from ...extraction.html.semantics import collect_html_section_hints
from ...extraction.html.signals import HtmlExtractionFailure
from ...quality.html_availability import (
    HTML_CONTAINER_DROP_BROWSER_WORKFLOW,
    HtmlQualityAssessor,
    availability_failure_message,
    clean_container,
    select_best_container,
)
from ...publisher_identity import normalize_doi
from ...utils import normalize_text
from .._atypon_browser_workflow_profiles import publisher_profile as _publisher_profile
from .._html_section_markdown import render_container_markdown
from .normalization import (
    _drop_table_blocks,
    _normalize_abstract_blocks,
    _normalize_special_blocks,
)
from .postprocess import (
    _abstract_block_texts_from_payloads,
    _abstract_section_payloads,
    _ensure_body_markdown_heading,
    _inject_inline_table_blocks,
    _missing_abstract_markdown,
    _postprocess_browser_workflow_markdown,
)
from .profile import (
    extract_page_title,
    _container_selection_policy,
    _content_fragment_html,
    _node_language_hint,
    _noise_profile_for_publisher,
)

from bs4 import BeautifulSoup



def extract_browser_workflow_markdown(
    html_text: str,
    source_url: str,
    publisher: str,
    *,
    metadata: ProviderMetadata | Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    soup = BeautifulSoup(html_text, choose_parser())
    title = extract_page_title(soup)
    title = _preferred_title_from_metadata(title, metadata)
    container = select_best_container(
        soup, publisher, policy=_container_selection_policy(publisher)
    )
    if container is None:
        raise HtmlExtractionFailure(
            "article_container_not_found",
            "Could not identify the main article container in publisher HTML.",
        )

    clean_container(
        container, publisher, drop_profile=HTML_CONTAINER_DROP_BROWSER_WORKFLOW
    )
    from ...extraction.html.assets import extract_figure_assets

    profile = _publisher_profile(publisher)
    asset_container = copy.deepcopy(container)
    _normalize_abstract_blocks(asset_container)
    hook = profile.dom_hooks.asset_figure_extraction
    if hook is not None:
        hook(asset_container)
    _drop_table_blocks(asset_container)
    figure_assets = extract_figure_assets(
        _content_fragment_html(asset_container, publisher=publisher), source_url
    )

    table_entries = _normalize_special_blocks(container, publisher)
    abstract_sections = _abstract_section_payloads(container)
    abstract_block_texts = _abstract_block_texts_from_payloads(abstract_sections)
    body_container = copy.deepcopy(container)
    hook = profile.dom_hooks.body_container
    if hook is not None:
        hook(body_container)
    section_hints = collect_html_section_hints(
        body_container,
        title=title,
        language_hint_resolver=_node_language_hint,
    )
    noise_profile = _noise_profile_for_publisher(publisher)

    if publisher == "acs":
        from .._acs_html import _normalise_chem, extract_body_markdown as _acs_extract_body

        # Abstract — single reliable CSS selector on ACS pages.
        abstract_el = soup.select_one("p.articleBody_abstractText")
        abstract_text = normalize_text(abstract_el.get_text(" ", strip=True)) if abstract_el else ""
        abstract_text = _normalise_chem(abstract_text)

        # Body — ACS-specific DOM walker (div.NLM_p, NLM_sec, back-matter cut-off).
        body_md = _acs_extract_body(body_container)

        # Assemble complete markdown.
        parts: list[str] = []
        if title:
            clean_title = normalize_text(str(title).replace("Click to copy article link", "").replace("Article link copied!", ""))
            clean_title = _normalise_chem(clean_title)
            parts.append(f"# {clean_title}\n")
        if abstract_text:
            parts.append(f"## Abstract\n\n{abstract_text}\n")
        parts.append(body_md)
        markdown = "\n".join(parts)
        abstract_markdown = None  # already handled above
        # ACS extractor already produces clean structured output —
        # skip the generic postprocessor which can drop body sections.
        markdown = _inject_inline_table_blocks(
            markdown, table_entries=table_entries, publisher=publisher
        )
        # Inject figure images with captions.
        from ...extraction.html.figure_links import inject_inline_figure_links as _inj_figs
        markdown = _inj_figs(
            markdown,
            figure_assets=figure_assets,
            clean_markdown_fn=lambda v: clean_rendered_markdown(v, noise_profile=noise_profile),
        )
        quality_metadata = dict(metadata or {})
        if title and not quality_metadata.get("title"):
            quality_metadata["title"] = title
        diagnostics = HtmlQualityAssessor(publisher).assess(
            markdown, quality_metadata,
            html_text=html_text, title=title, final_url=source_url,
            container_tag=container.name,
            container_text_length=len(" ".join(container.stripped_strings)),
            section_hints=section_hints,
        )
        if not diagnostics.accepted:
            raise HtmlExtractionFailure(
                diagnostics.reason, availability_failure_message(diagnostics)
            )
        extraction_payload = {
            "title": title,
            "abstract_text": abstract_text,
            "abstract_sections": abstract_sections,
            "section_hints": section_hints,
            "container_tag": container.name,
            "container_text_length": len(" ".join(container.stripped_strings)),
            "availability_diagnostics": diagnostics.to_dict(),
        }
        profile = _publisher_profile(publisher)
        if profile.finalize_extraction is not None:
            markdown, extraction_payload = profile.finalize_extraction(
                html_text, source_url, markdown, extraction_payload, metadata=metadata,
            )
        markdown = _inject_front_matter(
            markdown, title=None, extraction_payload=extraction_payload, metadata=metadata,
        )
        return markdown, extraction_payload
    else:
        lines: list[str] = []
        render_container_markdown(body_container, lines, level=2)
        markdown = "\n".join(lines)
        if abstract_sections:
            markdown = _ensure_body_markdown_heading(markdown, title=title)
        abstract_markdown = _missing_abstract_markdown(
            container, markdown, publisher=publisher
        )
        if abstract_markdown:
            markdown = clean_rendered_markdown(
                f"{abstract_markdown}\n\n{markdown}", noise_profile=noise_profile
            )
        if title and f"# {title}" not in markdown:
            markdown = f"# {title}\n\n{markdown}".strip() + "\n"
    markdown = _inject_inline_table_blocks(
        markdown, table_entries=table_entries, publisher=publisher
    )
    markdown = _postprocess_browser_workflow_markdown(
        markdown,
        title=title,
        publisher=publisher,
        figure_assets=figure_assets,
        table_entries=table_entries,
        abstract_block_texts=abstract_block_texts,
    )

    quality_metadata = dict(metadata or {})
    if title and not quality_metadata.get("title"):
        quality_metadata["title"] = title
    diagnostics = HtmlQualityAssessor(publisher).assess(
        markdown,
        quality_metadata,
        html_text=html_text,
        title=title,
        final_url=source_url,
        container_tag=container.name,
        container_text_length=len(" ".join(container.stripped_strings)),
        section_hints=section_hints,
    )
    if not diagnostics.accepted:
        raise HtmlExtractionFailure(
            diagnostics.reason, availability_failure_message(diagnostics)
        )

    extraction_payload = {
        "title": title,
        "abstract_text": normalize_text(abstract_sections[0]["text"])
        if abstract_sections
        else ("\n\n".join(abstract_block_texts) if abstract_block_texts else None),
        "abstract_sections": abstract_sections,
        "section_hints": section_hints,
        "container_tag": container.name,
        "container_text_length": len(" ".join(container.stripped_strings)),
        "availability_diagnostics": diagnostics.to_dict(),
    }
    profile = _publisher_profile(publisher)
    if profile.finalize_extraction is not None:
        markdown, extraction_payload = profile.finalize_extraction(
            html_text,
            source_url,
            markdown,
            extraction_payload,
            metadata=metadata,
        )
    markdown = _inject_front_matter(markdown, title=title, extraction_payload=extraction_payload, metadata=metadata)
    return markdown, extraction_payload


def _inject_front_matter(
    markdown: str,
    *,
    title: str | None,
    extraction_payload: dict[str, Any],
    metadata: Mapping[str, Any] | None,
) -> str:
    """Prepend YAML front matter with article metadata."""
    front: list[str] = ["---"]

    if title:
        front.append(f"title: {json.dumps(title, ensure_ascii=False)}")

    authors = extraction_payload.get("extracted_authors")
    if authors:
        front.append("authors:")
        for author in authors:
            front.append(f"  - {json.dumps(author, ensure_ascii=False)}")

    meta = dict(metadata or {})
    doi = normalize_text(str(meta.get("doi") or ""))
    if doi:
        front.append(f"doi: {json.dumps(doi, ensure_ascii=False)}")

    journal = normalize_text(str(meta.get("journal") or ""))
    if journal:
        front.append(f"journal: {json.dumps(journal, ensure_ascii=False)}")

    front.append("---")
    front.append("")

    return "\n".join(front) + markdown


def _preferred_title_from_metadata(
    title: str | None,
    metadata: ProviderMetadata | Mapping[str, Any] | None,
) -> str | None:
    metadata_map = dict(metadata or {})
    metadata_title = normalize_text(str(metadata_map.get("title") or ""))
    if not metadata_title or _title_is_doi(metadata_title, metadata_map):
        return title
    normalized_title = normalize_text(title)
    if not normalized_title or _title_is_doi(normalized_title, metadata_map):
        return metadata_title
    lowered_title = normalized_title.casefold()
    lowered_metadata_title = metadata_title.casefold()
    if lowered_title != lowered_metadata_title and (
        lowered_title.startswith(lowered_metadata_title)
        or lowered_title.endswith(lowered_metadata_title)
    ):
        return metadata_title
    return title


def _title_is_doi(title: str | None, metadata: Mapping[str, Any]) -> bool:
    normalized_title = normalize_text(title)
    if not normalized_title:
        return False
    doi = normalize_doi(str(metadata.get("doi") or ""))
    return bool(doi and normalize_doi(normalized_title) == doi)



def extract_atypon_browser_workflow_markdown(
    html_text: str,
    source_url: str,
    publisher: str,
    *,
    metadata: ProviderMetadata | Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    return extract_browser_workflow_markdown(
        html_text,
        source_url,
        publisher,
        metadata=metadata,
    )


__all__ = [
    "extract_browser_workflow_markdown",
    "extract_atypon_browser_workflow_markdown",
]
