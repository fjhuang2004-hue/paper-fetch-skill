"""IEEE Xplore direct HTML provider client."""

from __future__ import annotations

from dataclasses import dataclass
import html as html_lib
import json
from pathlib import Path
import re
import tempfile
import urllib.parse
from typing import Any, Mapping

from ..config import build_user_agent, resolve_asset_download_concurrency
from ..extraction.html import decode_html
from ..extraction.html.provider_rules import (
    IEEE_ACCESS_BLOCK_TEXT_TOKENS,
    IEEE_EXTRACTION_CLEANUP_SELECTORS,
)
from ..extraction.html.assets import (
    download_figure_assets,
    download_supplementary_assets,
    extract_scoped_html_assets,
    split_body_and_supplementary_assets,
)
from ..extraction.html.assets.supplementary import (
    GENERIC_SUPPLEMENTARY_TEXT_TOKENS,
    has_supplementary_file_suffix,
)
from ..extraction.html.asset_fields import DEFAULT_ASSET_URL_FIELDS
from ..extraction.html.landing import LandingRedirectLimitExceeded, fetch_landing_html
from ..extraction.html.parsing import choose_parser
from ..extraction.html.semantics import collect_html_section_hints
from ..extraction.html.renderer import clean_rendered_markdown
from ..http import DEFAULT_FULLTEXT_TIMEOUT_SECONDS, HttpTransport, PDF_MIME_TYPE, RequestFailure
from ..metadata.types import ProviderMetadata
from ..models import AssetProfile, article_from_markdown, metadata_only_article
from ..publisher_identity import DOI_PATTERN, normalize_doi
from ..quality.html_availability import HtmlQualityAssessor, availability_failure_message
from ..runtime import RuntimeContext
from ..tracing import download_marker, fulltext_marker, trace_from_markers
from ..utils import (
    choose_public_landing_page_url,
    dedupe_authors,
    empty_asset_results,
    normalize_text,
    strip_html_tags,
)
from ._html_section_markdown import render_container_markdown
from .browser_workflow.shared import BROWSER_HTML_BLOCKED_RESOURCE_TYPES
from ._pdf_fallback import PdfFallbackStrategy, PdfFetchFailure, fetch_pdf_over_http, fetch_pdf_with_playwright
from ._payloads import build_provider_payload
from ._waterfall import (
    DEFAULT_WATERFALL_CONTINUE_CODES,
    ProviderWaterfallStep,
    ProviderWaterfallState,
    run_provider_waterfall,
)
from ._script_json import extract_assignment_json
from ..reason_codes import ABSTRACT_ONLY, ERROR, NO_RESULT, NOT_SUPPORTED, OK, PDF_FALLBACK
from .base import (
    ProviderArtifacts,
    ProviderClient,
    ProviderContent,
    ProviderFailure,
    ProviderStatusResult,
    RawFulltextPayload,
    build_provider_status_check,
    combine_provider_failures,
    map_request_failure,
    summarize_capability_status,
)

__all__ = ["IeeeClient", "ProviderContent", "RawFulltextPayload"]

try:
    from bs4 import BeautifulSoup, Tag
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    BeautifulSoup = None
    Tag = None

IEEE_BASE_URL = "https://ieeexplore.ieee.org"
IEEE_DOCUMENT_URL_TEMPLATE = IEEE_BASE_URL + "/document/{article_number}/"
IEEE_REST_URL_TEMPLATE = IEEE_BASE_URL + "/rest/document/{article_number}/?logAccess=true"
IEEE_REFERENCES_URL_TEMPLATE = IEEE_BASE_URL + "/rest/document/{article_number}/references"
IEEE_MULTIMEDIA_URL_TEMPLATE = IEEE_BASE_URL + "/rest/document/{article_number}/multimedia"
IEEE_STAMP_URL_TEMPLATE = IEEE_BASE_URL + "/stamp/stamp.jsp?arnumber={article_number}"
IEEE_PDF_FALLBACK_ARTIFACT_DIR_NAME = "ieee_pdf_fallback"
IEEE_BROWSER_HTML_NAVIGATION_TIMEOUT_MS = 60000
IEEE_BROWSER_HTML_REST_WAIT_TIMEOUT_MS = 15000
IEEE_BROWSER_HTML_DOM_WAIT_TIMEOUT_MS = 5000
MAX_IEEE_LANDING_REDIRECTS = 8
IEEE_METADATA_ASSIGNMENT = "xplGlobal.document.metadata"
# IEEE Xplore article numbers are parsed only from the provider-owned
# `/document/{article_number}/` URL contract. Other IEEE URLs expose the same
# number in query params or REST paths, but those are handled by metadata fields
# or explicit route builders instead of this landing URL parser.
IEEE_ARTICLE_NUMBER_PATH_PATTERN = re.compile(r"^/document/(?P<article_number>\d+)(?:/|$)")
IEEE_SCRIPT_VALUE_PATTERN_TEMPLATE = r"""["']?{key}["']?\s*:\s*(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|true|false|null|\d+)"""
IEEE_SUPPORT_ICON_PATH = "/assets/img/icon.support.gif"
IEEE_MEDIASTORE_PATH_PREFIX = "/mediastore/ieee/content/media/"
IEEE_SUPPLEMENTARY_SEMANTIC_TOKENS = (
    *GENERIC_SUPPLEMENTARY_TEXT_TOKENS,
    "supporting-information",
    "supporting-material",
    "multimedia",
    "supplement file",
    "supplemental item",
)
IEEE_SUPPLEMENTARY_EXTRA_FILE_SUFFIXES = (
    ".doc",
    ".docx",
    ".ps",
    ".eps",
    ".bmp",
    ".mp4",
    ".mov",
    ".wmv",
    ".avi",
    ".mp3",
    ".aiff",
    ".ra",
    ".wav",
    ".tar.gz",
)
IEEE_ASSET_KIND_PRIORITY = {
    "formula": 10,
    "figure": 20,
    "table": 30,
}
IEEE_ASSET_URL_FIELDS = (
    *DEFAULT_ASSET_URL_FIELDS,
    "download_url",
    "figure_page_url",
)
_IEEE_STRONG_ASSET_IDENTITY_FIELD_NAMES = frozenset(
    {"download_url", "source_url", "full_size_url", "url"}
)
IEEE_STRONG_ASSET_IDENTITY_FIELDS = tuple(
    field for field in IEEE_ASSET_URL_FIELDS if field in _IEEE_STRONG_ASSET_IDENTITY_FIELD_NAMES
)
IEEE_WEAK_ASSET_IDENTITY_FIELDS = tuple(
    field for field in IEEE_ASSET_URL_FIELDS if field not in _IEEE_STRONG_ASSET_IDENTITY_FIELD_NAMES
)
IEEE_DOWNLOAD_MERGE_FIELDS = (
    "path",
    "download_url",
    "source_url",
    "original_url",
    "figure_page_url",
    "content_type",
    "width",
    "height",
    "download_tier",
    "downloaded_bytes",
    "preview_accepted",
)
IEEE_ASSET_URL_ATTRS = (
    "href",
    "src",
    "data-src",
    "data-original",
    "data-full-src",
    "data-url",
)
IEEE_REFERENCE_PAGE_SIZE = 30
IEEE_MAX_REFERENCE_PAGES = 20
IEEE_SECTION_MARKER_PATTERN = re.compile(r"^SECTION\s+(?:[IVXLCDM]+|\d+)\s*[.:]?$", flags=re.IGNORECASE)


@dataclass(frozen=True)
class IeeeLandingAttempt:
    normalized_doi: str
    landing_url: str
    response_url: str
    html_text: str
    merged_metadata: dict[str, Any]
    article_number: str
    landing_metadata: dict[str, Any]


@dataclass(frozen=True)
class IeeeHtmlExtraction:
    html_text: str
    markdown_text: str
    section_hints: list[dict[str, Any]]
    abstract_sections: list[dict[str, Any]]
    extracted_assets: list[dict[str, Any]]
    marker_counts: dict[str, int]


def _header_value(headers: Mapping[str, Any] | None, key: str, default: str = "") -> str:
    lowered_key = key.lower()
    for raw_key, value in (headers or {}).items():
        if str(raw_key).lower() == lowered_key:
            return str(value or default)
    return default


def _article_number_from_url(url: str | None) -> str:
    parsed = urllib.parse.urlparse(normalize_text(url or ""))
    match = IEEE_ARTICLE_NUMBER_PATH_PATTERN.match(parsed.path or "")
    return match.group("article_number") if match else ""


def _article_number_from_metadata(metadata: Mapping[str, Any] | None) -> str:
    for key in ("article_number", "articleNumber", "articleId", "arnumber"):
        value = normalize_text(str((metadata or {}).get(key) or ""))
        if value.isdigit():
            return value
    return ""


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = normalize_text(str(value or "")).lower()
    return normalized in {"1", "true", "yes", "y"}


def _landing_metadata_has_multimedia_scope(metadata: Mapping[str, Any] | None) -> bool:
    sections = (metadata or {}).get("sections")
    if isinstance(sections, Mapping) and _boolish(sections.get("multimedia")):
        return True
    return _boolish((metadata or {}).get("hasMultimedia")) or _boolish((metadata or {}).get("multimedia"))


def _script_value(text: str, key: str) -> Any:
    pattern = re.compile(IEEE_SCRIPT_VALUE_PATTERN_TEMPLATE.format(key=re.escape(key)), flags=re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    value = match.group("value")
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        return value[1:-1].encode("utf-8").decode("unicode_escape", errors="replace")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    return value


def _first_metadata_text(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if isinstance(value, Mapping):
            value = value.get("value") or value.get("text")
        text = normalize_text(str(value or ""))
        if text:
            return text
    return ""


def _ieee_author_name(author: Any) -> str:
    if isinstance(author, Mapping):
        for key in ("name", "preferredName", "fullName", "authorName"):
            text = normalize_text(str(author.get(key) or ""))
            if text:
                return text
        first_name = normalize_text(str(author.get("firstName") or ""))
        last_name = normalize_text(str(author.get("lastName") or ""))
        return normalize_text(f"{first_name} {last_name}")
    return normalize_text(str(author or ""))


def _authors_from_ieee_metadata(metadata: Mapping[str, Any]) -> list[str]:
    authors = metadata.get("authors") or metadata.get("authorsList")
    if isinstance(authors, Mapping):
        authors = authors.get("authors") or authors.get("author")
    if not isinstance(authors, list):
        return []
    return dedupe_authors([_ieee_author_name(item) for item in authors if _ieee_author_name(item)])


def _extend_unique_text(target: list[str], values: list[str]) -> None:
    seen = {item.lower() for item in target}
    for value in values:
        text = normalize_text(str(value or ""))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        target.append(text)


def _keywords_from_ieee_metadata(metadata: Mapping[str, Any]) -> list[str]:
    keywords: list[str] = []
    raw_keywords = metadata.get("keywords")
    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if isinstance(item, Mapping):
                values = item.get("kwd") or item.get("keywords") or item.get("terms") or item.get("value")
                if isinstance(values, str):
                    _extend_unique_text(keywords, [values])
                elif isinstance(values, list):
                    _extend_unique_text(keywords, [str(value) for value in values])
                continue
            _extend_unique_text(keywords, [str(item)])
    elif isinstance(raw_keywords, str):
        _extend_unique_text(keywords, [part.strip() for part in re.split(r"[;,]", raw_keywords)])

    for key in (
        "authorKeywords",
        "author_terms",
        "authorTerms",
        "indexTerms",
        "ieeeTerms",
        "controlledTerms",
        "meshTerms",
        "pubTopics",
    ):
        value = metadata.get(key)
        if isinstance(value, list):
            _extend_unique_text(keywords, [str(item) for item in value])
        elif isinstance(value, str):
            _extend_unique_text(keywords, [part.strip() for part in re.split(r"[;,]", value)])
    return keywords


def _parse_landing_metadata(html_text: str) -> dict[str, Any]:
    parsed = extract_assignment_json(html_text, IEEE_METADATA_ASSIGNMENT)
    metadata = dict(parsed) if isinstance(parsed, Mapping) else {}
    for key in (
        "articleNumber",
        "articleId",
        "isDynamicHtml",
        "html_flag",
        "ml_html_flag",
        "pdfUrl",
        "pdfPath",
        "doi",
        "title",
        "displayDocTitle",
        "formulaStrippedArticleTitle",
        "publicationTitle",
        "publicationDate",
        "abstract",
    ):
        if key not in metadata:
            value = _script_value(html_text, key)
            if value not in (None, ""):
                metadata[key] = value
    return metadata


def _reference_doi_from_ieee_reference(item: Mapping[str, Any]) -> str:
    links = item.get("links")
    if isinstance(links, Mapping):
        for key in ("crossRefLink", "doiLink"):
            value = normalize_text(str(links.get(key) or ""))
            match = _reference_doi_match(value)
            if match is not None:
                return normalize_doi(match.group(0).rstrip(").,;")) or ""
    for key in ("doi", "googleScholarStructredQuery", "googleScholarStructuredQuery", "text"):
        value = normalize_text(str(item.get(key) or ""))
        match = _reference_doi_match(value)
        if match is not None:
            return normalize_doi(match.group(0).rstrip(").,;")) or ""
    return ""


def _reference_doi_match(value: str) -> re.Match[str] | None:
    for match in DOI_PATTERN.finditer(value):
        if match.start() == 0 or not value[match.start() - 1].isalnum():
            return match
    return None


def _references_from_ieee_reference_payload(payload: Mapping[str, Any]) -> list[dict[str, str | None]]:
    raw_references = payload.get("references")
    if not isinstance(raw_references, list):
        return []
    references: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_references, start=1):
        if not isinstance(item, Mapping):
            continue
        raw_text = normalize_text(html_lib.unescape(strip_html_tags(str(item.get("text") or "")) or ""))
        if not raw_text:
            continue
        label = normalize_text(str(item.get("order") or index))
        key = (label, raw_text)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            {
                "label": label or None,
                "raw": raw_text,
                "doi": _reference_doi_from_ieee_reference(item) or None,
                "title": normalize_text(html_lib.unescape(strip_html_tags(str(item.get("title") or "")) or "")) or None,
            }
        )
    return references


def _merge_ieee_metadata(base_metadata: Mapping[str, Any], landing_metadata: Mapping[str, Any], response_url: str) -> dict[str, Any]:
    merged = dict(base_metadata or {})
    title = (
        strip_html_tags(
            _first_metadata_text(
                landing_metadata,
                "formulaStrippedArticleTitle",
                "displayDocTitle",
                "title",
            )
        )
        or normalize_text(str(merged.get("title") or ""))
    )
    abstract = strip_html_tags(_first_metadata_text(landing_metadata, "abstract")) or normalize_text(str(merged.get("abstract") or ""))
    authors = _authors_from_ieee_metadata(landing_metadata)
    base_authors = [
        normalize_text(str(item))
        for item in (merged.get("authors") or [])
        if normalize_text(str(item))
    ]
    keywords: list[str] = []
    base_keywords = merged.get("keywords") or []
    if isinstance(base_keywords, str):
        _extend_unique_text(keywords, [part.strip() for part in re.split(r"[;,]", base_keywords)])
    elif isinstance(base_keywords, list):
        _extend_unique_text(keywords, [str(item) for item in base_keywords])
    _extend_unique_text(keywords, _keywords_from_ieee_metadata(landing_metadata))
    article_number = _article_number_from_metadata(landing_metadata) or _article_number_from_url(response_url)
    doi = normalize_doi(_first_metadata_text(landing_metadata, "doi") or str(merged.get("doi") or ""))
    merged.update(
        {
            "provider": "ieee",
            "official_provider": True,
            "publisher": merged.get("publisher") or "IEEE",
            "doi": doi or merged.get("doi"),
            "title": title or None,
            "abstract": abstract or None,
            "journal_title": _first_metadata_text(landing_metadata, "publicationTitle") or merged.get("journal_title") or merged.get("journal"),
            "published": _first_metadata_text(
                landing_metadata,
                "publicationDate",
                "onlineDate",
                "publicationYear",
            )
            or merged.get("published"),
            "keywords": keywords,
            "landing_page_url": response_url,
            "authors": dedupe_authors([*authors, *base_authors]),
            "article_number": article_number or None,
            "articleNumber": article_number or None,
            "articleId": _first_metadata_text(landing_metadata, "articleId") or article_number or None,
            "isDynamicHtml": _boolish(landing_metadata.get("isDynamicHtml")),
            "html_flag": _boolish(landing_metadata.get("html_flag")),
            "ml_html_flag": _boolish(landing_metadata.get("ml_html_flag")),
            "pdfUrl": _first_metadata_text(landing_metadata, "pdfUrl") or None,
            "pdfPath": _first_metadata_text(landing_metadata, "pdfPath") or None,
            "raw_ieee_metadata": dict(landing_metadata),
        }
    )
    return merged


def _scan_ieee_block_page_tokens(html_text: str) -> bool:
    lowered = normalize_text(html_text).lower()
    return any(token in lowered for token in IEEE_ACCESS_BLOCK_TEXT_TOKENS)


def _looks_like_ieee_block_page(
    html_text: str,
    *,
    context: RuntimeContext | None = None,
    source_url: str | None = None,
) -> bool:
    if not isinstance(context, RuntimeContext):
        return _scan_ieee_block_page_tokens(html_text)
    key = context.build_parse_cache_key(
        provider="ieee",
        role="access_block_page",
        source=source_url,
        body=html_text,
        parser="text-token-scan",
        config={"tokens": IEEE_ACCESS_BLOCK_TEXT_TOKENS},
    )
    return bool(context.get_or_set_parse_cache(key, lambda: _scan_ieee_block_page_tokens(html_text)))


def _clean_ieee_article(article: Tag) -> None:
    for selector in IEEE_EXTRACTION_CLEANUP_SELECTORS:
        try:
            for node in list(article.select(selector)):
                if isinstance(node, Tag):
                    if "href^='javascript:'" in selector and node.name == "a" and _is_ieee_bibliography_anchor(node):
                        continue
                    node.decompose()
        except Exception:
            continue
    for node in list(article.find_all(True)):
        if isinstance(node, Tag) and _ieee_tag_has_ignored_asset_url(node):
            node.decompose()
    for node in list(article.find_all(True)):
        if not isinstance(node, Tag):
            continue
        text = normalize_text(node.get_text(" ", strip=True))
        classes = {
            normalize_text(str(item)).lower()
            for item in (node.get("class") or [])
        }
        if "kicker" in classes and IEEE_SECTION_MARKER_PATTERN.fullmatch(text):
            node.decompose()
            continue
        if node.name in {"span", "div"} and IEEE_SECTION_MARKER_PATTERN.fullmatch(text) and not node.find(True):
            node.decompose()
    for anchor in list(article.find_all("a")):
        if not isinstance(anchor, Tag):
            continue
        href = normalize_text(str(anchor.get("href") or ""))
        if href.lower().startswith("javascript:"):
            anchor.attrs.pop("href", None)
            if _is_ieee_bibliography_anchor(anchor):
                continue
            if normalize_text(anchor.get_text(" ", strip=True)):
                anchor.unwrap()
            else:
                anchor.decompose()
            continue
        for attr in ("onclick", "data-docId", "data-docid", "data-figure-id"):
            anchor.attrs.pop(attr, None)


def _is_ieee_bibliography_anchor(anchor: Tag) -> bool:
    attrs = getattr(anchor, "attrs", None) or {}
    if normalize_text(str(attrs.get("ref-type") or "")).lower() == "bibr":
        return True
    for key in ("anchor", "data-range"):
        value = normalize_text(str(attrs.get(key) or ""))
        if re.fullmatch(r"ref\d+[a-z]?", value, flags=re.IGNORECASE):
            return True
    return False


def _annotate_ieee_inline_media_blocks(article: Tag, source_url: str) -> None:
    for block in article.select("div.figure.figure-full"):
        if not isinstance(block, Tag):
            continue
        asset = _ieee_asset_from_figure_full_block(block, source_url)
        if asset is None:
            continue
        inline_url = normalize_text(str(asset.get("url") or asset.get("full_size_url") or asset.get("preview_url") or ""))
        if not inline_url:
            continue
        block["data-paper-fetch-inline-src"] = inline_url
        block["data-paper-fetch-inline-alt"] = normalize_text(str(asset.get("heading") or asset.get("caption") or "Figure"))


def _ieee_marker_counts(article: Tag) -> dict[str, int]:
    return {
        "sections": len(article.select("div.section, div.section_2, section")),
        "headings": len(article.select("h2, h3, h4")),
        "paragraphs": len(article.select("p")),
        "figures": len(article.select("figure, .figure, .fig, [id^='fig']")),
        "tables": len(article.select("table, .table, [id^='table']")),
        "formulas": len(article.select("tex-math, .tex-math, math, .formula, .disp-formula")),
    }


def _absolute_ieee_url(raw_url: str, fallback_url: str = "") -> str:
    url = normalize_text(str(raw_url or ""))
    if not url or url.startswith("#") or url.lower().startswith("javascript:"):
        return ""
    if url.startswith("/"):
        return urllib.parse.urljoin(IEEE_BASE_URL, url)
    base_url = normalize_text(str(fallback_url or "")) or IEEE_BASE_URL
    if not urllib.parse.urlparse(base_url).scheme:
        base_url = urllib.parse.urljoin(IEEE_BASE_URL, base_url)
    return urllib.parse.urljoin(base_url, url)


def _absolute_ieee_asset_url(raw_url: str, source_url: str) -> str:
    return _absolute_ieee_url(raw_url, source_url)


def _ieee_asset_url_path(url: str) -> str:
    return urllib.parse.urlparse(normalize_text(str(url or ""))).path.lower()


def _is_ignored_ieee_asset_url(url: str) -> bool:
    # Kept as a fallback contract for the historical Xplore support icon path;
    # DOM and asset heuristics below are the primary filters for new markup.
    return _ieee_asset_url_path(url).endswith(IEEE_SUPPORT_ICON_PATH)


def _small_html_dimension(value: Any, *, max_size: int = 32) -> bool:
    normalized = normalize_text(str(value or "")).lower().rstrip("px")
    if not normalized:
        return False
    try:
        return 0 < int(float(normalized)) <= max_size
    except (TypeError, ValueError):
        return False


def _ieee_support_icon_text(value: str) -> bool:
    normalized = normalize_text(value).lower()
    if not normalized:
        return False
    tokens = set(normalized.replace("-", " ").replace("_", " ").split())
    return "icon" in tokens and ("support" in tokens or "help" in tokens)


def _looks_like_ieee_support_icon_node(node: Tag) -> bool:
    attrs = getattr(node, "attrs", None) or {}
    text_parts = [
        normalize_text(str(attrs.get("alt") or "")),
        normalize_text(str(attrs.get("title") or "")),
        normalize_text(str(attrs.get("aria-label") or "")),
        normalize_text(str(attrs.get("id") or "")),
    ]
    class_values = attrs.get("class")
    if isinstance(class_values, (list, tuple, set)):
        text_parts.extend(normalize_text(str(item)) for item in class_values)
    else:
        text_parts.append(normalize_text(str(class_values or "")))
    semantic_match = _ieee_support_icon_text(" ".join(text_parts))
    if not semantic_match:
        return False
    width_small = _small_html_dimension(attrs.get("width"))
    height_small = _small_html_dimension(attrs.get("height"))
    return width_small or height_small or normalize_text(getattr(node, "name", "")).lower() == "img"


def _ieee_tag_has_ignored_asset_url(node: Tag) -> bool:
    if _looks_like_ieee_support_icon_node(node):
        return True
    for attr_name in IEEE_ASSET_URL_ATTRS:
        value = normalize_text(str(node.get(attr_name) or ""))
        if value and _is_ignored_ieee_asset_url(_absolute_ieee_asset_url(value, IEEE_BASE_URL)):
            return True
    return False


def _has_ieee_supplementary_file_suffix(url: str) -> bool:
    parsed = urllib.parse.urlparse(normalize_text(str(url or "")))
    path = urllib.parse.unquote(parsed.path).lower()
    query = urllib.parse.unquote(parsed.query).lower()
    return has_supplementary_file_suffix(
        path,
        extra_suffixes=IEEE_SUPPLEMENTARY_EXTRA_FILE_SUFFIXES,
    ) or has_supplementary_file_suffix(
        query,
        extra_suffixes=IEEE_SUPPLEMENTARY_EXTRA_FILE_SUFFIXES,
    )


def _supplementary_assets_from_ieee_multimedia_payload(
    payload: Mapping[str, Any],
    source_url: str,
) -> list[dict[str, str]]:
    raw_items = payload.get("multimedia")
    if not isinstance(raw_items, list):
        return []
    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        url = _absolute_ieee_asset_url(
            str(item.get("filePath") or item.get("fileUrl") or item.get("downloadUrl") or item.get("url") or ""),
            source_url,
        )
        if not url or _is_ignored_ieee_asset_url(url):
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        filename = normalize_text(str(item.get("fileName") or ""))
        title = normalize_text(str(item.get("title") or ""))
        description = normalize_text(html_lib.unescape(strip_html_tags(str(item.get("description") or "")) or ""))
        asset: dict[str, str] = {
            "kind": "supplementary",
            "heading": title or filename or "Supplementary Material",
            "caption": description,
            "url": url,
            "section": "supplementary",
        }
        if filename:
            asset["filename_hint"] = filename
        media_type = normalize_text(str(item.get("mediaType") or ""))
        if media_type:
            asset["media_type"] = media_type
        media_doi = normalize_doi(str(item.get("doi") or ""))
        if media_doi:
            asset["doi"] = media_doi
        assets.append(asset)
    return assets


def _ieee_supplementary_token_match(text: str) -> bool:
    normalized = normalize_text(text).lower()
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    for token in IEEE_SUPPLEMENTARY_SEMANTIC_TOKENS:
        normalized_token = token.lower()
        if normalized_token in normalized:
            return True
        if re.sub(r"[^a-z0-9]+", "", normalized_token) in compact:
            return True
    return False


def _ieee_node_identity_text(node: Tag, *, include_accessible_labels: bool = True) -> str:
    values: list[str] = []
    for key, value in (getattr(node, "attrs", None) or {}).items():
        normalized_key = normalize_text(str(key)).lower()
        if normalized_key in {"href", "src", "srcset"}:
            continue
        if normalized_key in {"title", "aria-label"} and not include_accessible_labels:
            continue
        if (
            normalized_key in {"id", "class", "role", "title", "aria-label"}
            or normalized_key.startswith("data-")
        ):
            if isinstance(value, list):
                values.extend(normalize_text(str(item)) for item in value)
            else:
                values.append(normalize_text(str(value)))
    return " ".join(value for value in values if value)


def _ieee_direct_heading_texts(node: Tag) -> list[str]:
    texts: list[str] = []
    for child in node.find_all(True, recursive=False):
        if not isinstance(child, Tag):
            continue
        child_name = normalize_text(getattr(child, "name", "")).lower()
        if re.fullmatch(r"h[1-6]", child_name):
            text = normalize_text(child.get_text(" ", strip=True))
            if text:
                texts.append(text)
            continue
        child_identity = _ieee_node_identity_text(child)
        if child_name != "header" and "header" not in child_identity.lower():
            continue
        for heading in child.find_all(re.compile(r"^h[1-6]$")):
            if isinstance(heading, Tag):
                text = normalize_text(heading.get_text(" ", strip=True))
                if text:
                    texts.append(text)
    return texts


def _is_ieee_supplementary_scope_node(node: Tag) -> bool:
    node_name = normalize_text(getattr(node, "name", "")).lower()
    if node_name not in {"section", "div", "aside", "ul", "ol"}:
        return False
    if _ieee_supplementary_token_match(_ieee_node_identity_text(node)):
        return True
    return any(
        _ieee_supplementary_token_match(text)
        for text in _ieee_direct_heading_texts(node)
    )


def _is_descendant_of_any(node: Tag, ancestors: list[Tag]) -> bool:
    parent = node.parent
    while isinstance(parent, Tag):
        if any(parent is ancestor for ancestor in ancestors):
            return True
        parent = parent.parent
    return False


def _ieee_supplementary_scope_nodes(soup: BeautifulSoup) -> list[Tag]:
    scopes: list[Tag] = []
    for node in soup.find_all(True):
        if not isinstance(node, Tag):
            continue
        if not _is_ieee_supplementary_scope_node(node):
            continue
        if _is_descendant_of_any(node, scopes):
            continue
        scopes.append(node)
    return scopes


def _is_ieee_marked_supplementary_anchor(anchor: Tag) -> bool:
    return _ieee_supplementary_token_match(
        _ieee_node_identity_text(anchor, include_accessible_labels=False)
    )


def _ieee_anchor_semantic_text(anchor: Tag, href: str) -> str:
    values = [
        normalize_text(anchor.get_text(" ", strip=True)),
        href,
        normalize_text(str(anchor.get("title") or "")),
        normalize_text(str(anchor.get("aria-label") or "")),
    ]
    for key, value in anchor.attrs.items():
        normalized_key = normalize_text(str(key)).lower()
        if normalized_key.startswith("data-"):
            values.append(normalize_text(str(value or "")))
    return " ".join(value for value in values if value).lower()


def _is_ieee_supplementary_anchor(
    anchor: Tag,
    source_url: str,
    *,
    in_explicit_scope: bool,
) -> bool:
    href = normalize_text(str(anchor.get("href") or ""))
    absolute_url = _absolute_ieee_asset_url(href, source_url)
    if not absolute_url or _is_ignored_ieee_asset_url(absolute_url):
        return False
    semantic_text = _ieee_anchor_semantic_text(anchor, href)
    if in_explicit_scope:
        return _ieee_supplementary_token_match(
            semantic_text
        ) or _has_ieee_supplementary_file_suffix(absolute_url)
    return _is_ieee_marked_supplementary_anchor(anchor) and (
        _ieee_supplementary_token_match(semantic_text) or _has_ieee_supplementary_file_suffix(absolute_url)
    )


def _ieee_supplementary_asset_from_anchor(
    anchor: Tag,
    source_url: str,
    *,
    in_explicit_scope: bool,
) -> dict[str, str] | None:
    if not _is_ieee_supplementary_anchor(anchor, source_url, in_explicit_scope=in_explicit_scope):
        return None
    href = normalize_text(str(anchor.get("href") or ""))
    absolute_url = _absolute_ieee_asset_url(href, source_url)
    heading = (
        normalize_text(anchor.get_text(" ", strip=True))
        or normalize_text(str(anchor.get("title") or ""))
        or normalize_text(str(anchor.get("aria-label") or ""))
        or "Supplementary Material"
    )
    return {
        "kind": "supplementary",
        "heading": heading,
        "caption": "",
        "url": absolute_url,
        "section": "supplementary",
    }


def _extract_ieee_supplementary_assets(html_text: str, source_url: str) -> list[dict[str, str]]:
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(html_text, choose_parser())
    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    scope_nodes = _ieee_supplementary_scope_nodes(soup)

    def add_anchor(anchor: Tag, *, in_explicit_scope: bool) -> None:
        asset = _ieee_supplementary_asset_from_anchor(
            anchor,
            source_url,
            in_explicit_scope=in_explicit_scope,
        )
        if asset is None:
            return
        key = _ieee_asset_dedupe_key(asset)
        if key and key in seen:
            return
        if key:
            seen.add(key)
        assets.append(asset)

    for scope in scope_nodes:
        for anchor in scope.find_all("a", href=True):
            if isinstance(anchor, Tag):
                add_anchor(anchor, in_explicit_scope=True)
    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag) or _is_descendant_of_any(anchor, scope_nodes):
            continue
        add_anchor(anchor, in_explicit_scope=False)
    return assets


def _is_ieee_mediastore_url(url: str) -> bool:
    return _ieee_asset_url_path(url).startswith(IEEE_MEDIASTORE_PATH_PREFIX)


def _looks_like_ieee_large_media_url(url: str) -> bool:
    path = _ieee_asset_url_path(url)
    return bool(re.search(r"-(?:large|full)\.[a-z0-9]+$", path))


def _looks_like_ieee_small_media_url(url: str) -> bool:
    path = _ieee_asset_url_path(url)
    return bool(re.search(r"-(?:small|thumb|thumbnail|preview)\.[a-z0-9]+$", path))


def _first_ieee_text(node: Tag, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        candidate = node.select_one(selector)
        if isinstance(candidate, Tag):
            text = normalize_text(candidate.get_text(" ", strip=True))
            if text:
                return text
    return ""


def _dedupe_ieee_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = normalize_text(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def _ieee_media_urls_from_attrs(
    node: Tag,
    source_url: str,
    *,
    tag_name: str,
    attr_name: str,
) -> list[str]:
    urls: list[str] = []
    for tag in node.find_all(tag_name):
        if not isinstance(tag, Tag):
            continue
        raw_url = normalize_text(str(tag.get(attr_name) or ""))
        absolute_url = _absolute_ieee_asset_url(raw_url, source_url)
        if absolute_url and _is_ieee_mediastore_url(absolute_url) and not _is_ignored_ieee_asset_url(absolute_url):
            urls.append(absolute_url)
    return _dedupe_ieee_urls(urls)


def _preferred_ieee_url(urls: list[str], *, prefer_large: bool) -> str:
    if prefer_large:
        for url in urls:
            if _looks_like_ieee_large_media_url(url):
                return url
    else:
        for url in urls:
            if _looks_like_ieee_small_media_url(url):
                return url
    return urls[0] if urls else ""


def _ieee_asset_from_figure_full_block(block: Tag, source_url: str) -> dict[str, str] | None:
    class_names = {normalize_text(str(item)).lower() for item in (block.get("class") or [])}
    kind = "table" if "table" in class_names else "figure"
    href_urls = _ieee_media_urls_from_attrs(block, source_url, tag_name="a", attr_name="href")
    image_urls = _ieee_media_urls_from_attrs(block, source_url, tag_name="img", attr_name="src")
    full_size_url = _preferred_ieee_url(href_urls, prefer_large=True)
    preview_url = _preferred_ieee_url(image_urls, prefer_large=False)
    if not full_size_url:
        full_size_url = _preferred_ieee_url(image_urls, prefer_large=True)
    url = full_size_url or preview_url
    if not url:
        return None

    title = _first_ieee_text(block, (".title",))
    caption = _first_ieee_text(block, (".figcaption", "figcaption"))
    image = block.find("img")
    alt_text = normalize_text(str(image.get("alt") or "")) if isinstance(image, Tag) else ""
    caption = caption or title or alt_text
    heading = title or caption[:80] or alt_text or ("Table" if kind == "table" else "Figure")
    asset = {
        "kind": kind,
        "heading": heading,
        "caption": caption,
        "url": url,
        "section": "body",
        "render_state": "inline",
    }
    if preview_url:
        asset["preview_url"] = preview_url
    if full_size_url:
        asset["full_size_url"] = full_size_url
    return asset


def _extract_ieee_body_media_assets(article_html: str, source_url: str) -> list[dict[str, str]]:
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(article_html, choose_parser())
    assets: list[dict[str, str]] = []
    for block in soup.select("div.figure.figure-full"):
        if not isinstance(block, Tag):
            continue
        asset = _ieee_asset_from_figure_full_block(block, source_url)
        if asset is not None:
            assets.append(asset)
    return assets


def _ieee_asset_has_ignored_url(asset: Mapping[str, Any]) -> bool:
    semantic_text = " ".join(
        normalize_text(str(asset.get(field) or ""))
        for field in ("heading", "caption", "alt", "title", "aria_label", "filename_hint")
    )
    width = asset.get("width")
    height = asset.get("height")
    if _ieee_support_icon_text(semantic_text) and (_small_html_dimension(width) or _small_html_dimension(height)):
        return True
    for field in (
        "url",
        "full_size_url",
        "preview_url",
        "original_url",
        "download_url",
        "source_url",
        "figure_page_url",
    ):
        value = normalize_text(str(asset.get(field) or ""))
        if value and _is_ignored_ieee_asset_url(value):
            return True
    return False


def _ieee_asset_dedupe_key(asset: Mapping[str, Any]) -> str:
    for field in (
        "full_size_url",
        "url",
        "download_url",
        "source_url",
        "preview_url",
        "original_url",
        "figure_page_url",
    ):
        value = normalize_text(str(asset.get(field) or ""))
        if value:
            return value
    return ""


def _ieee_asset_kind(asset: Mapping[str, Any]) -> str:
    return normalize_text(str(asset.get("kind") or asset.get("asset_type") or "")).lower()


def _ieee_asset_priority(asset: Mapping[str, Any]) -> int:
    return IEEE_ASSET_KIND_PRIORITY.get(_ieee_asset_kind(asset), 0)


def _ieee_asset_field_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(normalize_text(str(value)))


def _merge_ieee_missing_asset_fields(
    target: dict[str, Any],
    source: Mapping[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if not _ieee_asset_field_has_value(target.get(field)) and _ieee_asset_field_has_value(source.get(field)):
            target[field] = source[field]


def _unique_ieee_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for asset in assets:
        identity = id(asset)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(asset)
    return unique


def _ieee_asset_values_for_fields(asset: Mapping[str, Any], fields: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for field in fields:
        value = normalize_text(str(asset.get(field) or ""))
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _ieee_asset_identity_index(
    assets: list[dict[str, Any]],
    *,
    fields: tuple[str, ...] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        values = _ieee_asset_values_for_fields(asset, fields) if fields is not None else _ieee_asset_identity_values(asset)
        for value in values:
            bucket = index.setdefault(value, [])
            if all(existing is not asset for existing in bucket):
                bucket.append(asset)
    return index


def _ieee_index_matches(index: Mapping[str, list[dict[str, Any]]], values: list[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for value in values:
        for asset in index.get(value, []):
            identity = id(asset)
            if identity in seen:
                continue
            seen.add(identity)
            matches.append(asset)
    return matches


def _select_ieee_asset_survivor(candidates: list[dict[str, Any]], current_assets: list[dict[str, Any]]) -> dict[str, Any]:
    current_order = {id(asset): index for index, asset in enumerate(current_assets)}
    fallback_order = len(current_assets)
    return max(
        candidates,
        key=lambda asset: (
            _ieee_asset_priority(asset),
            -current_order.get(id(asset), fallback_order),
        ),
    )


def _asset_identity_index_in_list(assets: list[dict[str, Any]], target: dict[str, Any]) -> int | None:
    for index, asset in enumerate(assets):
        if asset is target:
            return index
    return None


def _merge_ieee_asset_group(
    current_assets: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    merge_fields: tuple[str, ...],
) -> dict[str, Any]:
    candidates = _unique_ieee_assets(candidates)
    survivor = _select_ieee_asset_survivor(candidates, current_assets)
    existing_positions = [
        position
        for position in (_asset_identity_index_in_list(current_assets, candidate) for candidate in candidates)
        if position is not None
    ]
    insert_at = min(existing_positions) if existing_positions else len(current_assets)
    for candidate in candidates:
        if candidate is survivor:
            continue
        _merge_ieee_missing_asset_fields(survivor, candidate, merge_fields)

    survivor_position = _asset_identity_index_in_list(current_assets, survivor)
    for index in range(len(current_assets) - 1, -1, -1):
        asset = current_assets[index]
        if any(asset is candidate for candidate in candidates) and asset is not survivor:
            del current_assets[index]

    if survivor_position is None:
        current_assets.insert(min(insert_at, len(current_assets)), survivor)
    return survivor


def _dedupe_ieee_assets_by_priority(
    assets: list[dict[str, Any]],
    *,
    merge_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for asset in assets:
        identity_index = _ieee_asset_identity_index(deduped)
        overlaps = _ieee_index_matches(identity_index, _ieee_asset_identity_values(asset))
        if overlaps:
            _merge_ieee_asset_group(deduped, [*overlaps, asset], merge_fields=merge_fields)
            continue
        deduped.append(asset)
    return deduped


def _normalize_ieee_html_assets(
    extracted_assets: list[dict[str, Any]],
    body_media_assets: list[dict[str, str]],
    source_url: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in [*body_media_assets, *extracted_assets]:
        asset = _absolute_ieee_html_asset_fields(dict(item), source_url)
        if _ieee_asset_has_ignored_url(asset):
            continue
        candidates.append(asset)
    return _dedupe_ieee_assets_by_priority(candidates, merge_fields=IEEE_ASSET_URL_FIELDS)


def _absolute_ieee_html_asset_fields(asset: dict[str, Any], source_url: str) -> dict[str, Any]:
    for field in IEEE_ASSET_URL_FIELDS:
        value = normalize_text(str(asset.get(field) or ""))
        if value:
            asset[field] = _absolute_ieee_asset_url(value, source_url)
    return asset


def _ieee_asset_identity_values(asset: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for field in (*IEEE_ASSET_URL_FIELDS, "path", "link"):
        value = normalize_text(str(asset.get(field) or ""))
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _merge_ieee_assets(
    extracted_assets: list[Mapping[str, Any]] | None,
    downloaded_assets: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged = _dedupe_ieee_assets_by_priority(
        [dict(item) for item in extracted_assets or []],
        merge_fields=IEEE_ASSET_URL_FIELDS,
    )
    merge_fields = (*IEEE_ASSET_URL_FIELDS, *IEEE_DOWNLOAD_MERGE_FIELDS)
    for item in downloaded_assets or []:
        asset = dict(item)
        strong_index = _ieee_asset_identity_index(merged, fields=IEEE_STRONG_ASSET_IDENTITY_FIELDS)
        weak_index = _ieee_asset_identity_index(merged, fields=IEEE_WEAK_ASSET_IDENTITY_FIELDS)
        identity_index = _ieee_asset_identity_index(merged)
        strong_matches = _ieee_index_matches(
            strong_index,
            _ieee_asset_values_for_fields(asset, IEEE_STRONG_ASSET_IDENTITY_FIELDS),
        )
        weak_matches = _ieee_index_matches(
            weak_index,
            [
                *_ieee_asset_values_for_fields(asset, IEEE_WEAK_ASSET_IDENTITY_FIELDS),
                *_ieee_asset_values_for_fields(asset, IEEE_STRONG_ASSET_IDENTITY_FIELDS),
            ],
        )
        identity_matches = _ieee_index_matches(identity_index, _ieee_asset_identity_values(asset))
        matches = _unique_ieee_assets([*strong_matches, *weak_matches, *identity_matches])
        if matches:
            _merge_ieee_asset_group(merged, [*matches, asset], merge_fields=merge_fields)
            continue
        merged.append(asset)
    return merged


def _extract_ieee_html(
    html_text: str,
    source_url: str,
    *,
    metadata: Mapping[str, Any],
    context: RuntimeContext | None = None,
) -> IeeeHtmlExtraction:
    if BeautifulSoup is None:
        raise ProviderFailure(ERROR, "IEEE HTML extraction requires BeautifulSoup.")
    if _looks_like_ieee_block_page(html_text, context=context, source_url=source_url):
        raise ProviderFailure(NO_RESULT, "IEEE dynamic HTML endpoint returned an access, challenge, or unable page.")

    html_for_parse = re.sub(r"^\s*<\?xml[^>]*>\s*", "", html_text)
    soup = BeautifulSoup(html_for_parse, choose_parser())
    article = soup.select_one("#article")
    if not isinstance(article, Tag):
        raise ProviderFailure(NO_RESULT, "IEEE dynamic HTML endpoint did not include #article.")
    asset_html = str(article)
    _clean_ieee_article(article)
    _annotate_ieee_inline_media_blocks(article, source_url)
    marker_counts = _ieee_marker_counts(article)
    article_text = normalize_text(article.get_text(" ", strip=True))
    if not article_text and not any(marker_counts.values()):
        raise ProviderFailure(NO_RESULT, "IEEE dynamic HTML endpoint returned an empty #article shell.")
    if marker_counts["paragraphs"] <= 0 and marker_counts["sections"] <= 0:
        raise ProviderFailure(NO_RESULT, "IEEE dynamic HTML endpoint did not include article body sections or paragraphs.")

    section_hints = collect_html_section_hints(
        article,
        title=str(metadata.get("title") or "") or None,
    )
    lines: list[str] = []
    render_container_markdown(article, lines, level=2)
    markdown_text = clean_rendered_markdown("\n".join(lines), noise_profile="ieee")
    if not normalize_text(markdown_text):
        raise ProviderFailure(NO_RESULT, "IEEE dynamic HTML endpoint did not produce usable Markdown.")
    cleaned_html = str(article)
    extracted_assets = extract_scoped_html_assets(
        cleaned_html,
        source_url,
        asset_profile="body",
    )
    extracted_assets.extend(_extract_ieee_supplementary_assets(cleaned_html, source_url))
    extracted_assets = _normalize_ieee_html_assets(
        [dict(item) for item in extracted_assets],
        _extract_ieee_body_media_assets(asset_html, source_url),
        source_url,
    )
    return IeeeHtmlExtraction(
        html_text=cleaned_html,
        markdown_text=markdown_text,
        section_hints=list(section_hints),
        abstract_sections=[],
        extracted_assets=[dict(item) for item in extracted_assets],
        marker_counts=marker_counts,
    )


def _abstract_markdown(metadata: Mapping[str, Any]) -> str:
    title = normalize_text(str(metadata.get("title") or ""))
    abstract = normalize_text(str(metadata.get("abstract") or ""))
    lines: list[str] = []
    if title:
        lines.extend([f"# {title}", ""])
    if abstract:
        lines.extend(["## Abstract", "", abstract])
    return "\n".join(lines).strip()


def _pdf_candidates(landing_attempt: IeeeLandingAttempt) -> list[str]:
    metadata = landing_attempt.merged_metadata
    candidates: list[str] = []
    for key in ("pdfUrl", "pdfPath"):
        value = normalize_text(str(metadata.get(key) or landing_attempt.landing_metadata.get(key) or ""))
        if value:
            candidates.append(urllib.parse.urljoin(IEEE_BASE_URL, value))
    if landing_attempt.article_number:
        candidates.append(IEEE_STAMP_URL_TEMPLATE.format(article_number=landing_attempt.article_number))
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _dedupe_urls(urls: list[str | None]) -> list[str]:
    deduped: list[str] = []
    for raw_url in urls:
        url = normalize_text(str(raw_url or ""))
        if url and url not in deduped:
            deduped.append(url)
    return deduped


def _is_ieee_rest_document_url(url: str, article_number: str) -> bool:
    parsed = urllib.parse.urlparse(normalize_text(url))
    host = normalize_text(parsed.netloc).lower()
    path = normalize_text(parsed.path).rstrip("/")
    return bool(
        article_number
        and host.endswith("ieeexplore.ieee.org")
        and path == f"/rest/document/{article_number}"
    )


def _playwright_response_headers(response: Any | None) -> dict[str, str]:
    if response is None:
        return {}
    try:
        headers = response.all_headers()
    except Exception:
        headers = getattr(response, "headers", {}) or {}
    return {
        normalize_text(str(key)).lower(): str(value)
        for key, value in dict(headers or {}).items()
        if normalize_text(str(key))
    }


def _playwright_response_status(response: Any | None) -> int | None:
    if response is None:
        return None
    try:
        return int(getattr(response, "status", 0) or 0) or None
    except Exception:
        return None


def _pdf_failure_diagnostics(failure: PdfFetchFailure | None) -> dict[str, Any] | None:
    if failure is None:
        return None
    diagnostics: dict[str, Any] = {
        "kind": failure.kind,
        "message": failure.message,
    }
    if failure.details:
        diagnostics["details"] = dict(failure.details)
    return diagnostics


def _provider_failure_diagnostics(failure: ProviderFailure | None) -> dict[str, Any] | None:
    if failure is None:
        return None
    diagnostics: dict[str, Any] = {
        "code": failure.code,
        "message": failure.message,
    }
    if failure.source_trail:
        diagnostics["source_trail"] = list(failure.source_trail)
    return diagnostics


class IeeeClient(ProviderClient):
    name = "ieee"

    def __init__(self, transport: HttpTransport, env: Mapping[str, str]) -> None:
        self.transport = transport
        self.env = dict(env)
        self.user_agent = build_user_agent(env)

    def probe_status(self) -> ProviderStatusResult:
        return summarize_capability_status(
            self.name,
            official_provider=self.official_provider,
            checks=[
                build_provider_status_check(
                    "html_route",
                    OK,
                    "IEEE Xplore direct REST HTML and clean-browser HTML fallback routes are available when the article exposes ml_html/full HTML.",
                    details={"mode": "direct_rest_html_or_clean_browser_html"},
                ),
                build_provider_status_check(
                    PDF_FALLBACK,
                    OK,
                    "IEEE Xplore PDF fallback is available for text-only full text when direct HTTP or a seeded browser returns a real PDF payload.",
                    details={"mode": "direct_http_pdf_or_seeded_browser_pdf"},
                ),
            ],
        )

    def _landing_headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": self.user_agent,
        }

    def _rest_headers(self, document_url: str) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": document_url,
            "User-Agent": self.user_agent,
            "x-security-request": "required",
        }

    def _document_url(self, article_number: str) -> str:
        return IEEE_DOCUMENT_URL_TEMPLATE.format(article_number=article_number)

    def _rest_url(self, article_number: str) -> str:
        return IEEE_REST_URL_TEMPLATE.format(article_number=article_number)

    def _references_url(self, article_number: str, *, start: int = 0) -> str:
        url = IEEE_REFERENCES_URL_TEMPLATE.format(article_number=article_number)
        if start > 0:
            return f"{url}?start={start}&rowsPerPage={IEEE_REFERENCE_PAGE_SIZE}"
        return url

    def _multimedia_url(self, article_number: str) -> str:
        return IEEE_MULTIMEDIA_URL_TEMPLATE.format(article_number=article_number)

    def _multimedia_headers(self, document_url: str) -> dict[str, str]:
        headers = self._rest_headers(document_url)
        headers.update(
            {
                "Origin": IEEE_BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
                "cache-http-response": "true",
                "Pragma": "no-cache",
                "Cache-Control": "no-store",
            }
        )
        return headers

    def _fetch_reference_metadata(self, article_number: str, document_url: str, *, expected_count: int = 0) -> list[dict[str, str | None]]:
        if not article_number:
            return []
        references: list[dict[str, str | None]] = []
        seen: set[tuple[str, str]] = set()
        max_expected = max(0, expected_count)
        for page_index in range(IEEE_MAX_REFERENCE_PAGES):
            start = page_index * IEEE_REFERENCE_PAGE_SIZE
            if max_expected and start >= max_expected:
                break
            response = self.transport.request(
                "GET",
                self._references_url(article_number, start=start),
                headers=self._rest_headers(document_url),
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                retry_on_transient=True,
            )
            body = bytes(response.get("body") or b"")
            if not body:
                break
            try:
                payload = json.loads(decode_html(body))
            except (TypeError, ValueError):
                break
            if not isinstance(payload, Mapping):
                break
            page_references = _references_from_ieee_reference_payload(payload)
            if not page_references:
                break
            added = 0
            for reference in page_references:
                key = (
                    normalize_text(str(reference.get("label") or "")),
                    normalize_text(str(reference.get("raw") or "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                references.append(reference)
                added += 1
            if added == 0 or len(page_references) < IEEE_REFERENCE_PAGE_SIZE:
                break
        return references

    def _fetch_multimedia_assets(self, landing_attempt: IeeeLandingAttempt) -> list[dict[str, str]]:
        if not landing_attempt.article_number or not _landing_metadata_has_multimedia_scope(landing_attempt.landing_metadata):
            return []
        document_url = self._document_url(landing_attempt.article_number)
        multimedia_url = self._multimedia_url(landing_attempt.article_number)
        try:
            response = self.transport.request(
                "GET",
                multimedia_url,
                headers=self._multimedia_headers(document_url),
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                retry_on_transient=True,
            )
            body = bytes(response.get("body") or b"")
            payload = json.loads(decode_html(body))
        except (RequestFailure, TypeError, ValueError):
            return []
        if not isinstance(payload, Mapping):
            return []
        response_url = _absolute_ieee_url(str(response.get("url") or multimedia_url), multimedia_url)
        return _supplementary_assets_from_ieee_multimedia_payload(payload, response_url)

    def _html_extraction_assets_with_landing_payloads(
        self,
        extraction: IeeeHtmlExtraction,
        landing_attempt: IeeeLandingAttempt,
    ) -> list[dict[str, Any]]:
        return _dedupe_ieee_assets_by_priority(
            [*list(extraction.extracted_assets), *self._fetch_multimedia_assets(landing_attempt)],
            merge_fields=IEEE_ASSET_URL_FIELDS,
        )

    def fetch_metadata(self, query: Mapping[str, str | None]) -> ProviderMetadata:
        raise ProviderFailure(
            NOT_SUPPORTED,
            "IEEE publisher metadata is read from the Xplore landing page during full-text retrieval; routing relies on Crossref metadata.",
        )

    def _resolve_landing_url(self, doi: str, metadata: Mapping[str, Any]) -> str:
        article_number = _article_number_from_metadata(metadata)
        document_url = self._document_url(article_number) if article_number else None
        return choose_public_landing_page_url(
            metadata.get("landing_page_url"),
            document_url,
            f"https://doi.org/{urllib.parse.quote(doi, safe='')}",
        ) or f"https://doi.org/{urllib.parse.quote(doi, safe='')}"

    def _fetch_landing_attempt(self, doi: str, metadata: Mapping[str, Any]) -> IeeeLandingAttempt:
        normalized_doi = normalize_doi(doi)
        if not normalized_doi:
            raise ProviderFailure(NOT_SUPPORTED, "IEEE full-text retrieval requires a DOI.")
        landing_url = self._resolve_landing_url(normalized_doi, metadata)
        try:
            landing_fetch = fetch_landing_html(
                landing_url,
                transport=self.transport,
                headers=self._landing_headers(),
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                max_redirects=MAX_IEEE_LANDING_REDIRECTS,
                raise_on_redirect_limit=True,
                retry_on_transient=True,
            )
        except LandingRedirectLimitExceeded as exc:
            raise ProviderFailure(
                ERROR,
                f"IEEE landing retrieval exceeded {MAX_IEEE_LANDING_REDIRECTS} redirects.",
            ) from exc
        except RequestFailure as exc:
            raise map_request_failure(exc) from exc

        landing_metadata = _parse_landing_metadata(landing_fetch.html_text)
        article_number = (
            _article_number_from_metadata(landing_metadata)
            or _article_number_from_url(landing_fetch.final_url)
            or _article_number_from_metadata(metadata)
            or _article_number_from_url(landing_url)
        )
        if not article_number:
            raise ProviderFailure(NO_RESULT, "IEEE landing page did not expose an article number.")
        merged_metadata = _merge_ieee_metadata(metadata, landing_metadata, landing_fetch.final_url)
        reference_count = 0
        try:
            reference_count = int(landing_metadata.get("referenceCount") or 0)
        except (TypeError, ValueError):
            reference_count = 0
        if reference_count > 0:
            try:
                reference_metadata = self._fetch_reference_metadata(
                    article_number,
                    self._document_url(article_number),
                    expected_count=reference_count,
                )
            except RequestFailure:
                reference_metadata = []
            if reference_metadata:
                merged_metadata["references"] = reference_metadata
        if not merged_metadata.get("doi"):
            merged_metadata["doi"] = normalized_doi
        merged_metadata["article_number"] = article_number
        merged_metadata["articleNumber"] = article_number
        return IeeeLandingAttempt(
            normalized_doi=normalized_doi,
            landing_url=landing_url,
            response_url=landing_fetch.final_url,
            html_text=landing_fetch.html_text,
            merged_metadata=merged_metadata,
            article_number=article_number,
            landing_metadata=landing_metadata,
        )

    def _fetch_dynamic_html_payload(
        self,
        landing_attempt: IeeeLandingAttempt,
        *,
        context: RuntimeContext | None = None,
    ) -> RawFulltextPayload:
        article_number = landing_attempt.article_number
        document_url = self._document_url(article_number)
        rest_url = self._rest_url(article_number)
        try:
            response = self.transport.request(
                "GET",
                rest_url,
                headers=self._rest_headers(document_url),
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                retry_on_transient=True,
            )
        except RequestFailure as exc:
            raise map_request_failure(exc) from exc
        response_url = _absolute_ieee_url(str(response.get("url") or rest_url), rest_url)
        body = bytes(response.get("body") or b"")
        html_text = decode_html(body)
        extraction = _extract_ieee_html(
            html_text,
            response_url,
            metadata=landing_attempt.merged_metadata,
            context=context,
        )
        diagnostics = HtmlQualityAssessor("ieee").assess(
            extraction.markdown_text,
            landing_attempt.merged_metadata,
            html_text=extraction.html_text,
            title=str(landing_attempt.merged_metadata.get("title") or ""),
            requested_url=rest_url,
            final_url=response_url,
            response_status=int(response.get("status_code") or 0) or None,
            section_hints=extraction.section_hints,
        )
        if not diagnostics.accepted:
            raise ProviderFailure(NO_RESULT, availability_failure_message(diagnostics))
        content_type = _header_value(response.get("headers"), "content-type", "text/html")
        cleaned_body = extraction.html_text.encode("utf-8")
        extracted_assets = self._html_extraction_assets_with_landing_payloads(extraction, landing_attempt)
        return build_provider_payload(
            provider=self.name,
            route_kind="html",
            source_url=response_url,
            content_type=content_type,
            body=cleaned_body,
            markdown_text=extraction.markdown_text,
            merged_metadata=landing_attempt.merged_metadata,
            diagnostics={
                "availability_diagnostics": diagnostics.to_dict(),
                "extraction": {
                    "abstract_sections": extraction.abstract_sections,
                    "section_hints": extraction.section_hints,
                    "marker_counts": extraction.marker_counts,
                },
            },
            reason="Downloaded full text from the IEEE Xplore dynamic HTML route.",
            extracted_assets=extracted_assets,
            trace_markers=[fulltext_marker("ieee", "ok", route="html")],
        )

    def _fetch_browser_html_payload(
        self,
        landing_attempt: IeeeLandingAttempt,
        *,
        direct_html_failure: ProviderFailure | None,
        context: RuntimeContext,
    ) -> RawFulltextPayload:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except Exception as exc:  # pragma: no cover - exercised by missing dependency deployments
            raise ProviderFailure(
                ERROR,
                "Playwright is not installed; cannot use IEEE browser HTML fallback.",
            ) from exc

        article_number = landing_attempt.article_number
        document_url = self._document_url(article_number)
        rest_url = self._rest_url(article_number)
        browser_context = None
        page = None
        rest_responses: list[Any] = []
        navigation_response = None
        browser_final_url = document_url
        navigation_status: int | None = None
        payload_source = ""
        response_status: int | None = None
        response_headers: dict[str, str] = {}
        source_url = document_url
        html_text = ""

        try:
            browser_context = context.new_playwright_context(
                headless=True,
                user_agent=self.user_agent,
                locale="en-US",
                viewport={"width": 1440, "height": 1600},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )

            def route_handler(route: Any) -> None:
                try:
                    resource_type = normalize_text(getattr(route.request, "resource_type", "")).lower()
                    if resource_type in BROWSER_HTML_BLOCKED_RESOURCE_TYPES:
                        route.abort()
                        return
                    route.continue_()
                except Exception:
                    try:
                        route.continue_()
                    except Exception:
                        pass

            browser_context.route("**/*", route_handler)
            page = browser_context.new_page()

            def remember_rest_response(response: Any) -> None:
                if _is_ieee_rest_document_url(str(getattr(response, "url", "") or ""), article_number):
                    rest_responses.append(response)

            page.on("response", remember_rest_response)
            try:
                navigation_response = page.goto(
                    document_url,
                    wait_until="domcontentloaded",
                    timeout=IEEE_BROWSER_HTML_NAVIGATION_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                navigation_response = None
            browser_final_url = normalize_text(str(getattr(page, "url", "") or "")) or document_url
            navigation_status = _playwright_response_status(navigation_response)

            if not rest_responses:
                try:
                    page.wait_for_timeout(IEEE_BROWSER_HTML_REST_WAIT_TIMEOUT_MS)
                except Exception:
                    pass

            for response in reversed(rest_responses):
                try:
                    body = response.body()
                except Exception:
                    continue
                if not isinstance(body, (bytes, bytearray)) or not body:
                    continue
                html_text = decode_html(bytes(body))
                source_url = _absolute_ieee_url(str(getattr(response, "url", "") or rest_url), rest_url)
                response_headers = _playwright_response_headers(response)
                response_status = _playwright_response_status(response)
                payload_source = "rest_response"
                break

            if not html_text:
                try:
                    page.wait_for_selector("#article", timeout=IEEE_BROWSER_HTML_DOM_WAIT_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    pass
                try:
                    has_article = page.locator("#article").count() > 0
                except Exception:
                    has_article = False
                if not has_article:
                    raise ProviderFailure(
                        NO_RESULT,
                        "IEEE browser HTML fallback did not capture REST full-text HTML or #article DOM.",
                    )
                html_text = str(page.content() or "")
                browser_final_url = normalize_text(str(getattr(page, "url", "") or "")) or browser_final_url
                source_url = browser_final_url
                response_headers = {"content-type": "text/html"}
                response_status = navigation_status
                payload_source = "dom_article"
        except ProviderFailure:
            raise
        except Exception as exc:
            message = normalize_text(str(exc)) or exc.__class__.__name__
            raise ProviderFailure(ERROR, f"IEEE browser HTML fallback failed ({message}).") from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if browser_context is not None:
                try:
                    browser_context.close()
                except Exception:
                    pass

        extraction = _extract_ieee_html(
            html_text,
            source_url,
            metadata=landing_attempt.merged_metadata,
            context=context,
        )
        diagnostics = HtmlQualityAssessor("ieee").assess(
            extraction.markdown_text,
            landing_attempt.merged_metadata,
            html_text=extraction.html_text,
            title=str(landing_attempt.merged_metadata.get("title") or ""),
            requested_url=rest_url if payload_source == "rest_response" else document_url,
            final_url=source_url,
            response_status=response_status,
            section_hints=extraction.section_hints,
        )
        if not diagnostics.accepted:
            raise ProviderFailure(NO_RESULT, availability_failure_message(diagnostics))
        content_type = _header_value(response_headers, "content-type", "text/html")
        cleaned_body = extraction.html_text.encode("utf-8")
        extracted_assets = self._html_extraction_assets_with_landing_payloads(extraction, landing_attempt)
        return build_provider_payload(
            provider=self.name,
            route_kind="html",
            source_url=source_url,
            content_type=content_type,
            body=cleaned_body,
            markdown_text=extraction.markdown_text,
            merged_metadata=landing_attempt.merged_metadata,
            diagnostics={
                "availability_diagnostics": diagnostics.to_dict(),
                "browser_html": {
                    "fetcher": "playwright_html",
                    "payload_source": payload_source,
                    "document_url": document_url,
                    "rest_url": rest_url,
                    "final_url": browser_final_url,
                    "navigation_status": navigation_status,
                    "response_status": response_status,
                    "direct_html_failure": _provider_failure_diagnostics(direct_html_failure),
                },
                "extraction": {
                    "abstract_sections": extraction.abstract_sections,
                    "section_hints": extraction.section_hints,
                    "marker_counts": extraction.marker_counts,
                },
            },
            reason="Downloaded full text from the IEEE Xplore clean-browser HTML fallback route.",
            fetcher="playwright_html",
            extracted_assets=extracted_assets,
            trace_markers=[
                fulltext_marker("ieee", "fail", route="html"),
                fulltext_marker("ieee", "ok", route="browser_html"),
                fulltext_marker("ieee", "ok", route="html"),
            ],
        )

    def _fetch_pdf_payload(
        self,
        landing_attempt: IeeeLandingAttempt,
        *,
        html_failure_message: str,
        warnings: list[str],
        context: RuntimeContext,
        html_trace_markers: list[str] | None = None,
    ) -> RawFulltextPayload:
        document_url = self._document_url(landing_attempt.article_number)
        candidates = _pdf_candidates(landing_attempt)
        headers = {
            "User-Agent": self.user_agent,
            "Referer": document_url,
        }
        artifact_dir = (
            context.download_dir / IEEE_PDF_FALLBACK_ARTIFACT_DIR_NAME
            if context.download_dir is not None
            else None
        )
        direct_failure: PdfFetchFailure | None = None
        try:
            pdf_result = PdfFallbackStrategy(
                transport=self.transport,
                headers=headers,
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                artifact_dir=artifact_dir,
                seed_urls=[document_url],
                fetcher=fetch_pdf_over_http,
            ).fetch(candidates)
            fetcher = "direct_http"
        except PdfFetchFailure as exc:
            direct_failure = exc
            browser_seed_urls = _dedupe_urls([landing_attempt.response_url, document_url])

            def run_browser_pdf(active_artifact_dir: Path):
                return fetch_pdf_with_playwright(
                    candidates,
                    artifact_dir=active_artifact_dir,
                    browser_user_agent=self.user_agent,
                    headless=True,
                    referer=document_url,
                    seed_urls=browser_seed_urls,
                    context=context,
                )

            try:
                if artifact_dir is None:
                    with tempfile.TemporaryDirectory(prefix="paper_fetch_ieee_pdf_") as tempdir:
                        pdf_result = run_browser_pdf(Path(tempdir))
                else:
                    pdf_result = run_browser_pdf(artifact_dir)
                fetcher = "seeded_browser"
            except PdfFetchFailure as browser_exc:
                raise PdfFetchFailure(
                    browser_exc.kind,
                    (
                        "IEEE PDF fallback failed. "
                        f"Direct HTTP failure: {direct_failure.message} "
                        f"Browser fallback failure: {browser_exc.message}"
                    ),
                    details={
                        "candidates": list(candidates),
                        "direct_failure": _pdf_failure_diagnostics(direct_failure),
                        "browser_failure": _pdf_failure_diagnostics(browser_exc),
                    },
                ) from browser_exc
        pdf_diagnostics = {
            "fetcher": fetcher,
            "candidates": list(candidates),
            "direct_failure": _pdf_failure_diagnostics(direct_failure),
        }
        payload_warnings = list(warnings)
        if direct_failure is not None:
            payload_warnings.append(
                f"IEEE direct PDF fallback was not usable ({direct_failure.message}); browser PDF fallback succeeded."
            )
        payload_warnings.append(
            "Full text was extracted from IEEE PDF fallback after the IEEE HTML paths were not usable."
        )
        return build_provider_payload(
            provider=self.name,
            route_kind=PDF_FALLBACK,
            source_url=pdf_result.final_url,
            content_type=PDF_MIME_TYPE,
            body=pdf_result.pdf_bytes,
            markdown_text=pdf_result.markdown_text,
            merged_metadata=landing_attempt.merged_metadata,
            diagnostics={PDF_FALLBACK: pdf_diagnostics},
            reason=(
                "Downloaded full text from the IEEE Xplore seeded-browser PDF fallback route."
                if fetcher == "seeded_browser"
                else "Downloaded full text from the IEEE Xplore direct PDF fallback route."
            ),
            suggested_filename=pdf_result.suggested_filename,
            html_failure_message=html_failure_message,
            content_needs_local_copy=True,
            warnings=payload_warnings,
            trace_markers=[
                *list(html_trace_markers or [fulltext_marker("ieee", "fail", route="html")]),
                fulltext_marker("ieee", "ok", route=PDF_FALLBACK),
            ],
            needs_local_copy=True,
        )

    def _abstract_only_payload(
        self,
        landing_attempt: IeeeLandingAttempt,
        *,
        warnings: list[str],
        trace_markers: list[str],
        diagnostics: Mapping[str, Any] | None = None,
        ) -> RawFulltextPayload:
        markdown_text = _abstract_markdown(landing_attempt.merged_metadata)
        if not markdown_text:
            raise ProviderFailure(NO_RESULT, "IEEE landing metadata did not include provider abstract content.")
        body = markdown_text.encode("utf-8")
        return build_provider_payload(
            provider=self.name,
            route_kind=ABSTRACT_ONLY,
            source_url=landing_attempt.response_url,
            content_type="text/markdown",
            body=body,
            markdown_text=markdown_text,
            merged_metadata=landing_attempt.merged_metadata,
            diagnostics=diagnostics,
            reason="IEEE provider route only exposed abstract-level content.",
            warnings=warnings,
            trace_markers=[*trace_markers, fulltext_marker("ieee", ABSTRACT_ONLY)],
        )

    def fetch_raw_fulltext(
        self,
        doi: str,
        metadata: ProviderMetadata,
        *,
        context: RuntimeContext | None = None,
    ) -> RawFulltextPayload:
        runtime_context = self._runtime_context(context)
        landing_attempt = self._fetch_landing_attempt(doi, metadata)
        pdf_failure_diagnostics: dict[str, Any] | None = None

        def run_browser_html(state: ProviderWaterfallState) -> RawFulltextPayload:
            return self._fetch_browser_html_payload(
                landing_attempt,
                direct_html_failure=state.failure("html"),
                context=runtime_context,
            )

        def run_pdf(state: ProviderWaterfallState) -> RawFulltextPayload:
            nonlocal pdf_failure_diagnostics
            html_failure = state.failure("html")
            browser_html_failure = state.failure("browser_html")
            html_failure_message = (
                html_failure.message if html_failure is not None else "IEEE dynamic HTML route failed."
            )
            if browser_html_failure is not None:
                html_failure_message = f"{html_failure_message} Browser HTML fallback: {browser_html_failure.message}"
            try:
                return self._fetch_pdf_payload(
                    landing_attempt,
                    html_failure_message=html_failure_message,
                    warnings=[],
                    context=runtime_context,
                    html_trace_markers=state.source_markers(),
                )
            except PdfFetchFailure as exc:
                pdf_failure_diagnostics = _pdf_failure_diagnostics(exc)
                raise ProviderFailure(NO_RESULT, exc.message) from exc

        def run_abstract(state: ProviderWaterfallState) -> RawFulltextPayload:
            return self._abstract_only_payload(
                landing_attempt,
                warnings=[],
                trace_markers=state.source_markers(),
                diagnostics={
                    "html_failure": _provider_failure_diagnostics(state.failure("html")),
                    "browser_html_failure": _provider_failure_diagnostics(state.failure("browser_html")),
                    PDF_FALLBACK: pdf_failure_diagnostics
                    or _provider_failure_diagnostics(state.failure("pdf")),
                },
            )

        def final_failure(state: ProviderWaterfallState) -> ProviderFailure:
            failures = [
                ("html", state.failure("html") or ProviderFailure(NO_RESULT, "IEEE dynamic HTML route failed.")),
                (
                    "browser_html",
                    state.failure("browser_html")
                    or ProviderFailure(NO_RESULT, "IEEE browser HTML fallback failed."),
                ),
                ("pdf", state.failure("pdf") or ProviderFailure(NO_RESULT, "IEEE PDF fallback failed.")),
                (
                    "abstract",
                    state.failure("abstract") or ProviderFailure(NO_RESULT, "IEEE abstract fallback failed."),
                ),
            ]
            combined = combine_provider_failures(failures)
            return ProviderFailure(
                combined.code,
                "IEEE full text could not be retrieved. " + combined.message,
                warnings=state.warnings,
                source_trail=[
                    fulltext_marker("ieee", "fail", route="html"),
                    fulltext_marker("ieee", "fail", route="browser_html"),
                    fulltext_marker("ieee", "fail", route="pdf"),
                ],
            )

        return run_provider_waterfall(
            [
                ProviderWaterfallStep(
                    label="html",
                    run=lambda _state: self._fetch_dynamic_html_payload(landing_attempt, context=runtime_context),
                    failure_marker=fulltext_marker("ieee", "fail", route="html"),
                    continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,
                    failure_warning=lambda failure, _state: (
                        f"IEEE dynamic HTML route was not usable ({failure.message}); "
                        "attempting clean-browser HTML fallback."
                    ),
                ),
                ProviderWaterfallStep(
                    label="browser_html",
                    run=run_browser_html,
                    failure_marker=fulltext_marker("ieee", "fail", route="browser_html"),
                    continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,
                    failure_warning=lambda failure, _state: (
                        f"IEEE browser HTML fallback was not usable ({failure.message}); attempting PDF fallback."
                    ),
                ),
                ProviderWaterfallStep(
                    label="pdf",
                    run=run_pdf,
                    failure_marker=fulltext_marker("ieee", "fail", route="pdf"),
                    continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,
                    failure_warning=lambda failure, _state: (
                        f"IEEE PDF fallback was not usable ({failure.message})."
                    ),
                ),
                ProviderWaterfallStep(
                    label="abstract",
                    run=run_abstract,
                    continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,
                ),
            ],
            final_failure_factory=final_failure,
        )

    def html_to_markdown(
        self,
        html_text: str,
        source_url: str,
        *,
        metadata: Mapping[str, Any],
        context: RuntimeContext,
    ) -> tuple[str, Mapping[str, Any]]:
        extraction = _extract_ieee_html(html_text, source_url, metadata=metadata, context=context)
        return extraction.markdown_text, {
            "abstract_sections": extraction.abstract_sections,
            "section_hints": extraction.section_hints,
            "marker_counts": extraction.marker_counts,
        }

    def download_related_assets(
        self,
        doi: str,
        metadata: ProviderMetadata,
        raw_payload: RawFulltextPayload,
        output_dir: Path | None,
        *,
        asset_profile: AssetProfile = "all",
        context: RuntimeContext | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        context = self._runtime_context(context, output_dir=output_dir)
        if output_dir is None or asset_profile == "none":
            return empty_asset_results()
        content = raw_payload.content
        if normalize_text(content.route_kind if content is not None else "").lower() != "html":
            return empty_asset_results()
        extracted_assets = [
            dict(item)
            for item in (content.extracted_assets if content is not None else [])
        ]
        body_assets, supplementary_assets = split_body_and_supplementary_assets(extracted_assets)
        body_assets = [
            dict(item)
            for item in body_assets
            if (
                normalize_text(str(item.get("kind") or "")).lower() in {"figure", "table", "formula"}
                and normalize_text(str(item.get("section") or "")).lower() != "supplementary"
            )
        ]
        if not body_assets and not supplementary_assets:
            return empty_asset_results()
        merged_metadata = content.merged_metadata if content is not None else raw_payload.merged_metadata
        article_id = (
            normalize_doi(str((merged_metadata or {}).get("doi") or doi or ""))
            or normalize_doi(doi)
            or normalize_text(str(metadata.get("title") or ""))
            or raw_payload.source_url
        )
        landing_or_source_url = normalize_text(
            str((merged_metadata or {}).get("landing_page_url") or raw_payload.source_url or "")
        )
        article_number = (
            _article_number_from_metadata(merged_metadata)
            or _article_number_from_metadata(metadata)
            or _article_number_from_url(raw_payload.source_url)
            or _article_number_from_url(landing_or_source_url)
        )
        canonical_landing_url = self._document_url(article_number) if article_number else landing_or_source_url
        seed_urls = [canonical_landing_url] if canonical_landing_url else []
        asset_download_concurrency = resolve_asset_download_concurrency(context.env)
        body_result = (
            download_figure_assets(
                self.transport,
                article_id=article_id,
                assets=body_assets,
                output_dir=output_dir,
                user_agent=self.user_agent,
                asset_profile=asset_profile,
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": canonical_landing_url,
                },
                seed_urls=seed_urls,
                asset_download_concurrency=asset_download_concurrency,
            )
            if body_assets
            else empty_asset_results()
        )
        supplementary_result = (
            download_supplementary_assets(
                self.transport,
                article_id=article_id,
                assets=supplementary_assets,
                output_dir=output_dir,
                user_agent=self.user_agent,
                asset_profile=asset_profile,
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": canonical_landing_url,
                },
                seed_urls=seed_urls,
                asset_download_concurrency=asset_download_concurrency,
            )
            if supplementary_assets and asset_profile == "all"
            else empty_asset_results()
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

    def asset_download_failure_warning(self, exc: ProviderFailure | RequestFailure | OSError) -> str:
        message = exc.message if isinstance(exc, ProviderFailure) else str(exc)
        return f"IEEE related assets could not be downloaded: {message}"

    def to_article_model(
        self,
        metadata: ProviderMetadata,
        raw_payload: RawFulltextPayload,
        *,
        downloaded_assets: list[Mapping[str, Any]] | None = None,
        asset_failures: list[Mapping[str, Any]] | None = None,
        context: RuntimeContext | None = None,
    ):
        del context
        content = raw_payload.content
        merged_metadata = content.merged_metadata if content is not None else raw_payload.merged_metadata
        article_metadata = merged_metadata if isinstance(merged_metadata, Mapping) else metadata
        doi = normalize_doi(str(article_metadata.get("doi") or metadata.get("doi") or ""))
        markdown_text = str((content.markdown_text if content is not None else "") or "").strip()
        route = normalize_text(content.route_kind if content is not None else "").lower()
        source = "ieee_pdf" if route == PDF_FALLBACK else "ieee_html"
        trace = list(raw_payload.trace or trace_from_markers([fulltext_marker("ieee", "ok", route="html")]))
        warnings = list(raw_payload.warnings)
        if asset_failures:
            warnings.append(f"IEEE related assets were only partially downloaded ({len(asset_failures)} failed).")
        if not markdown_text:
            warnings.append("IEEE retrieval did not produce usable Markdown.")
            return metadata_only_article(
                source=source,
                metadata=article_metadata,
                doi=doi or None,
                warnings=warnings,
                trace=trace,
            )
        extraction_payload = content.diagnostics.get("extraction") if content is not None else None
        abstract_sections = (
            list(extraction_payload.get("abstract_sections") or [])
            if isinstance(extraction_payload, Mapping)
            else []
        )
        section_hints = (
            list(extraction_payload.get("section_hints") or [])
            if isinstance(extraction_payload, Mapping)
            else []
        )
        extracted_assets = list(content.extracted_assets if content is not None else [])
        assets = _merge_ieee_assets(extracted_assets, list(downloaded_assets or []))
        availability_diagnostics = (
            dict(content.diagnostics.get("availability_diagnostics") or {})
            if content is not None and isinstance(content.diagnostics.get("availability_diagnostics"), Mapping)
            else None
        )
        article = article_from_markdown(
            source=source,
            metadata=article_metadata,
            doi=doi or None,
            markdown_text=markdown_text,
            abstract_sections=abstract_sections,
            section_hints=section_hints,
            assets=assets,
            warnings=warnings,
            trace=trace,
            availability_diagnostics=availability_diagnostics,
            semantic_losses={"formula_missing_count": markdown_text.count("[Formula unavailable]")},
            allow_downgrade_from_diagnostics=True,
        )
        if asset_failures:
            article.quality.asset_failures = [dict(item) for item in asset_failures]
        return article

    def describe_artifacts(
        self,
        raw_payload: RawFulltextPayload,
        *,
        downloaded_assets: list[Mapping[str, Any]] | None = None,
        asset_failures: list[Mapping[str, Any]] | None = None,
    ) -> ProviderArtifacts:
        artifacts = super().describe_artifacts(
            raw_payload,
            downloaded_assets=downloaded_assets,
            asset_failures=asset_failures,
        )
        content = raw_payload.content
        if normalize_text(content.route_kind if content is not None else "").lower() != PDF_FALLBACK:
            return artifacts
        return ProviderArtifacts(
            assets=list(artifacts.assets),
            asset_failures=list(artifacts.asset_failures),
            allow_related_assets=False,
            text_only=True,
            skip_warning=(
                "IEEE PDF fallback currently returns text-only full text; "
                "figure and supplementary asset downloads are not implemented for PDF fallback."
            ),
            skip_trace=trace_from_markers([download_marker("ieee_assets_skipped_text_only")]),
        )
