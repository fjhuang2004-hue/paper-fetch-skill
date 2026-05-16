"""Springer provider-owned HTML extraction and asset helpers."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Mapping

from ..common_patterns import (
    EXTENDED_DATA_LABEL,
    EXTENDED_DATA_TABLE_PREFIX_PATTERN,
    FIGURE_LABEL_CORE_PATTERN,
    LABEL_NUMBER_PATTERN,
    TABLE_LABEL_PREFIX_PATTERN,
)
from ..extraction.html import decode_html as _decode_html
from ..extraction.html.assets import (
    FULL_SIZE_IMAGE_ATTRS,
    FIGURE_KIND,
    PREVIEW_IMAGE_ATTRS,
    FigurePageFetcher,
    SUPPLEMENTARY_KIND,
    _soup_attr_url,
    download_assets,
    extract_figure_assets as extract_generic_figure_assets,
    extract_formula_assets as extract_generic_formula_assets,
    extract_supplementary_assets as extract_generic_supplementary_assets,
    split_body_and_supplementary_assets,
    looks_like_full_size_asset_url,
)
from ..extraction.html._metadata import (
    parse_html_metadata as parse_generic_html_metadata,
)
from ..extraction.html._runtime import (
    clean_markdown,
    prune_html_tree,
)
from ..extraction.html.figure_links import inject_inline_figure_links
from ..extraction.html.renderer import render_html_markdown
from ..extraction.html.language import (
    collect_html_abstract_blocks,
    html_node_language_hint,
)
from ..extraction.html.parsing import choose_parser
from ..extraction.html.semantics import (
    BACK_MATTER_HEADINGS,
    SUPPLEMENTARY_BACK_MATTER_HEADINGS,
    collect_html_section_hints,
    heading_category,
    normalize_section_title,
)
from ..extraction.html.ui_tokens import (
    SPRINGER_FULL_SIZE_IMAGE_LABEL,
    SPRINGER_NATURE_SOURCE_DATA_LABEL,
    SPRINGER_PREVIEW_PHRASE,
)
from ..metadata.types import MetadataMergeRule, merge_metadata_layers
from ..publisher_identity import normalize_doi
from ..utils import dedupe_authors, normalize_text
from ._html_asset_engine import (
    HtmlAssetExtractionPolicy,
    extract_scoped_assets_with_policy,
)
from ._html_authors import (
    AuthorExtractionPipeline,
    AuthorStep,
    GENERIC_AUTHOR_NOISE_TEXT,
    extract_jsonld_authors as extract_common_jsonld_authors,
    extract_meta_authors as extract_common_meta_authors,
    extract_selector_authors as extract_common_selector_authors,
    looks_like_collective_author_text,
    looks_like_author_name,
)
from ._html_references import extract_numbered_references_from_html
from .html_springer_nature import (
    clean_springer_nature_text_fragment,
    extract_springer_nature_markdown,
    is_springer_nature_url,
    is_nature_url,
    select_nature_abstract_section,
    select_springer_nature_article_root,
)
from ._html_section_markdown import (
    FIGURE_ACTION_TRAILING_LINK_PATTERN as SPRINGER_FIGURE_TRAILING_LINK_PATTERN,
    render_clean_text_from_html,
)

from bs4 import BeautifulSoup, Tag

SPRINGER_MEDIA_SIZE_SEGMENT_PATTERN = re.compile(r"^(?:lw|w|m|h)\d+(?:h\d+)?$")
SPRINGER_INLINE_FIGURE_SELECTORS = (".c-article-section__figure-item",)
SPRINGER_FIGURE_DESCRIPTION_SELECTORS = (".c-article-section__figure-description",)
# Springer figure headings are extracted from captions, alt text, and page URLs;
# keep the caption regex provider-scoped while deriving its core label syntax.
SPRINGER_FIGURE_LABEL_PATTERN = re.compile(
    rf"\b{FIGURE_LABEL_CORE_PATTERN}\b",
    flags=re.IGNORECASE,
)
SPRINGER_FIGURE_PAGE_NUMBER_PATTERN = re.compile(
    r"/figures/(\d+[A-Za-z]?)\b", flags=re.IGNORECASE
)
SPRINGER_INLINE_EQUATION_URL_PATTERN = re.compile(
    r"(?:ieq|math)[-_]?\d+", flags=re.IGNORECASE
)
SPRINGER_TABLE_LABEL_PATTERN = re.compile(
    rf"\b(?:{EXTENDED_DATA_TABLE_PREFIX_PATTERN}|{TABLE_LABEL_PREFIX_PATTERN})"
    rf"\s*\.?\s*(?P<number>{LABEL_NUMBER_PATTERN})\b",
    flags=re.IGNORECASE,
)
SPRINGER_TABLE_PAGE_NUMBER_PATTERN = re.compile(
    r"/tables/(\d+[A-Za-z]?)\b", flags=re.IGNORECASE
)
SPRINGER_TABLE_IMAGE_NUMBER_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:tab|table)[\s_.%-]*0*([a-z]?\d+[a-z]?)"
    r"(?:[^a-z0-9]|$)",
    flags=re.IGNORECASE,
)
SPRINGER_TABLE_IMAGE_EXTENSION_PATTERN = re.compile(
    r"\.(?:avif|gif|jpe?g|png|tiff?|webp)(?:[?#]|$)",
    flags=re.IGNORECASE,
)
SPRINGER_TABLE_IMAGE_HINT_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:tab|table)(?:[\s_.%-]*\d|[^a-z0-9])",
    flags=re.IGNORECASE,
)
SPRINGER_TABLE_IMAGE_ROOT_SELECTORS = (
    ".c-article-table-container",
    "[data-track-component='table']",
    "[data-component='article-container']",
    "[data-container-type='article']",
    ".container-type-article",
    "[role='main']",
    "main",
    "article",
    ".c-article-body",
    ".main-content",
)
# SITE_UI_COPY_REGRESSION_MARKER: site-owned Springer/Nature table-page chrome;
# rerun Springer table image fixture tests when these tokens change.
# STRUCTURAL_UI_COPY_HOOK: used only to constrain table-image discovery context.
SPRINGER_TABLE_IMAGE_CHROME_NODE_NAMES = frozenset({"header", "nav", "footer", "aside"})
SPRINGER_TABLE_IMAGE_CHROME_CONTEXT_TOKENS = (
    "account",
    "advert",
    "breadcrumb",
    "c-ad",
    "citation",
    "cookie",
    "footer",
    "gpt",
    "header",
    "identity",
    "journal-header",
    "login",
    "logo",
    "menu",
    "metrics",
    "newsletter",
    "recommend",
    "related",
    "search",
    "share",
    "social",
)
SPRINGER_TABLE_IMAGE_REJECT_URL_TOKENS = (
    "/favicons/",
    "/logos/",
    "/static/images/",
    "account",
    "advert",
    "crossmark",
    "favicon",
    "gpt-advert",
    "header-",
    "logo",
    "nature-cms/uploads/product",
    "newsletter",
    "orcid",
    "social",
    "verify.nature.com",
)
SPRINGER_SUPPLEMENTARY_HOST_TOKENS = (
    "static-content.springer.com/esm/",
    "/mediaobjects/",
)
SPRINGER_PREVIEW_SENTENCE_PATTERN = re.compile(
    rf"\b{re.escape(SPRINGER_PREVIEW_PHRASE)}\b[,.!;:]*",
    flags=re.IGNORECASE,
)
SPRINGER_PREVIEW_MARKDOWN_LINE_PATTERN = re.compile(
    rf"(?im)^[ \t>*-]*{re.escape(SPRINGER_PREVIEW_PHRASE)}[,.!;:]*\s*$\n?",
)
SPRINGER_AI_ALT_DISCLAIMER_ID_TOKEN = "ai-alt-disclaimer"
SPRINGER_ARTICLE_JSONLD_TYPES = frozenset(
    {
        "article",
        "newsarticle",
        "medicalscholarlyarticle",
        "scholarlyarticle",
        "webpage",
    }
)
SPRINGER_IGNORED_AUTHOR_TEXT = {
    *GENERIC_AUTHOR_NOISE_TEXT,
    "authors and affiliations",
    "view author information",
}
SPRINGER_NON_SUPPLEMENTARY_BACK_MATTER_HEADINGS = BACK_MATTER_HEADINGS - SUPPLEMENTARY_BACK_MATTER_HEADINGS
# BACK_MATTER_HEADINGS also includes references, acknowledgements, disclosures,
# and similar prose sections; those are not downloadable supplementary scopes.
SPRINGER_SUPPLEMENTARY_SECTION_TITLES = frozenset(
    (BACK_MATTER_HEADINGS | {EXTENDED_DATA_LABEL, f"{EXTENDED_DATA_LABEL} figures and tables"})
    - SPRINGER_NON_SUPPLEMENTARY_BACK_MATTER_HEADINGS
)
SPRINGER_EXTENDED_DATA_SECTION_TITLES = frozenset(
    {EXTENDED_DATA_LABEL, f"{EXTENDED_DATA_LABEL} figures and tables"}
)
SPRINGER_SOURCE_DATA_SECTION_TITLES = frozenset({SPRINGER_NATURE_SOURCE_DATA_LABEL})
SPRINGER_SOURCE_DATA_TITLE_PREFIX = SPRINGER_NATURE_SOURCE_DATA_LABEL
SPRINGER_PEER_REVIEW_TOKENS = (
    "peer review",
    "peer reviewer report",
    "peer reviewer reports",
    "transparent peer review",
)


def decode_html(body: bytes) -> str:
    return _decode_html(body)


def _looks_like_collective_author_text(text: str) -> bool:
    return looks_like_collective_author_text(text)


def _normalize_display_author_name(name: str) -> str:
    normalized = normalize_text(name)
    if not normalized or normalized.count(",") != 1:
        return normalized
    left, right = [part.strip() for part in normalized.split(",", 1)]
    if not left or not right:
        return normalized
    if not (looks_like_author_name(left) and looks_like_author_name(right)):
        return normalized
    if _looks_like_collective_author_text(left) or _looks_like_collective_author_text(
        right
    ):
        return normalized
    return normalize_text(f"{right} {left}")


def _normalize_display_authors(authors: list[str]) -> list[str]:
    return dedupe_authors(
        [
            _normalize_display_author_name(author)
            for author in authors
            if normalize_text(author)
        ]
    )


def normalize_display_authors(authors: list[str]) -> list[str]:
    return _normalize_display_authors(authors)


def _extract_meta_authors(html_text: str) -> list[str]:
    return _normalize_display_authors(
        extract_common_meta_authors(html_text, keys={"citation_author"})
    )


def _extract_jsonld_authors(html_text: str) -> list[str]:
    return _normalize_display_authors(
        extract_common_jsonld_authors(
            html_text,
            article_types=SPRINGER_ARTICLE_JSONLD_TYPES,
            author_paths=("mainEntity.author",),
        )
    )


def _node_author_text(node: Any) -> str:
    return (
        normalize_text(node.get_text(" ", strip=True))
        if isinstance(node, Tag)
        else ""
    )


def _extract_dom_authors(html_text: str) -> list[str]:
    return _normalize_display_authors(
        extract_common_selector_authors(
            html_text,
            selectors=(
                "[data-test='author-name']",
                ".c-article-author-list [itemprop='name']",
                ".c-article-author-list li",
                ".authors__name",
            ),
            ignored_text=SPRINGER_IGNORED_AUTHOR_TEXT,
            node_text=_node_author_text,
            reject_email=True,
            reject_affiliation_prefixes=("author information",),
        )
    )


_AUTHOR_PIPELINE = AuthorExtractionPipeline(
    AuthorStep("meta", _extract_meta_authors),
    AuthorStep("jsonld", _extract_jsonld_authors),
    AuthorStep("dom", _extract_dom_authors),
)
_SPRINGER_BASE_FIRST_SCALAR_KEYS = frozenset(
    {
        "title",
        "journal_title",
        "published",
        "landing_page_url",
        "doi",
        "article_type",
        "citation_fulltext_html_url",
        "citation_abstract_html_url",
    }
)
_SPRINGER_HTML_METADATA_MERGE_RULE = MetadataMergeRule(
    fill_empty=tuple(_SPRINGER_BASE_FIRST_SCALAR_KEYS),
    overwrite=(
        "abstract",
        "raw_meta",
        "lookup_title",
        "lookup_redirect_url",
        "identifier_value",
    ),
    concat_unique=("authors", "keywords"),
    take_first_non_empty=("references",),
)


def extract_authors(html_text: str) -> list[str]:
    return _AUTHOR_PIPELINE(html_text)


def parse_html_metadata(html_text: str, source_url: str):
    metadata = parse_generic_html_metadata(html_text, source_url)
    abstract = normalize_text(str(metadata.get("abstract") or ""))
    if abstract and is_springer_nature_url(source_url):
        metadata["abstract"] = clean_springer_nature_text_fragment(abstract)
    metadata["authors"] = _normalize_display_authors(
        [
            normalize_text(str(item))
            for item in (metadata.get("authors") or [])
            if normalize_text(str(item))
        ]
    )
    return metadata


def merge_html_metadata(base_metadata, html_metadata):
    base = dict(base_metadata or {})
    html_metadata = dict(html_metadata or {})
    merged = merge_metadata_layers(
        [base, html_metadata],
        rule=_SPRINGER_HTML_METADATA_MERGE_RULE,
    )
    for key in _SPRINGER_BASE_FIRST_SCALAR_KEYS:
        merged[key] = normalize_text(str(merged.get(key) or "")) or None
    merged["abstract"] = normalize_text(str(merged.get("abstract") or "")) or None
    merged["authors"] = dedupe_authors(
        [str(item) for item in (merged.get("authors") or [])]
    )
    merged["keywords"] = list(merged.get("keywords") or [])
    merged["license_urls"] = list(base.get("license_urls") or [])
    merged["fulltext_links"] = list(base.get("fulltext_links") or [])
    if "references" in base:
        merged["references"] = list(base.get("references") or [])
    else:
        merged.pop("references", None)
    merged["raw_meta"] = html_metadata.get("raw_meta", {})
    for key in ("lookup_title", "lookup_redirect_url", "identifier_value"):
        if html_metadata.get(key):
            merged[key] = html_metadata.get(key)
    if not merged.get("doi"):
        merged["doi"] = normalize_doi(str(html_metadata.get("doi") or ""))
    return merged


def _clean_springer_preview_fragment(text: str) -> str:
    cleaned = SPRINGER_PREVIEW_SENTENCE_PATTERN.sub(" ", text or "")
    return clean_springer_nature_text_fragment(cleaned)


def _clean_springer_asset_caption(text: str) -> str:
    cleaned = SPRINGER_FIGURE_TRAILING_LINK_PATTERN.sub("", normalize_text(text or ""))
    return normalize_text(cleaned)


def _clean_springer_preview_markdown(markdown_text: str) -> str:
    if not markdown_text:
        return ""
    cleaned = SPRINGER_PREVIEW_MARKDOWN_LINE_PATTERN.sub("", markdown_text)
    cleaned = SPRINGER_PREVIEW_SENTENCE_PATTERN.sub(" ", cleaned)
    return clean_markdown(cleaned)


def _clean_springer_abstract_sections(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cleaned_sections: list[dict[str, Any]] = []
    for section in sections:
        cleaned_section = dict(section)
        if cleaned_section.get("text") is not None:
            cleaned_section["text"] = _clean_springer_preview_fragment(
                str(cleaned_section.get("text") or "")
            )
        cleaned_sections.append(cleaned_section)
    return cleaned_sections


def _springer_node_context_text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    attrs = getattr(node, "attrs", None) or {}
    parts = [normalize_text(getattr(node, "name", "") or "")]
    for key in ("id", "data-test"):
        parts.append(normalize_text(str(attrs.get(key) or "")))
    class_values = attrs.get("class")
    if isinstance(class_values, (list, tuple, set)):
        parts.extend(normalize_text(str(item)) for item in class_values)
    else:
        parts.append(normalize_text(str(class_values or "")))
    return " ".join(part.lower() for part in parts if part)


def _strip_ai_alt_disclaimer_references(root: Any) -> None:
    if not isinstance(root, Tag):
        return
    for node in root.select(
        f"[aria-describedby*='{SPRINGER_AI_ALT_DISCLAIMER_ID_TOKEN}']"
    ):
        if not isinstance(node, Tag):
            continue
        described_by = normalize_text(str(node.get("aria-describedby") or ""))
        if not described_by:
            continue
        tokens = [
            token
            for token in described_by.split()
            if SPRINGER_AI_ALT_DISCLAIMER_ID_TOKEN not in normalize_text(token).lower()
        ]
        if tokens:
            node["aria-describedby"] = " ".join(tokens)
        elif node.has_attr("aria-describedby"):
            del node["aria-describedby"]


def _remove_springer_ai_alt_disclaimers(root: Any) -> None:
    if not isinstance(root, Tag):
        return
    removable_nodes: list[Tag] = []
    seen: set[int] = set()

    for node in root.select(f"[id*='{SPRINGER_AI_ALT_DISCLAIMER_ID_TOKEN}']"):
        if isinstance(node, Tag) and id(node) not in seen:
            removable_nodes.append(node)
            seen.add(id(node))

    for node in removable_nodes:
        node.decompose()

    _strip_ai_alt_disclaimer_references(root)


def _normalized_root_html(html_text: str) -> tuple[str, Any]:
    soup = BeautifulSoup(html_text, choose_parser())
    root = (
        select_springer_nature_article_root(soup)
        or soup.select_one("article")
        or soup.select_one("main")
    )
    if root is None:
        root = soup.body or soup
    candidate_soup = BeautifulSoup(str(root), choose_parser())
    active_root = candidate_soup.body or candidate_soup
    prune_html_tree(active_root)
    _remove_springer_ai_alt_disclaimers(active_root)
    return str(active_root), active_root


def extract_html_extraction_sidecars(
    html_text: str,
    source_url: str,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    cleaned_html, active_root = _normalized_root_html(html_text)
    if active_root is None:
        return {
            "cleaned_html": cleaned_html,
            "abstract_sections": [],
            "section_hints": [],
        }
    if is_nature_url(source_url):
        article_root = select_springer_nature_article_root(active_root) or active_root
        body_root = (
            article_root.select_one("div.c-article-body")
            if isinstance(article_root, Tag)
            else None
        )
        abstract_node = select_nature_abstract_section(body_root or article_root)
        if isinstance(abstract_node, Tag):
            content_root = (
                abstract_node.select_one("div.c-article-section__content")
                or abstract_node
            )
            abstract_text = render_clean_text_from_html(content_root)
            abstract_sections = (
                [
                    {
                        "heading": "Abstract",
                        "text": abstract_text,
                        "language": html_node_language_hint(
                            abstract_node, allow_soft_hints=True
                        ),
                        "kind": "abstract",
                        "order": 0,
                        "source_selector": "section",
                    }
                ]
                if abstract_text
                else []
            )
        else:
            abstract_sections = _clean_springer_abstract_sections(
                collect_html_abstract_blocks(active_root)
            )
    else:
        abstract_sections = _clean_springer_abstract_sections(
            collect_html_abstract_blocks(active_root)
        )
    return {
        "cleaned_html": cleaned_html,
        "abstract_sections": abstract_sections,
        "section_hints": collect_html_section_hints(
            active_root,
            title=title,
            language_hint_resolver=lambda node: html_node_language_hint(
                node, allow_soft_hints=True
            ),
        ),
    }


def extract_article_markdown(cleaned_html: str, source_url: str) -> str:
    def render_springer_html(html_text: str, active_source_url: str) -> str:
        return extract_springer_nature_markdown(html_text, active_source_url) or ""

    custom_markdown = render_html_markdown(
        cleaned_html,
        source_url,
        cleaned_html=True,
        renderer=render_springer_html,
    )
    markdown_text = custom_markdown or render_html_markdown(
        cleaned_html,
        source_url,
        cleaned_html=True,
    )
    return _inject_remote_figure_links(markdown_text, cleaned_html, source_url)


def _inject_remote_figure_links(markdown_text: str, cleaned_html: str, source_url: str) -> str:
    if not markdown_text:
        return markdown_text
    figure_assets = extract_figure_assets(cleaned_html, source_url)
    if not figure_assets:
        return markdown_text
    return inject_inline_figure_links(
        markdown_text,
        figure_assets=figure_assets,
        clean_markdown_fn=lambda value: clean_markdown(
            value,
            noise_profile="springer_nature",
        ),
    )


def extract_html_payload(
    html_text: str,
    source_url: str,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    extraction_sidecars = extract_html_extraction_sidecars(
        html_text, source_url, title=title
    )
    markdown_text = _clean_springer_preview_markdown(
        extract_article_markdown(extraction_sidecars["cleaned_html"], source_url)
    )
    extracted_authors = extract_authors(html_text)
    extracted_references = extract_numbered_references_from_html(html_text)
    return {
        "markdown_text": markdown_text,
        "abstract_sections": list(extraction_sidecars["abstract_sections"]),
        "section_hints": list(extraction_sidecars["section_hints"]),
        "cleaned_html": extraction_sidecars["cleaned_html"],
        "extracted_authors": extracted_authors,
        "references": extracted_references,
    }


def extract_asset_html_scopes(
    html_text: str,
    source_url: str,
    *,
    title: str | None = None,
) -> tuple[str, str]:
    cleaned_html, active_root = _normalized_root_html(html_text)
    if active_root is None:
        extraction_sidecars = extract_html_extraction_sidecars(
            html_text, source_url, title=title
        )
        cleaned_html = str(extraction_sidecars["cleaned_html"] or "")
        return cleaned_html, ""

    body_html, supplementary_html, _ = _extract_asset_html_scope_fragments(
        cleaned_html, active_root
    )
    return body_html, supplementary_html


def extract_source_data_html_scope(
    html_text: str,
    source_url: str,
    *,
    title: str | None = None,
) -> str:
    cleaned_html, active_root = _normalized_root_html(html_text)
    if active_root is None:
        extraction_sidecars = extract_html_extraction_sidecars(
            html_text, source_url, title=title
        )
        return str(extraction_sidecars["cleaned_html"] or "")

    _, _, source_data_html = _extract_asset_html_scope_fragments(
        cleaned_html, active_root
    )
    return source_data_html


def _springer_section_title_key(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    attrs = getattr(node, "attrs", None) or {}
    for key in ("data-title", "aria-label"):
        value = normalize_text(str(attrs.get(key) or ""))
        if value:
            return normalize_section_title(value)
    heading = node.find(re.compile(r"^h[1-6]$"))
    if isinstance(heading, Tag):
        return normalize_section_title(heading.get_text(" ", strip=True))
    return ""


def _springer_is_descendant_of(node: Any, ancestor: Any) -> bool:
    current = node.parent if isinstance(getattr(node, "parent", None), Tag) else None
    while isinstance(current, Tag):
        if current is ancestor:
            return True
        current = (
            current.parent
            if isinstance(getattr(current, "parent", None), Tag)
            else None
        )
    return False


def _springer_is_supplementary_like_section_title(title_key: str) -> bool:
    normalized_title = normalize_section_title(title_key)
    if not normalized_title:
        return False
    if normalized_title in SPRINGER_EXTENDED_DATA_SECTION_TITLES:
        return True
    return (
        normalized_title in SPRINGER_SUPPLEMENTARY_SECTION_TITLES
        and heading_category("h2", normalized_title) == "references_or_back_matter"
    )


def _springer_collect_asset_sections(article_root: Any) -> tuple[list[Any], list[Any]]:
    if not isinstance(article_root, Tag):
        return [], []
    supplementary_sections: list[Any] = []
    source_data_sections: list[Any] = []

    for node in article_root.find_all(["section", "div"]):
        if not isinstance(node, Tag):
            continue
        title_key = _springer_section_title_key(node)
        if not title_key:
            continue
        if any(
            _springer_is_descendant_of(node, existing)
            for existing in [*supplementary_sections, *source_data_sections]
        ):
            continue
        if normalize_section_title(title_key) in SPRINGER_SOURCE_DATA_SECTION_TITLES:
            source_data_sections.append(node)
            continue
        if _springer_is_supplementary_like_section_title(title_key):
            supplementary_sections.append(node)

    return supplementary_sections, source_data_sections


def _springer_merge_scope_fragments(nodes: list[Any]) -> str:
    fragments = [
        str(node)
        for node in nodes
        if isinstance(node, Tag) and normalize_text(str(node))
    ]
    return "\n".join(fragments)


def _extract_asset_html_scope_fragments(
    cleaned_html: str, active_root: Any
) -> tuple[str, str, str]:
    article_root = select_springer_nature_article_root(active_root) or active_root
    if isinstance(article_root, Tag):
        body_root = (
            article_root.select_one("div.c-article-body div.main-content")
            or article_root.select_one("div.c-article-body")
            or article_root.select_one("div.main-content")
            or article_root
        )
    else:
        body_root = active_root

    supplementary_sections, source_data_sections = _springer_collect_asset_sections(
        article_root if isinstance(article_root, Tag) else active_root
    )
    body_html = str(body_root) if isinstance(body_root, Tag) else cleaned_html
    supplementary_html = _springer_merge_scope_fragments(supplementary_sections)
    source_data_html = _springer_merge_scope_fragments(
        [*supplementary_sections, *source_data_sections]
    )
    return body_html, supplementary_html, source_data_html


def _springer_figure_caption(node: Any, soup: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    figcaption = node.find("figcaption")
    if isinstance(figcaption, Tag):
        caption = _clean_springer_asset_caption(figcaption.get_text(" ", strip=True))
        if caption:
            return caption
    image = node.find("img")
    if isinstance(image, Tag):
        described_by = normalize_text(str(image.get("aria-describedby") or ""))
        if described_by:
            described_node = soup.find(id=described_by)
            if isinstance(described_node, Tag):
                caption = _clean_springer_asset_caption(
                    described_node.get_text(" ", strip=True)
                )
                if caption:
                    return caption
    for context in (node, node.parent if isinstance(node.parent, Tag) else None):
        if not isinstance(context, Tag):
            continue
        for selector in SPRINGER_FIGURE_DESCRIPTION_SELECTORS:
            description = context.select_one(selector)
            if isinstance(description, Tag):
                caption = _clean_springer_asset_caption(
                    description.get_text(" ", strip=True)
                )
                if caption:
                    return caption
    return ""


def _springer_figure_page_url(node: Any, source_url: str) -> str:
    if not isinstance(node, Tag):
        return ""
    contexts = [node]
    if isinstance(node.parent, Tag):
        contexts.append(node.parent)
    for context in contexts:
        for anchor in context.find_all("a", href=True):
            href = normalize_text(str(anchor.get("href") or ""))
            if not href or href.startswith("#"):
                continue
            hint_blob = " ".join(
                [
                    normalize_text(anchor.get_text(" ", strip=True)).lower(),
                    normalize_text(str(anchor.get("aria-label") or "")).lower(),
                    normalize_text(str(anchor.get("title") or "")).lower(),
                ]
            )
            if SPRINGER_FULL_SIZE_IMAGE_LABEL in hint_blob or "/figures/" in href:
                return urllib.parse.urljoin(source_url, href)
    return ""


def _springer_figure_heading(
    figure_page_url: str,
    *,
    caption: str,
    alt_text: str,
) -> str:
    for candidate in (caption, alt_text):
        match = SPRINGER_FIGURE_LABEL_PATTERN.match(normalize_text(candidate))
        if match:
            return f"Figure {match.group(1)}"
    page_match = SPRINGER_FIGURE_PAGE_NUMBER_PATTERN.search(
        normalize_text(figure_page_url)
    )
    if page_match:
        return f"Figure {page_match.group(1)}"
    for candidate in (caption, alt_text):
        match = SPRINGER_FIGURE_LABEL_PATTERN.search(normalize_text(candidate))
        if match:
            return f"Figure {match.group(1)}"
    return caption[:80] or alt_text or "Figure"


def _springer_figure_asset_key(asset: Mapping[str, Any]) -> str:
    for field in ("figure_page_url", "full_size_url", "url", "preview_url"):
        candidate = normalize_text(str(asset.get(field) or ""))
        if candidate:
            return candidate
    return ""


def _springer_figure_asset_score(asset: Mapping[str, Any]) -> int:
    score = 0
    url_blob = " ".join(
        normalize_text(str(asset.get(field) or "")).lower()
        for field in ("full_size_url", "url", "preview_url")
    )
    if normalize_text(str(asset.get("figure_page_url") or "")):
        score += 100
    if "fig" in url_blob:
        score += 40
    if SPRINGER_INLINE_EQUATION_URL_PATTERN.search(url_blob):
        score -= 40
    if normalize_text(str(asset.get("full_size_url") or "")):
        score += 20
    if normalize_text(str(asset.get("preview_url") or "")):
        score += 10
    if normalize_text(str(asset.get("caption") or "")):
        score += 5
    return score


def promote_springer_media_url_to_full_size(url: str | None) -> str | None:
    candidate = normalize_text(url)
    if not candidate:
        return None
    parsed = urllib.parse.urlsplit(candidate)
    hostname = normalize_text(parsed.netloc).lower()
    if "media.springernature.com" not in hostname:
        return None
    path = parsed.path or ""
    if not path.startswith("/"):
        return None
    segments = path.lstrip("/").split("/", 1)
    if len(segments) < 2:
        return None
    size_segment, remainder = segments
    if size_segment == "full":
        return urllib.parse.urlunsplit(
            (
                parsed.scheme or "https",
                parsed.netloc,
                path,
                parsed.query,
                parsed.fragment,
            )
        )
    if not SPRINGER_MEDIA_SIZE_SEGMENT_PATTERN.match(size_segment):
        return None
    if "/springer-static/" not in f"/{remainder}":
        return None
    return urllib.parse.urlunsplit(
        (
            parsed.scheme or "https",
            parsed.netloc,
            f"/full/{remainder}",
            parsed.query,
            parsed.fragment,
        )
    )


def extract_full_size_figure_image_url(html_text: str, source_url: str) -> str | None:
    metadata = parse_html_metadata(html_text, source_url)
    raw_meta = metadata.get("raw_meta") if isinstance(metadata, Mapping) else {}
    if isinstance(raw_meta, Mapping):
        for key in ("twitter:image", "twitter:image:src", "og:image"):
            for value in raw_meta.get(key, []):
                candidate = urllib.parse.urljoin(
                    source_url, normalize_text(str(value or ""))
                )
                if candidate:
                    return candidate
    soup = BeautifulSoup(html_text, choose_parser())
    fallback_candidate = None
    promoted_candidate = None
    seen: set[str] = set()
    for tag in soup.find_all(["img", "source"]):
        candidate = _soup_attr_url(
            tag,
            *FULL_SIZE_IMAGE_ATTRS,
            "data-src",
            "src",
            "data-lazy-src",
            "srcset",
            "data-srcset",
        )
        if not candidate:
            continue
        absolute_candidate = urllib.parse.urljoin(source_url, candidate)
        if not absolute_candidate or absolute_candidate in seen:
            continue
        seen.add(absolute_candidate)
        if looks_like_full_size_asset_url(absolute_candidate.lower()):
            return absolute_candidate
        if promoted_candidate is None:
            promoted_candidate = promote_springer_media_url_to_full_size(
                absolute_candidate
            )
        if fallback_candidate is None:
            fallback_candidate = absolute_candidate
    return promoted_candidate or fallback_candidate


def _springer_table_number_from_label(label: str) -> str:
    match = SPRINGER_TABLE_LABEL_PATTERN.search(normalize_text(label).lower())
    return normalize_text(match.group("number")).lower() if match else ""


def _springer_table_number_from_url(table_url: str) -> str:
    match = SPRINGER_TABLE_PAGE_NUMBER_PATTERN.search(
        urllib.parse.urlparse(table_url).path
    )
    return normalize_text(match.group(1)).lower() if match else ""


def _springer_expected_table_number(label: str, table_url: str) -> str:
    return _springer_table_number_from_label(label) or _springer_table_number_from_url(
        table_url
    )


def _springer_table_image_url_blob(url: str) -> str:
    return urllib.parse.unquote(normalize_text(url)).lower()


def _springer_table_image_number_from_url(url: str) -> str:
    match = SPRINGER_TABLE_IMAGE_NUMBER_PATTERN.search(
        _springer_table_image_url_blob(url)
    )
    return normalize_text(match.group(1)).lower() if match else ""


def _springer_table_image_url_has_expected_number(url: str, table_number: str) -> bool:
    if not table_number:
        return False
    return _springer_table_image_number_from_url(url) == table_number.lower()


def _springer_table_image_url_has_table_semantics(url: str) -> bool:
    blob = _springer_table_image_url_blob(url)
    if SPRINGER_TABLE_IMAGE_HINT_PATTERN.search(blob):
        return True
    return (
        "/springer-static/esm/" in blob
        and "/mediaobjects/" in blob
        and "_tab" in blob
    )


def _springer_table_image_url_is_springer_esm_mediaobject(url: str) -> bool:
    blob = _springer_table_image_url_blob(url)
    return "/springer-static/esm/" in blob and "/mediaobjects/" in blob


def _springer_table_image_url_is_chrome(url: str) -> bool:
    blob = _springer_table_image_url_blob(url)
    if not blob or blob.startswith(("data:", "javascript:", "mailto:")):
        return True
    return any(token in blob for token in SPRINGER_TABLE_IMAGE_REJECT_URL_TOKENS)


def _springer_node_is_table_image_chrome(node: Any) -> bool:
    current = node
    while isinstance(current, Tag):
        name = normalize_text(getattr(current, "name", "")).lower()
        if name in SPRINGER_TABLE_IMAGE_CHROME_NODE_NAMES:
            return True
        context_text = _springer_node_context_text(current)
        if any(
            token in context_text
            for token in SPRINGER_TABLE_IMAGE_CHROME_CONTEXT_TOKENS
        ):
            return True
        current = (
            current.parent
            if isinstance(getattr(current, "parent", None), Tag)
            else None
        )
    return False


def _springer_node_is_table_content_context(node: Any) -> bool:
    current = node
    while isinstance(current, Tag):
        context_text = _springer_node_context_text(current)
        data_container_section = normalize_text(
            str(current.get("data-container-section") or "")
        ).lower()
        data_track_component = normalize_text(
            str(current.get("data-track-component") or "")
        ).lower()
        if (
            current.name == "table"
            or "c-article-table" in context_text
            or "table-container" in context_text
            or data_container_section == "table"
            or data_track_component == "table"
        ):
            return True
        current = (
            current.parent
            if isinstance(getattr(current, "parent", None), Tag)
            else None
        )
    return False


def _springer_table_image_roots(soup: BeautifulSoup) -> list[Tag]:
    roots: list[Tag] = []
    seen: set[int] = set()
    for selector in SPRINGER_TABLE_IMAGE_ROOT_SELECTORS:
        try:
            matches = soup.select(selector)
        except Exception:
            continue
        for match in matches:
            if isinstance(match, Tag) and id(match) not in seen:
                seen.add(id(match))
                roots.append(match)
    if roots:
        return roots
    body = soup.body
    return [body] if isinstance(body, Tag) else [soup]


def _springer_table_image_candidate_urls(
    root: Tag,
    source_url: str,
) -> list[tuple[str, Tag]]:
    candidates: list[tuple[str, Tag]] = []
    for tag in root.find_all(["img", "source", "a"]):
        if not isinstance(tag, Tag):
            continue
        if tag.name == "a":
            candidate = normalize_text(str(tag.get("href") or ""))
        else:
            candidate = _soup_attr_url(
                tag,
                *FULL_SIZE_IMAGE_ATTRS,
                "data-src",
                "src",
                "data-lazy-src",
                "srcset",
                "data-srcset",
            )
        if candidate:
            candidates.append((urllib.parse.urljoin(source_url, candidate), tag))
    return candidates


def _springer_table_meta_image_urls(
    soup: BeautifulSoup,
    source_url: str,
) -> list[str]:
    urls: list[str] = []
    for tag in soup.find_all("meta"):
        if not isinstance(tag, Tag):
            continue
        key = normalize_text(
            str(tag.get("property") or tag.get("name") or "")
        ).lower()
        if key not in {"og:image", "twitter:image", "twitter:image:src"}:
            continue
        candidate = normalize_text(str(tag.get("content") or ""))
        if candidate:
            urls.append(urllib.parse.urljoin(source_url, candidate))
    return urls


def _springer_table_image_candidate_score(
    url: str,
    *,
    node: Tag | None,
    table_number: str,
    from_meta: bool,
) -> int:
    if not SPRINGER_TABLE_IMAGE_EXTENSION_PATTERN.search(url):
        return -1
    if _springer_table_image_url_is_chrome(url):
        return -1
    if node is not None and _springer_node_is_table_image_chrome(node):
        return -1

    candidate_number = _springer_table_image_number_from_url(url)
    if candidate_number and table_number and candidate_number != table_number:
        return -1

    number_matches = _springer_table_image_url_has_expected_number(url, table_number)
    has_table_semantics = _springer_table_image_url_has_table_semantics(url)
    is_esm_mediaobject = _springer_table_image_url_is_springer_esm_mediaobject(url)
    is_table_context = node is not None and _springer_node_is_table_content_context(node)
    if not (
        number_matches
        or (is_esm_mediaobject and has_table_semantics)
        or (is_table_context and has_table_semantics)
    ):
        return -1
    if from_meta and not (number_matches or (is_esm_mediaobject and has_table_semantics)):
        return -1

    blob = _springer_table_image_url_blob(url)
    score = 0
    if number_matches:
        score += 120
    if has_table_semantics:
        score += 60
    if is_esm_mediaobject:
        score += 55
    if "media.springernature.com" in urllib.parse.urlparse(url).netloc.lower():
        score += 25
    if looks_like_full_size_asset_url(blob):
        score += 10
    if is_table_context:
        score += 20
    if node is not None and node.name == "img":
        score += 5
    if "as=webp" in blob or blob.endswith(".webp"):
        score -= 5
    return score


def extract_springer_table_image_url(
    html_text: str,
    source_url: str,
    *,
    label: str = "",
    table_url: str = "",
) -> str | None:
    """Return a trusted image fallback for a Springer/Nature table page."""
    soup = BeautifulSoup(html_text, choose_parser())
    table_number = _springer_expected_table_number(label, table_url or source_url)
    scored_candidates: list[tuple[int, int, str]] = []
    order = 0

    for meta_url in _springer_table_meta_image_urls(soup, source_url):
        score = _springer_table_image_candidate_score(
            meta_url,
            node=None,
            table_number=table_number,
            from_meta=True,
        )
        if score >= 0:
            scored_candidates.append((score, order, meta_url))
            order += 1

    seen_roots: set[int] = set()
    for root in _springer_table_image_roots(soup):
        if id(root) in seen_roots:
            continue
        seen_roots.add(id(root))
        for candidate_url, tag in _springer_table_image_candidate_urls(
            root,
            source_url,
        ):
            score = _springer_table_image_candidate_score(
                candidate_url,
                node=tag,
                table_number=table_number,
                from_meta=False,
            )
            if score >= 0:
                scored_candidates.append((score, order, candidate_url))
                order += 1

    if not scored_candidates:
        return None
    scored_candidates.sort(key=lambda item: (-item[0], item[1]))
    return scored_candidates[0][2]


def extract_formula_assets(html_text: str, source_url: str) -> list[dict[str, str]]:
    return extract_generic_formula_assets(
        html_text,
        source_url,
        noise_profile="springer_nature",
    )


def extract_figure_assets(html_text: str, source_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, choose_parser())
    candidates: list[Any] = []
    seen_nodes: set[int] = set()
    for node in soup.find_all("figure"):
        if id(node) not in seen_nodes:
            seen_nodes.add(id(node))
            candidates.append(node)
    for selector in SPRINGER_INLINE_FIGURE_SELECTORS:
        for node in soup.select(selector):
            if id(node) not in seen_nodes:
                seen_nodes.add(id(node))
                candidates.append(node)

    assets_by_key: dict[str, dict[str, str]] = {}
    fallback_assets: list[dict[str, str]] = []
    for node in candidates:
        if not isinstance(node, Tag):
            continue
        image = node.find("img")
        source = node.find("source")
        preview_url = _soup_attr_url(image, *PREVIEW_IMAGE_ATTRS) if image else ""
        if not preview_url:
            preview_url = (
                _soup_attr_url(source, "srcset", "data-srcset") if source else ""
            )
        full_size_url = _soup_attr_url(image, *FULL_SIZE_IMAGE_ATTRS) if image else ""
        if not full_size_url:
            full_size_url = (
                _soup_attr_url(source, *FULL_SIZE_IMAGE_ATTRS) if source else ""
            )
        absolute_preview = (
            urllib.parse.urljoin(source_url, preview_url) if preview_url else ""
        )
        absolute_full = (
            urllib.parse.urljoin(source_url, full_size_url) if full_size_url else ""
        )
        promoted_preview = promote_springer_media_url_to_full_size(absolute_preview)
        figure_page_url = _springer_figure_page_url(node, source_url)
        caption = _springer_figure_caption(node, soup)
        alt_text = (
            normalize_text(str(image.get("alt") or ""))
            if isinstance(image, Tag)
            else ""
        )
        heading = _springer_figure_heading(
            figure_page_url,
            caption=caption,
            alt_text=alt_text,
        )
        if (
            not absolute_preview
            and not absolute_full
            and not figure_page_url
            and not caption
        ):
            continue
        asset = {
            "kind": "figure",
            "heading": heading,
            "caption": caption or alt_text,
            "url": absolute_full or promoted_preview or absolute_preview,
            "section": "body",
        }
        if absolute_preview:
            asset["preview_url"] = absolute_preview
        if absolute_full:
            asset["full_size_url"] = absolute_full
        elif promoted_preview:
            asset["full_size_url"] = promoted_preview
        if figure_page_url:
            asset["figure_page_url"] = figure_page_url
        key = _springer_figure_asset_key(asset)
        if not key:
            fallback_assets.append(asset)
            continue
        existing = assets_by_key.get(key)
        if existing is None:
            assets_by_key[key] = asset
        else:
            if _springer_figure_asset_score(asset) > _springer_figure_asset_score(
                existing
            ):
                preserved_path = existing.get("path")
                existing.clear()
                existing.update(asset)
                if preserved_path and not existing.get("path"):
                    existing["path"] = preserved_path
            if len(normalize_text(asset.get("caption") or "")) > len(
                normalize_text(existing.get("caption") or "")
            ):
                existing["caption"] = asset["caption"]
            if len(normalize_text(asset.get("heading") or "")) > len(
                normalize_text(existing.get("heading") or "")
            ):
                existing["heading"] = asset["heading"]
            if asset.get("full_size_url") and not existing.get("full_size_url"):
                existing["full_size_url"] = asset["full_size_url"]
            if asset.get("preview_url") and not existing.get("preview_url"):
                existing["preview_url"] = asset["preview_url"]
    deduped_assets = list(assets_by_key.values()) + fallback_assets
    return deduped_assets or extract_generic_figure_assets(html_text, source_url)


def extract_supplementary_assets(
    html_text: str, source_url: str
) -> list[dict[str, str]]:
    assets: list[dict[str, str]] = []
    for asset in extract_generic_supplementary_assets(
        html_text, source_url, noise_profile="springer_nature"
    ):
        heading = normalize_text(str(asset.get("heading") or ""))
        if _springer_asset_is_source_data(heading) or _springer_asset_is_peer_review(
            heading
        ):
            continue
        assets.append(dict(asset))
    return _dedupe_springer_supplementary_assets(assets)


def _springer_asset_is_source_data(text: str) -> bool:
    normalized = normalize_section_title(text)
    return bool(normalized) and normalized.startswith(SPRINGER_SOURCE_DATA_TITLE_PREFIX)


def _springer_asset_is_peer_review(text: str) -> bool:
    normalized = normalize_section_title(text)
    return any(token in normalized for token in SPRINGER_PEER_REVIEW_TOKENS)


def _mark_source_data_assets(
    assets: list[dict[str, str]],
) -> list[dict[str, str]]:
    marked_assets: list[dict[str, str]] = []
    for asset in assets:
        heading = normalize_text(str(asset.get("heading") or ""))
        if _springer_asset_is_peer_review(heading):
            continue
        marked_asset = dict(asset)
        marked_asset["kind"] = "supplementary"
        marked_asset["section"] = "supplementary"
        marked_asset["asset_kind"] = "source_data"
        marked_assets.append(marked_asset)
    return marked_assets


def _anchor_text_candidates(anchor: Any) -> list[str]:
    if not isinstance(anchor, Tag):
        return []
    candidates = [
        normalize_text(anchor.get_text(" ", strip=True)),
        normalize_text(str(anchor.get("aria-label") or "")),
        normalize_text(str(anchor.get("title") or "")),
        normalize_text(str(anchor.get("data-track-label") or "")),
    ]
    return [candidate for candidate in candidates if candidate]


def _anchor_mentions_source_data(anchor: Any) -> bool:
    return any(
        _springer_asset_is_source_data(candidate)
        for candidate in _anchor_text_candidates(anchor)
    )


def _anchor_target_id(anchor: Any) -> str:
    if not isinstance(anchor, Tag):
        return ""
    href = normalize_text(str(anchor.get("href") or ""))
    if not href:
        return ""
    parsed = urllib.parse.urlparse(href)
    return normalize_text(urllib.parse.unquote(parsed.fragment or ""))


def extract_source_data_assets(html_text: str, source_url: str) -> list[dict[str, str]]:

    soup = BeautifulSoup(html_text, choose_parser())
    root = soup.body or soup
    supplementary_sections, source_data_sections = _springer_collect_asset_sections(
        root
    )
    assets: list[dict[str, str]] = []

    for section in source_data_sections:
        assets.extend(
            _mark_source_data_assets(
                extract_generic_supplementary_assets(
                    str(section), source_url, noise_profile="springer_nature"
                )
            )
        )

    for section in supplementary_sections:
        title_key = _springer_section_title_key(section)
        if (
            normalize_section_title(title_key)
            not in SPRINGER_EXTENDED_DATA_SECTION_TITLES
        ):
            continue
        for anchor in section.find_all("a", href=True):
            if not isinstance(anchor, Tag) or not _anchor_mentions_source_data(anchor):
                continue
            target_id = _anchor_target_id(anchor)
            if target_id:
                target = soup.find(id=target_id)
                if isinstance(target, Tag):
                    assets.extend(
                        _mark_source_data_assets(
                            extract_generic_supplementary_assets(
                                str(target),
                                source_url,
                                noise_profile="springer_nature",
                            )
                        )
                    )
                continue
            assets.extend(
                _mark_source_data_assets(
                    extract_generic_supplementary_assets(
                        str(anchor), source_url, noise_profile="springer_nature"
                    )
                )
            )

    return _dedupe_springer_supplementary_assets(assets)


def _springer_asset_identity(asset: Mapping[str, Any]) -> str:
    for field in (
        "figure_page_url",
        "full_size_url",
        "preview_url",
        "download_url",
        "url",
        "source_url",
    ):
        candidate = normalize_text(str(asset.get(field) or ""))
        if candidate:
            return candidate
    return normalize_text(str(asset.get("heading") or ""))


def _springer_asset_priority(asset: Mapping[str, Any]) -> int:
    if normalize_text(str(asset.get("asset_kind") or "")).lower() == "source_data":
        return 20
    if normalize_text(str(asset.get("kind") or "")).lower() == "supplementary":
        return 10
    return 0


def _dedupe_springer_supplementary_assets(
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    by_identity: dict[str, int] = {}

    for item in assets:
        asset = dict(item)
        identity = _springer_asset_identity(asset)
        if not identity:
            deduped.append(asset)
            continue
        existing_index = by_identity.get(identity)
        if existing_index is None:
            by_identity[identity] = len(deduped)
            deduped.append(asset)
            continue
        existing = deduped[existing_index]
        if _springer_asset_priority(asset) > _springer_asset_priority(existing):
            merged = dict(existing)
            merged.update(asset)
            deduped[existing_index] = merged
        else:
            for key, value in asset.items():
                if key not in existing or existing[key] in ("", None, [], {}):
                    existing[key] = value

    return deduped


def extract_html_assets(
    html_text: str,
    source_url: str,
    *,
    asset_profile,
) -> list[dict[str, str]]:
    body_html, supplementary_html = extract_asset_html_scopes(html_text, source_url)
    source_data_html = extract_source_data_html_scope(html_text, source_url)
    return extract_scoped_html_assets(
        body_html,
        source_url,
        asset_profile=asset_profile,
        supplementary_html_text=supplementary_html,
        source_data_html_text=source_data_html,
    )


def extract_scoped_html_assets(
    body_html_text: str,
    source_url: str,
    *,
    asset_profile,
    supplementary_html_text: str | None = None,
    source_data_html_text: str | None = None,
) -> list[dict[str, str]]:
    return extract_scoped_assets_with_policy(
        body_html_text,
        source_url,
        asset_profile=asset_profile,
        supplementary_html_text=supplementary_html_text,
        source_data_html_text=source_data_html_text,
        policy=HtmlAssetExtractionPolicy(
            figure_extractor=extract_figure_assets,
            formula_extractor=extract_formula_assets,
            supplementary_extractor=extract_supplementary_assets,
            source_data_extractor=extract_source_data_assets,
            finalizer=_dedupe_springer_supplementary_assets,
        ),
    )


def figure_download_candidates(
    transport,
    *,
    asset: Mapping[str, Any],
    user_agent: str,
    figure_page_fetcher: FigurePageFetcher | None = None,
) -> list[str]:
    direct_full_size_url = normalize_text(str(asset.get("full_size_url") or ""))
    primary_url = normalize_text(str(asset.get("url") or ""))
    preview_url = normalize_text(str(asset.get("preview_url") or "")) or primary_url
    candidates: list[str] = []
    if direct_full_size_url:
        candidates.append(direct_full_size_url)
    promoted_preview = promote_springer_media_url_to_full_size(primary_url)
    if promoted_preview:
        candidates.append(promoted_preview)
    if primary_url and looks_like_full_size_asset_url(primary_url):
        candidates.append(primary_url)

    figure_page_url = normalize_text(str(asset.get("figure_page_url") or ""))
    if figure_page_url:
        try:
            if figure_page_fetcher is not None:
                page_result = figure_page_fetcher(figure_page_url)
                if page_result is None:
                    raise ValueError("missing figure-page HTML")
                page_html, page_url = page_result
            else:
                response = transport.request(
                    "GET",
                    figure_page_url,
                    headers={
                        "User-Agent": user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                    },
                    timeout=20,
                    retry_on_rate_limit=True,
                    retry_on_transient=True,
                )
                page_html = decode_html(response["body"])
                page_url = str(response["url"] or figure_page_url)
            full_size_url = extract_full_size_figure_image_url(page_html, page_url)
            if full_size_url:
                candidates.append(full_size_url)
        except Exception:
            pass
    if preview_url:
        candidates.append(preview_url)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def download_assets_for_springer(
    transport,
    *,
    article_id: str,
    assets: list[dict[str, str]],
    output_dir,
    user_agent: str,
    asset_profile="all",
    figure_page_fetcher: FigurePageFetcher | None = None,
    browser_context_seed: Mapping[str, Any] | None = None,
    seed_urls: list[str] | None = None,
    asset_download_concurrency: int | None = None,
):
    body_assets, supplementary_assets = split_body_and_supplementary_assets(assets)
    body_result = download_assets(
        FIGURE_KIND,
        transport,
        article_id=article_id,
        assets=body_assets,
        output_dir=output_dir,
        user_agent=user_agent,
        asset_profile=asset_profile,
        figure_page_fetcher=figure_page_fetcher,
        browser_context_seed=browser_context_seed,
        seed_urls=seed_urls,
        candidate_builder=figure_download_candidates,
        asset_download_concurrency=asset_download_concurrency,
    )
    supplementary_result = download_assets(
        SUPPLEMENTARY_KIND,
        transport,
        article_id=article_id,
        assets=supplementary_assets,
        output_dir=output_dir,
        user_agent=user_agent,
        asset_profile=asset_profile,
        browser_context_seed=browser_context_seed,
        seed_urls=seed_urls,
        asset_download_concurrency=asset_download_concurrency,
    )
    return {
        "assets": [
            *list(body_result.get("assets") or []),
            *list(supplementary_result.get("assets") or []),
        ],
        "asset_failures": [
            *list(body_result.get("asset_failures") or []),
            *list(supplementary_result.get("asset_failures") or []),
        ],
    }
