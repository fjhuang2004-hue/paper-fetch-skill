"""arXiv metadata, official HTML, and PDF fallback provider client."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import html as html_lib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from ..arxiv_id import (
    arxiv_id_from_doi,
    arxiv_id_from_query,
    canonical_arxiv_abs_url,
    canonical_arxiv_doi,
    canonical_arxiv_html_url,
    canonical_arxiv_pdf_url,
    normalize_arxiv_id,
)
from ..config import build_user_agent, resolve_asset_download_concurrency
from ..common_patterns import EXTENDED_DATA_FIGURE_LABEL, WORD_TOKEN_PATTERN
from ..extraction.html import assets as html_assets
from ..extraction.html._runtime import clean_markdown
from ..extraction.html.html_tags import HTML_DROP_TAGS
from ..extraction.html.semantics import (
    HTML_BLOCK_TAGS,
    SECTION_HEADING_PATTERN,
    heading_category,
    node_source_selector,
    section_hint_kind_for_category,
)
from ..extraction.html.tables import (
    TABLE_PLACEHOLDER_PREFIX,
    inject_inline_table_blocks,
    render_table_markdown,
    table_placeholder,
)
from ..formula.convert import normalize_latex
from ..http import (
    DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    HttpTransport,
    PDF_ACCEPT_HEADER,
    PDF_MIME_TYPE,
    RequestErrorCategory,
    RequestFailure,
    is_pdf_content_type,
)
from ..metadata.types import ProviderMetadata
from ..models import AssetProfile, article_from_markdown, metadata_only_article
from ..models.markdown import normalize_markdown_text
from ..provider_catalog import register_metadata_probe_short_circuit
from ..publisher_identity import DOI_PATTERN
from ..quality.html_availability import assess_plain_text_fulltext_availability
from ..quality.reason_codes import FULLTEXT
from ..runtime import RuntimeContext
from ..tracing import download_marker, fulltext_marker, trace_from_markers
from ..utils import dedupe_authors, empty_asset_results, normalize_text
from ._html_section_markdown import (
    INLINE_FIGURE_ALT_ATTR,
    INLINE_FIGURE_SRC_ATTR,
    render_clean_text_from_html,
    render_container_markdown,
    render_heading_text_from_html,
)
from ._html_asset_engine import merge_assets_by_identity
from ._payloads import build_provider_payload
from ._retry_categories import (
    DEFAULT_RETRYABLE_ASSET_ERROR_CATEGORIES,
    NETWORK_RETRYABLE_REASON_TOKENS,
)
from ._pdf_fallback import PdfFallbackStrategy, PdfFetchFailure, fetch_pdf_over_http
from ._waterfall import (
    DEFAULT_WATERFALL_CONTINUE_CODES,
    ProviderWaterfallStep,
    ProviderWaterfallState,
    run_provider_waterfall,
)
from ..reason_codes import ERROR, NO_RESULT, NOT_CONFIGURED, NOT_SUPPORTED, OK, PDF_FALLBACK
from .base import (
    ProviderArtifacts,
    ProviderClient,
    ProviderFailure,
    ProviderStatusResult,
    RawFulltextPayload,
    build_provider_status_check,
    map_request_failure,
    summarize_capability_status,
)

try:
    from bs4 import BeautifulSoup, Tag
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    BeautifulSoup = None
    Tag = None


MIN_HTML_MARKDOWN_WORDS = 500
ARXIV_ASSET_DOWNLOAD_CONCURRENCY_LIMIT = 2
ARXIV_IMAGE_ACCEPT = "image/avif,image/webp,image/*,*/*;q=0.8"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_API_ACCEPT = "application/atom+xml,application/xml,text/xml,*/*;q=0.8"
ARXIV_API_DELAY_SECONDS = 0.0
ARXIV_API_NUM_RETRIES = 0
_ARXIV_ATOM_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_WORD_PATTERN = WORD_TOKEN_PATTERN
_REFERENCE_YEAR_PATTERN = re.compile(r"\b((?:18|19|20)\d{2})\b")
_ARXIV_WATERMARK_PATTERN = re.compile(
    r"arxiv:\s*(?P<arxiv_id>[^\s\[]+)(?:\s+\[(?P<category>[^\]]+)\])?(?:\s+(?P<date>\d{1,2}\s+[A-Za-z]{3}\s+\d{4}))?",
    flags=re.IGNORECASE,
)
_ARXIV_AUTHOR_LABEL_PATTERN = re.compile(
    r"(?P<name>[^\d,;]+?)\s+(?:\d+(?:\s*,\s*\d+)*)\b"
)
_ARXIV_AUTHOR_BOUNDARY_PACKAGE = "paper_fetch.resources.arxiv"
_ARXIV_AUTHOR_BOUNDARY_RESOURCE = "author_boundaries.json"


def _load_arxiv_author_boundary_tokens(
    key: str, *, resource_name: str = _ARXIV_AUTHOR_BOUNDARY_RESOURCE
) -> tuple[str, ...]:
    """Load ar5iv/plain-text author-affiliation fallback boundaries.

    These are not a general country or institution knowledge base; they only
    mark common author/frontmatter boundary strings when arXiv HTML lacks clean
    person/affiliation structure.
    """

    try:
        payload = json.loads(
            resources.files(_ARXIV_AUTHOR_BOUNDARY_PACKAGE)
            .joinpath(resource_name)
            .read_text(encoding="utf-8")
        )
    except (
        AttributeError,
        FileNotFoundError,
        ModuleNotFoundError,
        OSError,
        json.JSONDecodeError,
    ):
        return ()
    if not isinstance(payload, dict):
        return ()
    values = payload.get(key)
    if not isinstance(values, list):
        return ()
    return tuple(token for item in values if (token := str(item).strip()))


def _compile_arxiv_author_boundary_pattern(
    tokens: Sequence[str], *, prefix: str, suffix: str
) -> re.Pattern[str]:
    if not tokens:
        return re.compile(r"a\A")
    return re.compile(prefix + "|".join(tokens) + suffix, flags=re.IGNORECASE)


def _compile_arxiv_author_country_boundary_pattern(
    tokens: Sequence[str],
) -> re.Pattern[str]:
    return _compile_arxiv_author_boundary_pattern(
        tokens, prefix=r"[,;]\s*(?:", suffix=r")(?![A-Za-z])"
    )


def _compile_arxiv_author_institution_boundary_pattern(
    tokens: Sequence[str],
) -> re.Pattern[str]:
    return _compile_arxiv_author_boundary_pattern(
        tokens, prefix=r"(?<![A-Za-z])(?:", suffix=r")(?![A-Za-z])"
    )


_ARXIV_AUTHOR_INSTITUTION_BOUNDARY_TOKENS = _load_arxiv_author_boundary_tokens(
    "institution_boundary_patterns"
)
_ARXIV_AUTHOR_COUNTRY_BOUNDARY_TOKENS = _load_arxiv_author_boundary_tokens(
    "country_boundary_patterns"
)
_ARXIV_AUTHOR_INSTITUTION_BOUNDARY_PATTERN = (
    _compile_arxiv_author_institution_boundary_pattern(
        _ARXIV_AUTHOR_INSTITUTION_BOUNDARY_TOKENS
    )
)
_ARXIV_AUTHOR_COUNTRY_BOUNDARY_PATTERN = (
    _compile_arxiv_author_country_boundary_pattern(
        _ARXIV_AUTHOR_COUNTRY_BOUNDARY_TOKENS
    )
)
_ARXIV_AUTHOR_ADDRESS_BOUNDARY_PATTERN = re.compile(
    r"[,;]\s*(?:[A-Z]{1,3}[- ]?)?\d{3,}\b"
)
_ARXIV_AUTHOR_COUNTRY_CODE_BOUNDARY_PATTERN = re.compile(r"[,;]\s*[A-Z]{2,3}\.?\s*$")
_ARXIV_EMAIL_PATTERN = re.compile(r"\b\S+@\S+\b")
_ARXIV_ORCID_PATTERN = re.compile(
    r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", flags=re.IGNORECASE
)
_ARXIV_INITIAL_TOKEN_PATTERN = re.compile(r"^[A-Z]\.?$")
_ARXIV_BASE_CHROME_SELECTORS = ("script", "style")
_ARXIV_AR5IV_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "watermark": ("#watermark-tr", ".ltx_page_header", ".ltx_page_footer"),
    "frontmatter_noise": (
        *_ARXIV_BASE_CHROME_SELECTORS,
        "math",
        ".ltx_note",
        ".ltx_contact",
        ".ltx_author_notes",
        ".ltx_role_email",
        ".ltx_role_orcid",
        ".ltx_role_affiliation",
        "a[href^='mailto:']",
        ".ltx_font_typewriter",
    ),
    "author_creators": (".ltx_creator.ltx_role_author",),
    "author_person_names": (".ltx_personname",),
    "document_title": ("h1.ltx_title_document",),
    "abstract": ("div.ltx_abstract",),
    "abstract_heading": (".ltx_title_abstract", "h1", "h2", "h3", "h4", "h5", "h6"),
    "bibliography_containers": ("section.ltx_bibliography", "section#bib"),
    "bibliography_items": (".ltx_bibitem", "li.ltx_bibitem"),
    "reference_noise": (
        *_ARXIV_BASE_CHROME_SELECTORS,
        ".ltx_bib_cited",
        ".ltx_bib_links",
    ),
    "reference_links": (".ltx_bib_links", ".ltx_bib_cited"),
    "reference_blocks": (".ltx_bibblock",),
    "reference_year": (".ltx_bib_year",),
    "reference_title": (".ltx_bib_title",),
    "algorithm_listing": ("div.ltx_listing",),
    "latexml_error_nodes": (".ltx_ERROR", ".undefined"),
    "math_nodes": ("math.ltx_Math",),
    "note_nodes": ("span.ltx_note",),
    "note_markers": (".ltx_note_mark", ".ltx_tag_note"),
    "note_content": (".ltx_note_content",),
    "listing_noise": (".ltx_rule", ".ltx_linenumber"),
    "listing_lines": (".ltx_listingline",),
    "article_root": ("article.ltx_document",),
    "article_chrome": (
        *_ARXIV_BASE_CHROME_SELECTORS,
        "nav",
        "header",
        "footer",
        "h1.ltx_title_document",
        "div.ltx_authors",
        "div.ltx_dates",
        "span.ltx_note.ltx_role_thanks",
        "span.ltx_note.ltx_note_frontmatter",
        "span.ltx_role_submissionid",
        "span.ltx_role_journal",
        "span.ltx_role_ccs",
        ".ltx_pagination",
    ),
}
# SITE_UI_COPY_REGRESSION_MARKER: site-owned UI copy; rerun extraction rules
# when publisher text changes.
# Legacy compatibility for ar5iv/LaTeXML failure pages whose DOM did not expose
# structured ltx_ERROR nodes; structural selectors remain the primary guard.
_ARXIV_AR5IV_FATAL_ERROR_TEXTS = (
    "an error in the conversion from latex to xml has occurred",
)
_ARXIV_UNDEFINED_MACRO_PATTERN = re.compile(r"^\\[A-Za-z@]+\*?$")
_ARXIV_TABLE_ID_PATTERN = re.compile(
    r"(?:^|[.])T(?P<number>\d+[A-Za-z]?)\b", flags=re.IGNORECASE
)
_ARXIV_ALGORITHM_ID_PATTERN = re.compile(
    r"(?:^|[.])algorithm(?P<number>\d+)\b", flags=re.IGNORECASE
)
_ARXIV_CAPTION_LABEL_PATTERN = re.compile(
    r"^(?P<label>(?:Table|Algorithm)\s+\d+[A-Za-z]?)[.:]?\s*(?P<caption>.*)$",
    flags=re.IGNORECASE,
)
# ar5iv can expose Nature preprint captions, including Extended Data figures;
# keep this provider-scoped to preserve the caption remainder capture.
_ARXIV_FIGURE_CAPTION_LABEL_PATTERN = re.compile(
    rf"^(?P<label>(?:Figure|Fig\.?|{re.escape(EXTENDED_DATA_FIGURE_LABEL)}\.?)\s+\d+[A-Za-z]?)[.:]?\s*(?P<caption>.*)$",
    flags=re.IGNORECASE,
)
_ARXIV_FIGURE_ID_PATTERN = re.compile(
    r"(?:^|[.])F(?P<number>\d+[A-Za-z]?(?:\.\d+[A-Za-z]?)?)(?=$|[.])",
    flags=re.IGNORECASE,
)
_ARXIV_PLACEHOLDER_PATTERN = re.compile(
    rf"\b{re.escape(TABLE_PLACEHOLDER_PREFIX)}\d{{4}}\b"
)
_ARXIV_UNESCAPED_DOLLAR_PATTERN = re.compile(r"(?<!\\)\$")
_ARXIV_RETRYABLE_ASSET_ERROR_CATEGORIES = DEFAULT_RETRYABLE_ASSET_ERROR_CATEGORIES
_ARXIV_HTML_FATAL_ERROR_PATTERNS = (
    *(
        re.compile(r"\s+".join(re.escape(part) for part in text.split()), re.IGNORECASE)
        for text in _ARXIV_AR5IV_FATAL_ERROR_TEXTS
    ),
)
_ARXIV_SECTION_HINT_SKIP_CLASS_TOKENS = {
    "ltx_toc",
    "ltx_toclist",
    "ltx_tocentry",
    "ltx_page_navbar",
    "ltx_page_header",
    "ltx_page_footer",
    "ltx_pagination",
    "ltx_authors",
    "ltx_dates",
    "ltx_role_thanks",
    "ltx_note_frontmatter",
}
_ARXIV_SECTION_HINT_STRUCTURAL_SKIP_TAGS = (
    "aside",
    "figcaption",
    "footer",
    "header",
    "nav",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
)
_ARXIV_SECTION_HINT_SKIP_TAGS = {
    "figure",
    "math",
    *(tag for tag in HTML_DROP_TAGS if tag in {"script", "style", "svg"}),
    *(tag for tag in _ARXIV_SECTION_HINT_STRUCTURAL_SKIP_TAGS if tag in HTML_BLOCK_TAGS),
}


@dataclass(frozen=True)
class ArxivSearch:
    id_list: Sequence[str]
    max_results: int = 1


@dataclass(frozen=True)
class ArxivApiAuthor:
    name: str


@dataclass(frozen=True)
class ArxivApiResult:
    entry_id: str
    updated: datetime | None
    published: datetime | None
    title: str
    authors: tuple[ArxivApiAuthor, ...]
    summary: str
    comment: str | None = None
    journal_ref: str | None = None
    doi: str | None = None
    primary_category: str | None = None
    categories: tuple[str, ...] = ()
    pdf_url: str | None = None
    short_id: str = ""

    def get_short_id(self) -> str:
        return self.short_id or arxiv_id_from_query(self.entry_id)


def _parse_arxiv_atom_datetime(value: str | None) -> datetime | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _atom_text(entry: ET.Element, path: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        normalize_text(
            entry.findtext(path, default="", namespaces=_ARXIV_ATOM_NAMESPACES)
        ),
    ).strip()


def _atom_attr(entry: ET.Element, path: str, attribute: str) -> str:
    node = entry.find(path, _ARXIV_ATOM_NAMESPACES)
    if node is None:
        return ""
    return normalize_text(node.get(attribute))


def _atom_pdf_url(entry: ET.Element) -> str:
    for link in entry.findall("atom:link", _ARXIV_ATOM_NAMESPACES):
        href = normalize_text(link.get("href"))
        if not href:
            continue
        title = normalize_text(link.get("title")).lower()
        content_type = normalize_text(link.get("type")).lower()
        if title == "pdf" or is_pdf_content_type(content_type) or "/pdf/" in href:
            return href
    return ""


def _parse_arxiv_atom_result(
    entry: ET.Element, *, requested_ids: Sequence[str]
) -> ArxivApiResult:
    entry_id = _atom_text(entry, "atom:id")
    short_id = arxiv_id_from_query(entry_id)
    if not short_id and len(requested_ids) == 1:
        short_id = requested_ids[0]
    authors = tuple(
        ArxivApiAuthor(name=name)
        for name in _dedupe_strings(
            _atom_text(author, "atom:name")
            for author in entry.findall("atom:author", _ARXIV_ATOM_NAMESPACES)
        )
    )
    categories = tuple(
        _dedupe_strings(
            category.get("term")
            for category in entry.findall("atom:category", _ARXIV_ATOM_NAMESPACES)
        )
    )
    primary_category = _atom_attr(entry, "arxiv:primary_category", "term")
    if not primary_category and categories:
        primary_category = categories[0]
    return ArxivApiResult(
        entry_id=entry_id,
        updated=_parse_arxiv_atom_datetime(_atom_text(entry, "atom:updated")),
        published=_parse_arxiv_atom_datetime(_atom_text(entry, "atom:published")),
        title=_atom_text(entry, "atom:title"),
        authors=authors,
        summary=_atom_text(entry, "atom:summary"),
        comment=_atom_text(entry, "arxiv:comment") or None,
        journal_ref=_atom_text(entry, "arxiv:journal_ref") or None,
        doi=_atom_text(entry, "arxiv:doi") or None,
        primary_category=primary_category or None,
        categories=categories,
        pdf_url=_atom_pdf_url(entry) or None,
        short_id=short_id,
    )


def _parse_arxiv_atom_results(
    body: bytes, *, requested_ids: Sequence[str]
) -> list[ArxivApiResult]:
    try:
        root = ET.fromstring(body.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        raise ValueError(f"Invalid arXiv API Atom XML: {exc}") from exc
    return [
        _parse_arxiv_atom_result(entry, requested_ids=requested_ids)
        for entry in root.findall("atom:entry", _ARXIV_ATOM_NAMESPACES)
    ]


class InternalArxivApiClient:
    def __init__(self, transport: HttpTransport, user_agent: str) -> None:
        self.transport = transport
        self.user_agent = user_agent

    def results(self, search: ArxivSearch) -> list[ArxivApiResult]:
        requested_ids = [
            normalized
            for raw_id in getattr(search, "id_list", [])
            if (normalized := normalize_arxiv_id(str(raw_id)))
        ]
        if not requested_ids:
            return []
        max_results = max(1, int(getattr(search, "max_results", 1) or 1))
        response = self.transport.request(
            "GET",
            ARXIV_API_URL,
            headers={
                "Accept": ARXIV_API_ACCEPT,
                "User-Agent": self.user_agent,
            },
            query={
                "id_list": ",".join(requested_ids),
                "max_results": str(max_results),
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        status_code = int(response.get("status_code") or 0)
        if status_code >= 400:
            raise RequestFailure(
                status_code,
                f"HTTP {status_code} for {ARXIV_API_URL}",
                body=bytes(response.get("body") or b""),
                headers=response.get("headers"),
                url=normalize_text(response.get("url")) or ARXIV_API_URL,
            )
        body = response.get("body") or b""
        if not isinstance(body, bytes):
            body = str(body).encode("utf-8")
        return _parse_arxiv_atom_results(body, requested_ids=requested_ids)


@dataclass(frozen=True)
class ArxivHtmlExtraction:
    markdown_text: str
    merged_metadata: dict[str, Any]
    extracted_assets: list[dict[str, Any]]
    section_hints: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True)
class ArxivSemanticPreparation:
    entries: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    warnings: list[str]


def _arxiv_ar5iv_selectors(name: str) -> tuple[str, ...]:
    return _ARXIV_AR5IV_SELECTORS.get(name, ())


def _arxiv_select(node: Any, selector_group: str) -> list[Any]:
    if Tag is None or not isinstance(node, Tag):
        return []
    matches: list[Any] = []
    for selector in _arxiv_ar5iv_selectors(selector_group):
        matches.extend(node.select(selector))
    return matches


def _arxiv_select_one(node: Any, selector_group: str) -> Any:
    if Tag is None or not isinstance(node, Tag):
        return None
    for selector in _arxiv_ar5iv_selectors(selector_group):
        match = node.select_one(selector)
        if isinstance(match, Tag):
            return match
    return None


def _arxiv_author_boundary_start(text: str) -> int | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    matches = [
        match
        for match in (
            _ARXIV_AUTHOR_INSTITUTION_BOUNDARY_PATTERN.search(normalized),
            _ARXIV_AUTHOR_COUNTRY_BOUNDARY_PATTERN.search(normalized),
            _ARXIV_AUTHOR_ADDRESS_BOUNDARY_PATTERN.search(normalized),
            _ARXIV_AUTHOR_COUNTRY_CODE_BOUNDARY_PATTERN.search(normalized),
        )
        if match is not None
    ]
    if not matches:
        return None
    return min(match.start() for match in matches)


def _arxiv_author_text_has_boundary(text: str) -> bool:
    return _arxiv_author_boundary_start(text) is not None


def _trim_arxiv_author_text_at_boundary(text: str) -> str:
    boundary_start = _arxiv_author_boundary_start(text)
    if boundary_start is None:
        return normalize_text(text)
    return normalize_text(text[:boundary_start])


def _first_header_value(
    headers: Mapping[str, Any] | None, key: str, default: str = ""
) -> str:
    lowered = key.lower()
    for raw_key, value in (headers or {}).items():
        if str(raw_key).lower() == lowered:
            return str(value or default)
    return default


def _dedupe_strings(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for raw_value in values:
        value = normalize_text(raw_value)
        if value and value not in result:
            result.append(value)
    return result


def _datetime_to_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    normalized = normalize_text(value)
    return normalized or None


def _result_short_id(result: Any) -> str:
    get_short_id = getattr(result, "get_short_id", None)
    if callable(get_short_id):
        return normalize_arxiv_id(str(get_short_id()))
    entry_id = normalize_text(getattr(result, "entry_id", ""))
    return arxiv_id_from_query(entry_id)


def _result_authors(result: Any) -> list[str]:
    authors: list[str] = []
    for author in list(getattr(result, "authors", []) or []):
        name = normalize_text(getattr(author, "name", author))
        if name and name not in authors:
            authors.append(name)
    return authors


def metadata_from_arxiv_result(
    result: Any, *, requested_arxiv_id: str | None = None
) -> ProviderMetadata:
    arxiv_id = _result_short_id(result) or normalize_arxiv_id(requested_arxiv_id)
    if not arxiv_id:
        raise ProviderFailure(
            NO_RESULT, "arXiv API result did not include a usable arXiv ID."
        )
    pdf_url = normalize_text(getattr(result, "pdf_url", "")) or canonical_arxiv_pdf_url(
        arxiv_id
    )
    categories = [
        normalize_text(item)
        for item in list(getattr(result, "categories", []) or [])
        if normalize_text(item)
    ]
    external_doi = normalize_text(getattr(result, "doi", ""))
    metadata: dict[str, Any] = {
        "provider": "arxiv",
        "official_provider": True,
        "doi": canonical_arxiv_doi(arxiv_id),
        "external_doi": external_doi or None,
        "title": normalize_text(getattr(result, "title", "")) or None,
        "authors": _result_authors(result),
        "abstract": normalize_text(getattr(result, "summary", "")) or None,
        "published": _datetime_to_date(getattr(result, "published", None)),
        "updated": _datetime_to_date(getattr(result, "updated", None)),
        "journal_title": "arXiv",
        "publisher": "arXiv",
        "landing_page_url": canonical_arxiv_abs_url(arxiv_id),
        "arxiv_id": arxiv_id,
        "primary_category": normalize_text(getattr(result, "primary_category", ""))
        or None,
        "categories": categories,
        "keywords": categories,
        "license_urls": [],
        "references": [],
        "pdf_url": pdf_url,
        "html_url": canonical_arxiv_html_url(arxiv_id),
        "fulltext_links": [
            {
                "url": canonical_arxiv_html_url(arxiv_id),
                "content_type": "text/html",
                "content_version": arxiv_id,
                "intended_application": "full_text",
            },
            {
                "url": pdf_url,
                "content_type": PDF_MIME_TYPE,
                "content_version": arxiv_id,
                "intended_application": "full_text",
            },
        ],
    }
    return metadata


def _arxiv_id_from_metadata_or_doi(doi: str | None, metadata: Mapping[str, Any]) -> str:
    return (
        normalize_arxiv_id(str(metadata.get("arxiv_id") or ""))
        or arxiv_id_from_doi(str(metadata.get("doi") or ""))
        or arxiv_id_from_doi(doi)
        or arxiv_id_from_query(str(metadata.get("landing_page_url") or ""))
        or arxiv_id_from_query(str(metadata.get("html_url") or ""))
        or arxiv_id_from_query(str(metadata.get("pdf_url") or ""))
        or arxiv_id_from_query(str(metadata.get("url") or ""))
        or arxiv_id_from_query(str(metadata.get("entry_id") or ""))
    )


def _default_arxiv_fulltext_links(arxiv_id: str, pdf_url: str) -> list[dict[str, Any]]:
    return [
        {
            "url": canonical_arxiv_html_url(arxiv_id),
            "content_type": "text/html",
            "content_version": arxiv_id,
            "intended_application": "full_text",
        },
        {
            "url": pdf_url,
            "content_type": PDF_MIME_TYPE,
            "content_version": arxiv_id,
            "intended_application": "full_text",
        },
    ]


def _minimal_arxiv_metadata(
    arxiv_id: str,
    *,
    doi: str | None,
    metadata: Mapping[str, Any],
) -> ProviderMetadata:
    pdf_url = canonical_arxiv_pdf_url(arxiv_id)
    merged: dict[str, Any] = dict(metadata or {})
    merged.pop("source_url", None)
    merged.update(
        {
            "provider": "arxiv",
            "official_provider": True,
            "doi": canonical_arxiv_doi(arxiv_id)
            or normalize_text(doi)
            or normalize_text(metadata.get("doi"))
            or None,
            "journal_title": normalize_text(metadata.get("journal_title")) or "arXiv",
            "publisher": normalize_text(metadata.get("publisher")) or "arXiv",
            "landing_page_url": canonical_arxiv_abs_url(arxiv_id),
            "arxiv_id": arxiv_id,
            "pdf_url": pdf_url,
            "html_url": canonical_arxiv_html_url(arxiv_id),
        }
    )
    merged.setdefault("title", normalize_text(metadata.get("title")) or None)
    merged.setdefault("abstract", normalize_text(metadata.get("abstract")) or None)
    merged.setdefault("authors", list(metadata.get("authors") or []))
    merged.setdefault("keywords", list(metadata.get("keywords") or []))
    merged.setdefault("license_urls", list(metadata.get("license_urls") or []))
    merged.setdefault("references", list(metadata.get("references") or []))
    merged["fulltext_links"] = _default_arxiv_fulltext_links(arxiv_id, pdf_url)
    return merged


def minimal_arxiv_metadata(
    arxiv_id: str,
    *,
    doi: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderMetadata:
    """Build route metadata that is sufficient to fetch arXiv HTML/PDF."""

    normalized = normalize_arxiv_id(arxiv_id)
    if not normalized:
        raise ProviderFailure(NOT_SUPPORTED, "A valid arXiv ID is required.")
    return _minimal_arxiv_metadata(normalized, doi=doi, metadata=metadata or {})


def arxiv_metadata_probe_short_circuit(doi: str) -> ProviderMetadata | None:
    arxiv_id = arxiv_id_from_doi(doi)
    if not arxiv_id:
        return None
    return minimal_arxiv_metadata(arxiv_id, doi=doi, metadata={})


register_metadata_probe_short_circuit("arxiv", arxiv_metadata_probe_short_circuit)


def _clean_arxiv_frontmatter_text(node: Any, *, remove_line_breaks: bool = True) -> str:
    if BeautifulSoup is None or Tag is None or not isinstance(node, Tag):
        return ""
    clone_soup = BeautifulSoup(str(node), "html.parser")
    clone = clone_soup.find()
    if not isinstance(clone, Tag):
        return ""
    for selector in _arxiv_ar5iv_selectors("frontmatter_noise"):
        for match in clone.select(selector):
            match.decompose()
    separator = " " if remove_line_breaks else "\n"
    text = render_clean_text_from_html(clone).replace("\u200b", " ")
    text = text.replace("\u2005", " ").replace("\u200a", " ").replace("\u2003", " ")
    text = re.sub(r"\s*\n\s*", separator, text)
    return normalize_text(text)


def _arxiv_date_to_iso(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            continue
    return normalized


def _extract_arxiv_watermark_metadata(root: Any) -> dict[str, Any]:
    if Tag is None or not isinstance(root, Tag):
        return {}
    candidates = []
    for selector in _arxiv_ar5iv_selectors("watermark"):
        candidates.extend(root.select(selector))
    candidates.append(root)
    for node in candidates:
        text = normalize_text(
            node.get_text(" ", strip=True) if isinstance(node, Tag) else ""
        )
        match = _ARXIV_WATERMARK_PATTERN.search(text)
        if match is None:
            continue
        arxiv_id = normalize_arxiv_id(match.group("arxiv_id"))
        if not arxiv_id:
            continue
        primary_category = normalize_text(match.group("category"))
        return {
            "arxiv_id": arxiv_id,
            "primary_category": primary_category or None,
            "published": _arxiv_date_to_iso(match.group("date")),
        }
    return {}


def _candidate_arxiv_author_text_from_person_node(node: Any) -> str:
    if BeautifulSoup is None or Tag is None or not isinstance(node, Tag):
        return ""
    clone_soup = BeautifulSoup(str(node), "html.parser")
    clone = clone_soup.find()
    if not isinstance(clone, Tag):
        return ""
    for selector in _arxiv_ar5iv_selectors("frontmatter_noise"):
        for match in clone.select(selector):
            match.decompose()

    pieces: list[str] = []
    for child in list(clone.children):
        if Tag is not None and isinstance(child, Tag):
            child_name = normalize_text(child.name or "").lower()
            if child_name == "sup":
                break
            child_text = normalize_text(child.get_text(" ", strip=True))
            if child_name == "br":
                pieces.append(";")
                continue
            if pieces and _arxiv_author_text_has_boundary(child_text):
                break
            if child_text:
                pieces.append(child_text)
        else:
            child_text = normalize_text(str(child))
            if pieces and _arxiv_author_text_has_boundary(child_text):
                break
            if child_text:
                pieces.append(child_text)

    text = normalize_text(" ".join(pieces).replace("\u200b", " "))
    text = _ARXIV_EMAIL_PATTERN.sub(" ", text)
    text = _ARXIV_ORCID_PATTERN.sub(" ", text)
    text = _trim_arxiv_author_text_at_boundary(text)
    text = re.sub(r"\s*;\s*", " ; ", text)
    return normalize_text(text)


def _looks_like_arxiv_author_name(text: str) -> bool:
    normalized = normalize_text(text).strip(" ,;")
    if not normalized or "@" in normalized:
        return False
    if _arxiv_author_text_has_boundary(normalized):
        return False
    tokens = [token for token in normalized.split() if token]
    if not tokens or len(tokens) > 6:
        return False
    return any(any(character.isalpha() for character in token) for token in tokens)


def _split_compact_arxiv_author_sequence(text: str) -> list[str]:
    normalized = normalize_text(re.sub(r"\b\d+(?:\s*,\s*\d+)*\b", " ", text))
    tokens = [token.strip(" ,;") for token in normalized.split() if token.strip(" ,;")]
    if len(tokens) <= 4:
        return [normalized] if _looks_like_arxiv_author_name(normalized) else []
    authors: list[str] = []
    current: list[str] = []
    substantive_tokens = 0
    for token in tokens:
        current.append(token)
        if not _ARXIV_INITIAL_TOKEN_PATTERN.fullmatch(token):
            substantive_tokens += 1
        if substantive_tokens >= 2:
            candidate = normalize_text(" ".join(current))
            if _looks_like_arxiv_author_name(candidate):
                authors.append(candidate)
            current = []
            substantive_tokens = 0
    remainder = normalize_text(" ".join(current))
    if remainder and authors:
        authors[-1] = normalize_text(f"{authors[-1]} {remainder}")
    elif remainder and _looks_like_arxiv_author_name(remainder):
        authors.append(remainder)
    return authors


def _split_arxiv_author_text(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    labeled_authors = [
        normalize_text(match.group("name").strip(" ,;"))
        for match in _ARXIV_AUTHOR_LABEL_PATTERN.finditer(normalized)
        if _looks_like_arxiv_author_name(match.group("name"))
    ]
    if len(labeled_authors) >= 2:
        return dedupe_authors(labeled_authors)

    parts = [
        normalize_text(part.strip(" ,;"))
        for part in re.split(r"\s+(?:and|&)\s+|;|\n", normalized)
        if normalize_text(part.strip(" ,;"))
    ]
    authors: list[str] = []
    for part in parts or [normalized]:
        if _looks_like_arxiv_author_name(part) and len(part.split()) <= 4:
            authors.append(part)
            continue
        authors.extend(_split_compact_arxiv_author_sequence(part))
    return dedupe_authors(authors)


def _extract_arxiv_html_authors(article: Any) -> list[str]:
    if Tag is None or not isinstance(article, Tag):
        return []
    creators = [
        node
        for node in _arxiv_select(article, "author_creators")
        if isinstance(node, Tag)
    ]
    if len(creators) > 1:
        authors: list[str] = []
        for creator in creators:
            person_node = _arxiv_select_one(creator, "author_person_names") or creator
            candidate = _clean_arxiv_frontmatter_text(person_node)
            if _looks_like_arxiv_author_name(candidate):
                authors.append(candidate)
        if authors:
            return dedupe_authors(authors)

    authors = []
    for person_node in _arxiv_select(article, "author_person_names"):
        candidate_text = _candidate_arxiv_author_text_from_person_node(person_node)
        authors.extend(_split_arxiv_author_text(candidate_text))
    return dedupe_authors(authors)


def _arxiv_node_identity_text(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    attrs = getattr(node, "attrs", None) or {}
    parts = [normalize_text(getattr(node, "name", "") or "")]
    for key in ("id", "aria-label", "aria-labelledby", "data-title"):
        parts.append(normalize_text(str(attrs.get(key) or "")))
    class_values = attrs.get("class")
    if isinstance(class_values, (list, tuple, set)):
        parts.extend(normalize_text(str(item)) for item in class_values)
    else:
        parts.append(normalize_text(str(class_values or "")))
    return " ".join(part.lower() for part in parts if part)


def _select_arxiv_title_node(article: Any) -> Any:
    title_node = _arxiv_select_one(article, "document_title")
    if isinstance(title_node, Tag):
        return title_node
    return article.find("h1") if Tag is not None and isinstance(article, Tag) else None


def _select_arxiv_abstract_node(article: Any) -> Any:
    abstract_node = _arxiv_select_one(article, "abstract")
    if isinstance(abstract_node, Tag):
        return abstract_node
    if Tag is None or not isinstance(article, Tag):
        return None
    for candidate in article.find_all(["section", "div"]):
        if not isinstance(candidate, Tag):
            continue
        identity = _arxiv_node_identity_text(candidate)
        heading_node = candidate.find(SECTION_HEADING_PATTERN)
        title = normalize_text(
            render_heading_text_from_html(heading_node)
            if isinstance(heading_node, Tag)
            else ""
        ).lower()
        if "abstract" in identity or title.strip(" .:") == "abstract":
            return candidate
    return None


def _extract_arxiv_html_frontmatter(
    soup: Any,
    article: Any,
    source_url: str,
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if Tag is None or not isinstance(article, Tag):
        return {}
    arxiv_id = _arxiv_id_from_metadata_or_doi(
        str(metadata.get("doi") or ""), metadata
    ) or arxiv_id_from_query(source_url)
    watermark_metadata = _extract_arxiv_watermark_metadata(soup)
    arxiv_id = normalize_arxiv_id(watermark_metadata.get("arxiv_id")) or arxiv_id

    title_node = _select_arxiv_title_node(article)
    title = (
        _clean_arxiv_frontmatter_text(title_node) if isinstance(title_node, Tag) else ""
    )
    abstract_node = _select_arxiv_abstract_node(article)
    abstract = ""
    if isinstance(abstract_node, Tag):
        abstract_soup = BeautifulSoup(str(abstract_node), "html.parser")
        abstract_clone = abstract_soup.find()
        if isinstance(abstract_clone, Tag):
            for heading_selector in _arxiv_ar5iv_selectors("abstract_heading"):
                for heading in abstract_clone.select(heading_selector):
                    heading.decompose()
            for heading in abstract_clone.find_all(SECTION_HEADING_PATTERN):
                heading.decompose()
            abstract = _clean_arxiv_frontmatter_text(abstract_clone)

    html_metadata: dict[str, Any] = {
        "provider": "arxiv",
        "official_provider": True,
        "journal_title": "arXiv",
        "publisher": "arXiv",
    }
    if arxiv_id:
        html_metadata.update(
            {
                "doi": canonical_arxiv_doi(arxiv_id),
                "arxiv_id": arxiv_id,
                "landing_page_url": canonical_arxiv_abs_url(arxiv_id),
                "html_url": canonical_arxiv_html_url(arxiv_id),
                "pdf_url": canonical_arxiv_pdf_url(arxiv_id),
                "fulltext_links": _default_arxiv_fulltext_links(
                    arxiv_id, canonical_arxiv_pdf_url(arxiv_id)
                ),
            }
        )
    if title:
        html_metadata["title"] = title
    authors = _extract_arxiv_html_authors(article)
    if authors:
        html_metadata["authors"] = authors
    if abstract:
        html_metadata["abstract"] = abstract
    if watermark_metadata.get("published"):
        html_metadata["published"] = watermark_metadata["published"]
    if watermark_metadata.get("primary_category"):
        html_metadata["primary_category"] = watermark_metadata["primary_category"]
        html_metadata["categories"] = [watermark_metadata["primary_category"]]
        html_metadata["keywords"] = [watermark_metadata["primary_category"]]
    html_metadata.pop("source_url", None)
    return html_metadata


def _merge_arxiv_metadata_layers(
    derived_metadata: Mapping[str, Any],
    *,
    html_metadata: Mapping[str, Any] | None = None,
    api_metadata: Mapping[str, Any] | None = None,
    references: Sequence[Mapping[str, Any]] | None = None,
) -> ProviderMetadata:
    merged: dict[str, Any] = dict(derived_metadata or {})
    merged.pop("source_url", None)

    def apply_layer(layer: Mapping[str, Any] | None, *, replace_lists: bool) -> None:
        if not isinstance(layer, Mapping):
            return
        for key, value in layer.items():
            if key == "source_url" or value in (None, "", []):
                continue
            if key in {
                "authors",
                "keywords",
                "license_urls",
                "fulltext_links",
                "categories",
            }:
                if replace_lists or not merged.get(key):
                    merged[key] = (
                        list(value or []) if isinstance(value, list) else [value]
                    )
                else:
                    merged[key] = list(merged.get(key) or []) + list(value or [])
                continue
            if key == "references":
                continue
            merged[key] = value

    apply_layer(html_metadata, replace_lists=False)
    apply_layer(api_metadata, replace_lists=True)

    arxiv_id = normalize_arxiv_id(str(merged.get("arxiv_id") or ""))
    if arxiv_id:
        merged["doi"] = canonical_arxiv_doi(arxiv_id)
        merged["landing_page_url"] = canonical_arxiv_abs_url(arxiv_id)
        merged["html_url"] = canonical_arxiv_html_url(arxiv_id)
        merged["pdf_url"] = normalize_text(
            merged.get("pdf_url")
        ) or canonical_arxiv_pdf_url(arxiv_id)
        merged["fulltext_links"] = _default_arxiv_fulltext_links(
            arxiv_id, normalize_text(merged.get("pdf_url"))
        )
    merged["provider"] = "arxiv"
    merged["official_provider"] = True
    merged["journal_title"] = normalize_text(merged.get("journal_title")) or "arXiv"
    merged["publisher"] = normalize_text(merged.get("publisher")) or "arXiv"
    merged["authors"] = dedupe_authors(
        [str(item) for item in (merged.get("authors") or [])]
    )
    merged["keywords"] = _dedupe_strings(
        str(item) for item in (merged.get("keywords") or [])
    )
    merged["license_urls"] = _dedupe_strings(
        str(item) for item in (merged.get("license_urls") or [])
    )
    if references:
        merged["references"] = [dict(item) for item in references]
    else:
        merged["references"] = list(merged.get("references") or [])
    merged.pop("source_url", None)
    return merged


def _arxiv_asset_download_concurrency(env: Mapping[str, str] | None) -> int:
    return min(
        resolve_asset_download_concurrency(env), ARXIV_ASSET_DOWNLOAD_CONCURRENCY_LIMIT
    )


def _asset_candidate_urls(asset: Mapping[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            normalize_text(str(asset.get(field) or ""))
            for field in (
                "url",
                "full_size_url",
                "preview_url",
                "download_url",
                "original_url",
                "link",
            )
        )
        if normalized
    }


def _is_retryable_arxiv_asset_failure(failure: Mapping[str, Any]) -> bool:
    if failure.get("status") is not None:
        return False
    error_category = normalize_text(str(failure.get("error_category") or "")).lower()
    if error_category:
        return error_category in _ARXIV_RETRYABLE_ASSET_ERROR_CATEGORIES
    reason = normalize_text(str(failure.get("reason") or "")).lower()
    if not reason or "unsupported asset url scheme" in reason:
        return False
    return any(token in reason for token in NETWORK_RETRYABLE_REASON_TOKENS)


def _asset_matches_failure(
    asset: Mapping[str, Any], failure: Mapping[str, Any]
) -> bool:
    failure_url = normalize_text(
        str(failure.get("source_url") or failure.get("url") or "")
    )
    if failure_url and failure_url in _asset_candidate_urls(asset):
        return True
    failure_heading = normalize_text(str(failure.get("heading") or ""))
    asset_heading = normalize_text(str(asset.get("heading") or ""))
    failure_caption = normalize_text(str(failure.get("caption") or ""))
    asset_caption = normalize_text(str(asset.get("caption") or ""))
    return bool(
        failure_heading
        and failure_heading == asset_heading
        and failure_caption == asset_caption
    )


def _assets_for_arxiv_network_retry(
    assets: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    retry_assets: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str, str]] = set()
    retry_failures = [
        failure for failure in failures if _is_retryable_arxiv_asset_failure(failure)
    ]
    for failure in retry_failures:
        for asset in assets:
            if not _asset_matches_failure(asset, failure):
                continue
            identity = (
                tuple(sorted(_asset_candidate_urls(asset))),
                normalize_text(str(asset.get("heading") or "")),
                normalize_text(str(asset.get("caption") or "")),
            )
            if identity not in seen:
                seen.add(identity)
                retry_assets.append(dict(asset))
            break
    return retry_assets


def _merge_arxiv_asset_download_results(
    initial_result: Mapping[str, list[dict[str, Any]]],
    retry_result: Mapping[str, list[dict[str, Any]]],
    *,
    retried_assets: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    initial_assets = [dict(item) for item in (initial_result.get("assets") or [])]
    retry_assets = [dict(item) for item in (retry_result.get("assets") or [])]
    retry_failures = [dict(item) for item in (retry_result.get("asset_failures") or [])]
    retained_initial_failures: list[dict[str, Any]] = []
    for failure in initial_result.get("asset_failures") or []:
        if _is_retryable_arxiv_asset_failure(failure) and any(
            _asset_matches_failure(asset, failure) for asset in retried_assets
        ):
            continue
        retained_initial_failures.append(dict(failure))
    return {
        "assets": [*initial_assets, *retry_assets],
        "asset_failures": [*retained_initial_failures, *retry_failures],
    }


def _looks_like_html(content_type: str | None, body: bytes) -> bool:
    normalized = normalize_text(content_type).lower()
    if "html" in normalized or "xhtml" in normalized:
        return True
    prefix = body[:512].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html"))


def _markdown_word_count(markdown_text: str) -> int:
    return len(_WORD_PATTERN.findall(normalize_text(markdown_text)))


def _extract_reference_doi(node: Any) -> str | None:
    if Tag is None or not isinstance(node, Tag):
        return None
    for anchor in node.find_all("a", href=True):
        href = normalize_text(str(anchor.get("href") or ""))
        match = _reference_doi_match(href)
        if match is not None:
            return normalize_text(match.group(0).rstrip(").,;"))
    text = normalize_text(node.get_text(" ", strip=True))
    match = _reference_doi_match(text)
    if match is None:
        return None
    return normalize_text(match.group(0).rstrip(").,;"))


def _reference_doi_match(value: str) -> re.Match[str] | None:
    for match in DOI_PATTERN.finditer(value):
        if match.start() == 0 or not value[match.start() - 1].isalnum():
            return match
    return None


def _extract_reference_year(text: str, node: Any) -> str | None:
    if Tag is not None and isinstance(node, Tag):
        year_node = _arxiv_select_one(node, "reference_year")
        year_text = normalize_text(
            year_node.get_text(" ", strip=True) if isinstance(year_node, Tag) else ""
        )
        year_match = _REFERENCE_YEAR_PATTERN.search(year_text)
        if year_match is not None:
            return year_match.group(1)
    matches = list(_REFERENCE_YEAR_PATTERN.finditer(text))
    return matches[-1].group(1) if matches else None


def _extract_reference_title(node: Any) -> str | None:
    if Tag is None or not isinstance(node, Tag):
        return None
    title_node = _arxiv_select_one(node, "reference_title")
    title = normalize_text(
        title_node.get_text(" ", strip=True) if isinstance(title_node, Tag) else ""
    )
    return title or None


def _clean_arxiv_reference_node(node: Any) -> Any:
    if BeautifulSoup is None or Tag is None or not isinstance(node, Tag):
        return None
    clone_soup = BeautifulSoup(str(node), "html.parser")
    clone = clone_soup.find()
    if not isinstance(clone, Tag):
        return None

    for selector in _arxiv_ar5iv_selectors("reference_noise"):
        for match in clone.select(selector):
            match.decompose()
    for block in _arxiv_select(clone, "reference_blocks"):
        block_text = normalize_text(block.get_text(" ", strip=True)).lower()
        if _arxiv_select_one(block, "reference_links") is not None:
            block.decompose()
            continue
        if block_text.startswith(("external links", "cited by")):
            block.decompose()
    return clone


def _normalize_reference_text(text: str) -> str:
    normalized = normalize_text(text)
    normalized = re.sub(r"\s+([,.;:)])", r"\1", normalized)
    normalized = re.sub(r"([(])\s+", r"\1", normalized)
    return normalize_text(normalized)


def _arxiv_reference_text(node: Any) -> str:
    clone = _clean_arxiv_reference_node(node)
    if Tag is None or not isinstance(clone, Tag):
        return ""
    return _normalize_reference_text(clone.get_text(" ", strip=True))


def _candidate_arxiv_bibliography_containers(root: Any) -> list[Any]:
    if Tag is None or not isinstance(root, Tag):
        return []
    containers: list[Any] = []
    seen: set[int] = set()
    for selector in _arxiv_ar5iv_selectors("bibliography_containers"):
        for container in root.select(selector):
            if isinstance(container, Tag) and id(container) not in seen:
                seen.add(id(container))
                containers.append(container)
    if containers:
        return containers
    for candidate in root.find_all(["section", "div"]):
        if not isinstance(candidate, Tag):
            continue
        heading = candidate.find(SECTION_HEADING_PATTERN)
        if not isinstance(heading, Tag):
            continue
        title = (
            normalize_text(render_heading_text_from_html(heading)).lower().strip(" .:")
        )
        if title in {"references", "bibliography"} and id(candidate) not in seen:
            seen.add(id(candidate))
            containers.append(candidate)
    return containers


def _candidate_arxiv_bibitems(root: Any) -> list[Any]:
    if Tag is None or not isinstance(root, Tag):
        return []
    containers = _candidate_arxiv_bibliography_containers(root)
    scopes = containers or [root]

    items: list[Any] = []
    seen_items: set[int] = set()
    for scope in scopes:
        for selector in _arxiv_ar5iv_selectors("bibliography_items"):
            for item in scope.select(selector):
                if isinstance(item, Tag) and id(item) not in seen_items:
                    seen_items.add(id(item))
                    items.append(item)
        if items:
            continue
        for item in scope.find_all("li"):
            if isinstance(item, Tag) and id(item) not in seen_items:
                seen_items.add(id(item))
                items.append(item)
    return items


def _extract_arxiv_html_references(root: Any) -> list[dict[str, str | None]]:
    references: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for node in _candidate_arxiv_bibitems(root):
        raw = _arxiv_reference_text(node)
        if not raw or raw in seen:
            continue
        seen.add(raw)
        references.append(
            {
                "raw": raw,
                "doi": _extract_reference_doi(node),
                "title": _extract_reference_title(node),
                "year": _extract_reference_year(raw, node),
            }
        )
    return references


def _asset_has_download_candidate(asset: Mapping[str, Any]) -> bool:
    return bool(
        normalize_text(
            str(
                asset.get("url")
                or asset.get("full_size_url")
                or asset.get("preview_url")
                or asset.get("download_url")
                or asset.get("original_url")
                or asset.get("link")
                or ""
            )
        )
    )


def _extract_arxiv_html_assets(
    article_html: str, source_url: str
) -> list[dict[str, Any]]:
    assets = [
        _postprocess_arxiv_html_asset(item)
        for item in html_assets.extract_figure_assets(article_html, source_url)
        if normalize_text(str(item.get("kind") or "")).lower() == "figure"
        and _asset_has_download_candidate(item)
    ]
    return [dict(item) for item in merge_assets_by_identity(assets)]


def _arxiv_figure_label_from_text(text: str) -> str:
    normalized = normalize_text(str(text or "").replace("\n", " "))
    match = _ARXIV_FIGURE_CAPTION_LABEL_PATTERN.match(normalized)
    if match is None:
        return ""
    raw_label = normalize_text(match.group("label"))
    number_match = re.search(r"(\d+[A-Za-z]?)$", raw_label)
    if number_match is None:
        return raw_label.rstrip(".:")
    if raw_label.lower().startswith(EXTENDED_DATA_FIGURE_LABEL.lower()):
        return f"{EXTENDED_DATA_FIGURE_LABEL}. {number_match.group(1)}"
    return f"Figure {number_match.group(1)}"


def _arxiv_figure_label_from_dom_id(dom_id: Any) -> str:
    normalized = normalize_text(str(dom_id or ""))
    match = _ARXIV_FIGURE_ID_PATTERN.search(normalized)
    if match is None:
        return ""
    return f"Figure {match.group('number')}"


def _clean_arxiv_asset_caption(text: Any) -> str:
    return html_assets.clean_noisy_image_alt_text(str(text or "").replace("\n", " "))


def _postprocess_arxiv_html_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(asset)
    caption = _clean_arxiv_asset_caption(result.get("caption"))
    heading = _clean_arxiv_asset_caption(result.get("heading")) or "Figure"
    short_heading = _arxiv_figure_label_from_text(
        caption
    ) or _arxiv_figure_label_from_text(heading)
    if not short_heading:
        short_heading = _arxiv_figure_label_from_dom_id(
            result.get("dom_id")
        ) or _arxiv_figure_label_from_dom_id(result.get("image_id"))
    result["heading"] = short_heading or heading
    result["caption"] = caption
    return result


def _arxiv_node_classes(node: Any) -> set[str]:
    if Tag is None or not isinstance(node, Tag):
        return set()
    raw_classes = (getattr(node, "attrs", None) or {}).get("class") or []
    if isinstance(raw_classes, str):
        return {
            normalize_text(item).lower()
            for item in raw_classes.split()
            if normalize_text(item)
        }
    return {
        normalize_text(str(item)).lower()
        for item in raw_classes
        if normalize_text(str(item))
    }


def _arxiv_node_has_class(node: Any, class_name: str) -> bool:
    return normalize_text(class_name).lower() in _arxiv_node_classes(node)


def _is_arxiv_table_figure(node: Any) -> bool:
    return (
        Tag is not None
        and isinstance(node, Tag)
        and node.name == "figure"
        and _arxiv_node_has_class(node, "ltx_table")
        and node.find("table") is not None
    )


def _is_arxiv_tabular_table(node: Any) -> bool:
    return (
        Tag is not None
        and isinstance(node, Tag)
        and node.name == "table"
        and _arxiv_node_has_class(node, "ltx_tabular")
    )


def _is_arxiv_listing_node(node: Any) -> bool:
    return (
        Tag is not None
        and isinstance(node, Tag)
        and node.name == "div"
        and _arxiv_node_has_class(node, "ltx_listing")
    )


def _is_arxiv_algorithm_figure(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag) or node.name != "figure":
        return False
    classes = _arxiv_node_classes(node)
    if "ltx_algorithm" not in classes and "ltx_float" not in classes:
        return False
    return _arxiv_select_one(node, "algorithm_listing") is not None


def _is_arxiv_inline_figure_container(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag) or node.name != "figure":
        return False
    classes = _arxiv_node_classes(node)
    if classes.intersection(
        {"ltx_table", "ltx_algorithm", "ltx_equation", "ltx_listing"}
    ):
        return False
    return not _is_arxiv_table_figure(node) and not _is_arxiv_algorithm_figure(node)


def _arxiv_parent_figures(node: Any, article: Any) -> list[Any]:
    figures: list[Any] = []
    current = getattr(node, "parent", None)
    while Tag is not None and isinstance(current, Tag) and current is not article:
        if current.name == "figure":
            figures.append(current)
        current = getattr(current, "parent", None)
    return figures


def _arxiv_inline_figure_for_image(image: Any, article: Any) -> Any:
    if Tag is None or not isinstance(image, Tag):
        return None
    figures = _arxiv_parent_figures(image, article)
    if not figures:
        return None
    if any(not _is_arxiv_inline_figure_container(figure) for figure in figures):
        return None
    return figures[0]


def _arxiv_srcset_url_candidates(raw_value: Any) -> list[str]:
    raw = normalize_text(str(raw_value or ""))
    if not raw:
        return []
    candidates: list[str] = []
    for item in raw.split(","):
        candidate = normalize_text(item).split(" ", 1)[0]
        if candidate:
            candidates.append(candidate)
    return candidates


def _arxiv_url_reference_candidates(raw_value: Any, source_url: str = "") -> set[str]:
    raw = normalize_text(str(raw_value or "")).strip("<>").replace("\\", "/")
    if not raw:
        return set()
    values = [raw]
    if source_url:
        values.append(urllib.parse.urljoin(source_url, raw))
    candidates: set[str] = set()
    for value in values:
        normalized = normalize_text(value).strip("<>").replace("\\", "/")
        if not normalized:
            continue
        parsed = urllib.parse.urlsplit(normalized)
        path = parsed.path or normalized
        for candidate in (
            normalized,
            urllib.parse.unquote(normalized),
            path,
            urllib.parse.unquote(path),
        ):
            cleaned = normalize_text(candidate).replace("\\", "/").strip()
            if not cleaned:
                continue
            candidates.add(cleaned)
            candidates.add(cleaned.lstrip("/"))
            basename = cleaned.rstrip("/").rsplit("/", 1)[-1]
            if basename:
                candidates.add(basename)
    return candidates


def _arxiv_url_candidate_sets_match(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    for left_item in left:
        for right_item in right:
            if left_item.endswith(f"/{right_item}") or right_item.endswith(
                f"/{left_item}"
            ):
                return True
    return False


def _arxiv_image_url_candidates(image: Any, source_url: str) -> set[str]:
    if Tag is None or not isinstance(image, Tag):
        return set()
    candidates: set[str] = set()
    for attr in ("src", "data-src", "data-lazy-src"):
        candidates |= _arxiv_url_reference_candidates(image.get(attr), source_url)
    for attr in ("srcset", "data-srcset"):
        for srcset_url in _arxiv_srcset_url_candidates(image.get(attr)):
            candidates |= _arxiv_url_reference_candidates(srcset_url, source_url)

    picture = image.find_parent("picture")
    if isinstance(picture, Tag):
        for source in picture.find_all("source"):
            if not isinstance(source, Tag):
                continue
            for attr in ("src", "data-src"):
                candidates |= _arxiv_url_reference_candidates(
                    source.get(attr), source_url
                )
            for attr in ("srcset", "data-srcset"):
                for srcset_url in _arxiv_srcset_url_candidates(source.get(attr)):
                    candidates |= _arxiv_url_reference_candidates(
                        srcset_url, source_url
                    )

    anchor = image.find_parent("a", href=True)
    if isinstance(anchor, Tag):
        candidates |= _arxiv_url_reference_candidates(anchor.get("href"), source_url)
    return candidates


def _arxiv_inline_asset_url(asset: Mapping[str, Any]) -> str:
    for field in (
        "url",
        "full_size_url",
        "preview_url",
        "download_url",
        "original_url",
        "link",
    ):
        candidate = normalize_text(str(asset.get(field) or ""))
        if candidate:
            return candidate
    return ""


def _arxiv_inline_asset_alt(asset: Mapping[str, Any]) -> str:
    return (
        normalize_text(str(asset.get("heading") or ""))
        or _arxiv_figure_label_from_dom_id(asset.get("image_id"))
        or _arxiv_figure_label_from_dom_id(asset.get("dom_id"))
        or "Figure"
    )


def _arxiv_asset_order(asset: Mapping[str, Any]) -> int | None:
    raw_value = normalize_text(str(asset.get("asset_order") or ""))
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return value if value >= 0 else None


def _arxiv_inline_images_for_figure(figure: Any, article: Any) -> list[Any]:
    if Tag is None or not isinstance(figure, Tag):
        return []
    images: list[Any] = []
    for image in figure.find_all("img"):
        if not isinstance(image, Tag):
            continue
        if _arxiv_inline_figure_for_image(image, article) is None:
            continue
        if figure not in _arxiv_parent_figures(image, article):
            continue
        images.append(image)
    return images


def _annotate_arxiv_inline_figure_images(
    article: Any,
    extracted_assets: Sequence[Mapping[str, Any]],
    source_url: str,
) -> dict[str, int]:
    if Tag is None or not isinstance(article, Tag):
        return {
            "inline_figure_image_count": 0,
            "inline_figure_asset_match_count": 0,
            "inline_figure_asset_miss_count": len(extracted_assets),
        }

    figure_by_id: dict[str, Any] = {}
    for figure in article.find_all("figure"):
        if not _is_arxiv_inline_figure_container(figure):
            continue
        figure_id = normalize_text(str(figure.get("id") or ""))
        if figure_id and figure_id not in figure_by_id:
            figure_by_id[figure_id] = figure

    eligible_images: list[Any] = []
    image_by_id: dict[str, Any] = {}
    image_url_candidates: dict[int, set[str]] = {}
    for image in article.find_all("img"):
        if (
            not isinstance(image, Tag)
            or _arxiv_inline_figure_for_image(image, article) is None
        ):
            continue
        eligible_images.append(image)
        image_id = normalize_text(str(image.get("id") or ""))
        if image_id and image_id not in image_by_id:
            image_by_id[image_id] = image
        image_url_candidates[id(image)] = _arxiv_image_url_candidates(image, source_url)

    consumed_image_ids: set[int] = set()
    match_count = 0
    miss_count = 0

    for asset in extracted_assets:
        inline_url = _arxiv_inline_asset_url(asset)
        if not inline_url:
            miss_count += 1
            continue

        matched_image = None
        image_id = normalize_text(str(asset.get("image_id") or ""))
        if image_id:
            candidate = image_by_id.get(image_id)
            if candidate is not None and id(candidate) not in consumed_image_ids:
                matched_image = candidate

        if matched_image is None:
            dom_id = normalize_text(str(asset.get("dom_id") or ""))
            order = _arxiv_asset_order(asset)
            figure = figure_by_id.get(dom_id) if dom_id else None
            figure_images = (
                _arxiv_inline_images_for_figure(figure, article)
                if figure is not None
                else []
            )
            if order is not None and order < len(figure_images):
                candidate = figure_images[order]
                if id(candidate) not in consumed_image_ids:
                    matched_image = candidate

        if matched_image is None:
            asset_candidates = set()
            for candidate_url in _asset_candidate_urls(asset):
                asset_candidates |= _arxiv_url_reference_candidates(
                    candidate_url, source_url
                )
            for image in eligible_images:
                if id(image) in consumed_image_ids:
                    continue
                if _arxiv_url_candidate_sets_match(
                    asset_candidates,
                    image_url_candidates.get(id(image), set()),
                ):
                    matched_image = image
                    break

        if matched_image is None:
            miss_count += 1
            continue

        matched_image[INLINE_FIGURE_SRC_ATTR] = inline_url
        matched_image[INLINE_FIGURE_ALT_ATTR] = _arxiv_inline_asset_alt(asset)
        consumed_image_ids.add(id(matched_image))
        match_count += 1

    return {
        "inline_figure_image_count": match_count,
        "inline_figure_asset_match_count": match_count,
        "inline_figure_asset_miss_count": miss_count,
    }


def _arxiv_parent_identities(node: Any) -> set[int]:
    identities: set[int] = set()
    current = getattr(node, "parent", None)
    while Tag is not None and isinstance(current, Tag):
        identities.add(id(current))
        current = getattr(current, "parent", None)
    return identities


def _arxiv_topmost_figure_ancestor(node: Any, article: Any) -> Any:
    if Tag is None or not isinstance(node, Tag):
        return None
    topmost = None
    current = getattr(node, "parent", None)
    while isinstance(current, Tag) and current is not article:
        if current.name == "figure":
            topmost = current
        current = getattr(current, "parent", None)
    return topmost


def _replace_arxiv_semantic_node_with_placeholder(
    node: Any, article: Any, soup: Any, placeholder: str
) -> None:
    if Tag is None or not isinstance(node, Tag):
        return
    placeholder_node = soup.new_string(f"\n\n{placeholder}\n\n")
    if node.name == "figure":
        node.replace_with(placeholder_node)
        return
    figure_anchor = _arxiv_topmost_figure_ancestor(node, article)
    if isinstance(figure_anchor, Tag):
        figure_anchor.insert_before(placeholder_node)
        node.decompose()
        return
    node.replace_with(placeholder_node)


def _clean_official_html_latexml_noise(article: Any) -> dict[str, int]:
    if Tag is None or not isinstance(article, Tag):
        return {
            "latexml_error_nodes_removed": 0,
            "figure_alt_placeholders_removed": 0,
            "math_nodes_normalized": 0,
            "footnote_nodes_normalized": 0,
        }

    removed_error_nodes = 0
    for node in list(_arxiv_select(article, "latexml_error_nodes")):
        if not isinstance(node, Tag):
            continue
        classes = _arxiv_node_classes(node)
        text = normalize_text(node.get_text("", strip=True))
        if ("ltx_error" in classes or "undefined" in classes) and (
            not text or _ARXIV_UNDEFINED_MACRO_PATTERN.fullmatch(text)
        ):
            node.decompose()
            removed_error_nodes += 1

    removed_alt_placeholders = 0
    for image in article.find_all("img"):
        if not isinstance(image, Tag):
            continue
        alt_text = normalize_text(str(image.get("alt") or ""))
        if alt_text and not html_assets.clean_noisy_image_alt_text(alt_text):
            del image["alt"]
            removed_alt_placeholders += 1

    math_nodes_normalized = _normalize_official_html_latexml_math_nodes(article)
    footnote_nodes_normalized = _normalize_official_html_latexml_notes(article)

    return {
        "latexml_error_nodes_removed": removed_error_nodes,
        "figure_alt_placeholders_removed": removed_alt_placeholders,
        "math_nodes_normalized": math_nodes_normalized,
        "footnote_nodes_normalized": footnote_nodes_normalized,
    }


def _arxiv_math_annotation_latex(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    for annotation in node.find_all("annotation"):
        if not isinstance(annotation, Tag):
            continue
        encoding = normalize_text(str(annotation.get("encoding") or "")).lower()
        if encoding == "application/x-tex":
            latex = annotation.get_text("", strip=False)
            return _sanitize_arxiv_math_annotation_latex(html_lib.unescape(latex))
    alttext = normalize_text(str(node.get("alttext") or ""))
    return _sanitize_arxiv_math_annotation_latex(html_lib.unescape(alttext))


def _latex_braces_are_balanced(text: str) -> bool:
    depth = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return depth == 0


def _sanitize_arxiv_math_annotation_latex(value: str) -> str:
    latex = normalize_latex(value)
    if not latex:
        return ""
    latex = _ARXIV_UNESCAPED_DOLLAR_PATTERN.sub("", latex)
    latex = normalize_latex(latex)
    if not latex or _ARXIV_UNESCAPED_DOLLAR_PATTERN.search(latex):
        return ""
    if r"\[" in latex or r"\]" in latex:
        return ""
    if not _latex_braces_are_balanced(latex):
        return ""
    return latex


def _arxiv_math_is_display(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    return normalize_text(str(node.get("display") or "")).lower() == "block"


def _arxiv_math_markdown(node: Any) -> str:
    latex = _arxiv_math_annotation_latex(node)
    if not latex:
        return ""
    if _arxiv_math_is_display(node):
        return f"\n\n$$\n{latex}\n$$\n\n"
    return f"${latex}$"


def _normalize_official_html_latexml_math_nodes(article: Any) -> int:
    if BeautifulSoup is None or Tag is None or not isinstance(article, Tag):
        return 0
    normalized_count = 0
    for node in list(_arxiv_select(article, "math_nodes")):
        if not isinstance(node, Tag):
            continue
        replacement = _arxiv_math_markdown(node)
        if not replacement:
            continue
        node.replace_with(replacement)
        normalized_count += 1
    return normalized_count


def _normalize_official_html_latexml_notes(article: Any) -> int:
    if BeautifulSoup is None or Tag is None or not isinstance(article, Tag):
        return 0
    normalized_count = 0
    for note in list(_arxiv_select(article, "note_nodes")):
        if not isinstance(note, Tag):
            continue
        classes = _arxiv_node_classes(note)
        if "ltx_role_footnote" not in classes and "ltx_role_endnote" not in classes:
            continue
        marker_node = _arxiv_select_one(note, "note_markers")
        marker = normalize_text(
            marker_node.get_text(" ", strip=True)
            if isinstance(marker_node, Tag)
            else ""
        )
        content_node = _arxiv_select_one(note, "note_content")
        if not isinstance(content_node, Tag):
            continue

        content_soup = BeautifulSoup(str(content_node), "html.parser")
        content = content_soup.find()
        if not isinstance(content, Tag):
            continue
        for duplicate_marker in _arxiv_select(content, "note_markers"):
            duplicate_marker.decompose()
        content_text = normalize_text(
            render_clean_text_from_html(content).replace("\n", " ")
        )
        if not marker and not content_text:
            continue

        note.clear()
        if marker:
            sup = BeautifulSoup("", "html.parser").new_tag("sup")
            sup.string = marker
            note.append(sup)
        if content_text:
            if marker:
                note.append(" ")
            note.append(content_text)
        normalized_count += 1
    return normalized_count


def _arxiv_label_from_identifier(node: Any, *, default_label: str) -> str:
    if Tag is None or not isinstance(node, Tag):
        return default_label
    current: Any = node
    while isinstance(current, Tag):
        node_id = normalize_text(str(current.get("id") or ""))
        table_match = _ARXIV_TABLE_ID_PATTERN.search(node_id)
        if table_match is not None and default_label.lower() == "table":
            return f"Table {table_match.group('number')}."
        algorithm_match = _ARXIV_ALGORITHM_ID_PATTERN.search(node_id)
        if algorithm_match is not None and default_label.lower() == "algorithm":
            return f"Algorithm {algorithm_match.group('number')}."
        current = getattr(current, "parent", None)
    return default_label


def _normalize_arxiv_caption_text(text: str) -> str:
    return normalize_text(str(text or "").replace("\n", " "))


def _arxiv_caption_label_and_text(node: Any, *, default_label: str) -> tuple[str, str]:
    caption = ""
    if Tag is not None and isinstance(node, Tag):
        caption_node = node.find("figcaption")
        if isinstance(caption_node, Tag):
            caption = _normalize_arxiv_caption_text(
                render_clean_text_from_html(caption_node)
            )
    label = _arxiv_label_from_identifier(node, default_label=default_label)
    if caption:
        match = _ARXIV_CAPTION_LABEL_PATTERN.match(caption)
        if match is not None and match.group("label").lower().startswith(
            default_label.lower()
        ):
            raw_label = normalize_text(match.group("label"))
            number = raw_label.split(None, 1)[1] if " " in raw_label else ""
            label = f"{default_label} {number}.".strip() if number else label
            caption = _normalize_arxiv_caption_text(match.group("caption"))
    return label, caption


def _arxiv_table_markdown_has_body(markdown_text: str) -> bool:
    normalized = normalize_text(markdown_text)
    if not normalized:
        return False
    lines = [
        line.strip() for line in markdown_text.splitlines() if normalize_text(line)
    ]
    return any(line.startswith("|") for line in lines) or any(
        line.startswith("- ") for line in lines
    )


def _arxiv_table_markdown_is_key_value_fallback(markdown_text: str) -> bool:
    lines = [
        line.strip() for line in markdown_text.splitlines() if normalize_text(line)
    ]
    return any(line.startswith("- ") for line in lines) and not any(
        line.startswith("|") for line in lines
    )


def _render_arxiv_table_block(node: Any) -> tuple[str, bool, bool]:
    if Tag is None or not isinstance(node, Tag):
        return "", False, False
    label, caption = _arxiv_caption_label_and_text(node, default_label="Table")
    if label == "Table" and not caption:
        label = ""
    markdown = render_table_markdown(
        node,
        label=label,
        caption=caption,
        render_inline_text=render_clean_text_from_html,
    )
    rendered = _arxiv_table_markdown_has_body(markdown)
    return (
        normalize_markdown_text(markdown),
        rendered,
        _arxiv_table_markdown_is_key_value_fallback(markdown),
    )


def _clean_arxiv_listing_line_node(line_node: Any) -> Any:
    if BeautifulSoup is None or Tag is None or not isinstance(line_node, Tag):
        return None
    clone_soup = BeautifulSoup(str(line_node), "html.parser")
    clone = clone_soup.find()
    if not isinstance(clone, Tag):
        return None
    for selector in _arxiv_ar5iv_selectors("listing_noise"):
        for node in clone.select(selector):
            node.decompose()
    return clone


def _render_arxiv_listing_lines(listing_node: Any) -> list[str]:
    if Tag is None or not isinstance(listing_node, Tag):
        return []
    lines: list[str] = []
    for line_node in _arxiv_select(listing_node, "listing_lines"):
        clone = _clean_arxiv_listing_line_node(line_node)
        text = render_clean_text_from_html(clone) if clone is not None else ""
        text = normalize_text(text.replace("\n", " "))
        if text:
            lines.append(text)
    if lines:
        return lines
    text = normalize_text(render_clean_text_from_html(listing_node).replace("\n", "\n"))
    return [line for line in text.splitlines() if normalize_text(line)]


def _render_arxiv_listing_block(node: Any) -> tuple[str, bool]:
    if Tag is None or not isinstance(node, Tag):
        return "", False
    listing = (
        _arxiv_select_one(node, "algorithm_listing") if node.name == "figure" else node
    )
    if not isinstance(listing, Tag):
        return "", False
    label, caption = _arxiv_caption_label_and_text(node, default_label="Algorithm")
    heading_line = f"**{label}** {caption}".strip()
    code_lines = _render_arxiv_listing_lines(listing)
    if not code_lines:
        return normalize_markdown_text(heading_line), False
    escaped_lines = [line.replace("```", "'''") for line in code_lines]
    return normalize_markdown_text(
        "\n".join([heading_line, "", "```text", *escaped_lines, "```"])
    ), True


def _prepare_arxiv_semantic_blocks(article: Any, soup: Any) -> ArxivSemanticPreparation:
    if BeautifulSoup is None or Tag is None or not isinstance(article, Tag):
        return ArxivSemanticPreparation(entries=[], diagnostics={}, warnings=[])

    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    selected_ancestor_ids: set[int] = set()
    table_total = 0
    listing_total = 0
    table_rendered = 0
    listing_rendered = 0
    table_key_value_fallback = 0
    semantic_loss_count = 0

    for node in list(article.find_all(True)):
        if id(node) in selected_ancestor_ids or selected_ancestor_ids.intersection(
            _arxiv_parent_identities(node)
        ):
            continue

        kind = ""
        markdown = ""
        rendered = False
        key_value_fallback = False
        if _is_arxiv_algorithm_figure(node):
            kind = "listing"
            listing_total += 1
            markdown, rendered = _render_arxiv_listing_block(node)
        elif _is_arxiv_table_figure(node):
            kind = "table"
            table_total += 1
            markdown, rendered, key_value_fallback = _render_arxiv_table_block(node)
        elif _is_arxiv_tabular_table(node) and not any(
            _is_arxiv_table_figure(parent) for parent in node.parents
        ):
            kind = "table"
            table_total += 1
            markdown, rendered, key_value_fallback = _render_arxiv_table_block(node)
        elif _is_arxiv_listing_node(node) and not any(
            _is_arxiv_algorithm_figure(parent) for parent in node.parents
        ):
            kind = "listing"
            listing_total += 1
            markdown, rendered = _render_arxiv_listing_block(node)

        if not kind:
            continue
        if not rendered:
            semantic_loss_count += 1
            warnings.append(
                f"arXiv HTML {kind} block could not be rendered with semantic content."
            )
            continue

        placeholder = table_placeholder(len(entries))
        entries.append(
            {
                "kind": kind,
                "placeholder": placeholder,
                "markdown": markdown,
                "key_value_fallback": key_value_fallback,
            }
        )
        if kind == "table":
            table_rendered += 1
            if key_value_fallback:
                table_key_value_fallback += 1
        else:
            listing_rendered += 1
        selected_ancestor_ids.add(id(node))
        _replace_arxiv_semantic_node_with_placeholder(node, article, soup, placeholder)

    diagnostics = {
        "table_block_count": table_total,
        "table_block_rendered_count": table_rendered,
        "table_key_value_fallback_count": table_key_value_fallback,
        "listing_block_count": listing_total,
        "listing_block_rendered_count": listing_rendered,
        "semantic_block_count": table_total + listing_total,
        "semantic_block_rendered_count": table_rendered + listing_rendered,
        "semantic_block_loss_count": semantic_loss_count,
    }
    return ArxivSemanticPreparation(
        entries=entries, diagnostics=diagnostics, warnings=warnings
    )


def _split_markdown_block_around_placeholder(
    block: str, placeholder: str, replacement: str
) -> list[str]:
    pieces: list[str] = []
    remaining = block
    while placeholder in remaining:
        before, remaining = remaining.split(placeholder, 1)
        if normalize_text(before):
            pieces.append(normalize_markdown_text(before))
        pieces.extend(
            normalize_markdown_text(part)
            for part in re.split(r"\n\s*\n", replacement)
            if normalize_text(part)
        )
    if normalize_text(remaining):
        pieces.append(normalize_markdown_text(remaining))
    return pieces


def _inject_arxiv_semantic_blocks(
    markdown_text: str,
    *,
    entries: list[dict[str, Any]],
) -> tuple[str, dict[str, int], list[str]]:
    if not entries:
        return markdown_text, {"inserted_count": 0, "appended_count": 0}, []

    markdown_text = inject_inline_table_blocks(
        markdown_text,
        table_entries=entries,
        clean_markdown_fn=clean_markdown,
    )
    replacement_by_placeholder = {
        normalize_text(str(entry.get("placeholder") or "")): normalize_markdown_text(
            str(entry.get("markdown") or "")
        )
        for entry in entries
        if normalize_text(str(entry.get("placeholder") or ""))
        and normalize_text(str(entry.get("markdown") or ""))
    }
    inserted: set[str] = {
        placeholder
        for placeholder, replacement in replacement_by_placeholder.items()
        if replacement
        and replacement in markdown_text
        and placeholder not in markdown_text
    }
    blocks = [
        normalize_markdown_text(block)
        for block in re.split(r"\n\s*\n", markdown_text)
        if normalize_text(block)
    ]
    injected: list[str] = []
    for block in blocks:
        placeholders = [
            placeholder
            for placeholder in _ARXIV_PLACEHOLDER_PATTERN.findall(block)
            if placeholder in replacement_by_placeholder
        ]
        if not placeholders:
            injected.append(block)
            continue
        pending_blocks = [block]
        for placeholder in placeholders:
            replacement = replacement_by_placeholder[placeholder]
            next_blocks: list[str] = []
            for pending_block in pending_blocks:
                if placeholder in pending_block:
                    next_blocks.extend(
                        _split_markdown_block_around_placeholder(
                            pending_block, placeholder, replacement
                        )
                    )
                    inserted.add(placeholder)
                else:
                    next_blocks.append(pending_block)
            pending_blocks = next_blocks
        injected.extend(pending_blocks)

    appended_markdown: list[str] = []
    appended_count = 0
    for entry in entries:
        placeholder = normalize_text(str(entry.get("placeholder") or ""))
        replacement = replacement_by_placeholder.get(placeholder, "")
        if not placeholder or not replacement or placeholder in inserted:
            continue
        appended_markdown.append(replacement)
        appended_count += 1

    warnings: list[str] = []
    if appended_markdown:
        warnings.append(
            f"arXiv HTML semantic block placeholders could not all be reinserted; appended {appended_count} block(s) at document end."
        )
    cleaned = clean_markdown("\n\n".join([*injected, *appended_markdown]))
    return (
        cleaned,
        {"inserted_count": len(inserted), "appended_count": appended_count},
        warnings,
    )


def _clean_arxiv_html_markdown_noise(markdown_text: str) -> str:
    blocks = [
        normalize_markdown_text(block)
        for block in re.split(r"\n\s*\n", markdown_text)
        if normalize_text(block)
    ]
    cleaned_blocks: list[str] = []
    for block in blocks:
        normalized = normalize_text(block)
        if normalized in {"****"}:
            continue
        cleaned_blocks.append(block)
    cleaned = "\n\n".join(cleaned_blocks)
    cleaned = re.sub(
        r"(<sup>(?P<marker>[^<]+)</sup>)\s*<sup>(?P=marker)</sup>\s*(?P=marker)\s*",
        r"\1 ",
        cleaned,
    )
    cleaned = re.sub(r"(?m)^-\s+[•◦▪▫‣⁃∙●○◾◽◼□■]\s*", "- ", cleaned)
    return clean_markdown(cleaned)


def _arxiv_html_contains_fatal_conversion_error(markdown_text: str) -> bool:
    normalized = normalize_text(markdown_text)
    return any(
        pattern.search(normalized) for pattern in _ARXIV_HTML_FATAL_ERROR_PATTERNS
    )


def _is_arxiv_bibliography_title_heading(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    if _arxiv_node_has_class(node, "ltx_title_bibliography"):
        return True
    text = normalize_text(render_heading_text_from_html(node)).lower().strip(" .:")
    return text in {"references", "bibliography"}


def _arxiv_heading_in_skipped_hint_scope(node: Any, article: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return True
    current: Any = node
    while isinstance(current, Tag) and current is not article:
        name = normalize_text(current.name or "").lower()
        if name in _ARXIV_SECTION_HINT_SKIP_TAGS:
            return True
        classes = _arxiv_node_classes(current)
        if classes.intersection(_ARXIV_SECTION_HINT_SKIP_CLASS_TOKENS):
            return True
        if "ltx_bibliography" in classes and not _is_arxiv_bibliography_title_heading(
            node
        ):
            return True
        current = getattr(current, "parent", None)
    return False


def _arxiv_section_hint_kind(
    node: Any, heading: str, *, title: str | None
) -> str | None:
    node_name = normalize_text(getattr(node, "name", "") or "").lower()
    category = heading_category(node_name, heading, title=title)
    if category in {"abstract", "front_matter"}:
        return None
    shared_kind = section_hint_kind_for_category(category)
    if shared_kind is not None:
        return shared_kind
    return "body"


def _collect_arxiv_html_section_hints(
    article: Any, *, title: str | None = None
) -> list[dict[str, Any]]:
    if Tag is None or not isinstance(article, Tag):
        return []
    hints: list[dict[str, Any]] = []
    for node in article.find_all(SECTION_HEADING_PATTERN):
        if not isinstance(node, Tag) or _arxiv_heading_in_skipped_hint_scope(
            node, article
        ):
            continue
        heading = normalize_text(render_heading_text_from_html(node))
        if not heading:
            continue
        kind = _arxiv_section_hint_kind(node, heading, title=title)
        if kind is None:
            continue
        level_match = SECTION_HEADING_PATTERN.fullmatch(
            normalize_text(node.name or "").lower()
        )
        level = int(level_match.group(1)) if level_match else 2
        selector_node = (
            node.parent if isinstance(getattr(node, "parent", None), Tag) else node
        )
        hints.append(
            {
                "heading": heading,
                "level": level,
                "kind": kind,
                "order": len(hints),
                "language": None,
                "source_selector": node_source_selector(selector_node) or None,
            }
        )
    return hints


def _extract_arxiv_html_markdown(
    html_text: str,
    source_url: str,
    *,
    metadata: Mapping[str, Any],
) -> ArxivHtmlExtraction:
    if BeautifulSoup is None:
        raise ProviderFailure(
            NOT_CONFIGURED,
            "beautifulsoup4 is not installed; cannot parse arXiv HTML.",
        )
    soup = BeautifulSoup(html_text, "html.parser")
    article = _arxiv_select_one(soup, "article_root") or soup.find("article")
    if not isinstance(article, Tag):
        raise ProviderFailure(
            NO_RESULT, "arXiv official HTML did not expose a LaTeXML article body."
        )

    html_frontmatter = _extract_arxiv_html_frontmatter(
        soup,
        article,
        source_url,
        metadata=metadata,
    )
    noise_diagnostics = _clean_official_html_latexml_noise(article)
    extracted_references = _extract_arxiv_html_references(article)
    extracted_assets = _extract_arxiv_html_assets(str(article), source_url)
    semantic_preparation = _prepare_arxiv_semantic_blocks(article, soup)
    inline_figure_diagnostics = _annotate_arxiv_inline_figure_images(
        article, extracted_assets, source_url
    )

    for selector in _arxiv_ar5iv_selectors("article_chrome"):
        for node in article.select(selector):
            node.decompose()

    section_hints = _collect_arxiv_html_section_hints(
        article,
        title=normalize_text(metadata.get("title")),
    )
    lines: list[str] = []
    render_container_markdown(article, lines, level=2, section_content_selectors=())
    markdown_text = normalize_markdown_text("\n".join(lines))
    markdown_text, insertion_diagnostics, insertion_warnings = (
        _inject_arxiv_semantic_blocks(
            markdown_text,
            entries=semantic_preparation.entries,
        )
    )
    markdown_text = _clean_arxiv_html_markdown_noise(markdown_text)
    if _arxiv_html_contains_fatal_conversion_error(markdown_text):
        raise ProviderFailure(
            NO_RESULT, "arXiv official HTML was not classified as usable full text."
        )
    if _markdown_word_count(markdown_text) < MIN_HTML_MARKDOWN_WORDS:
        raise ProviderFailure(
            NO_RESULT, "arXiv official HTML did not expose enough body text."
        )
    if "##" not in markdown_text:
        raise ProviderFailure(
            NO_RESULT, "arXiv official HTML did not expose section headings."
        )

    diagnostics = assess_plain_text_fulltext_availability(
        markdown_text,
        metadata,
        title=normalize_text(metadata.get("title")),
        section_hints=section_hints,
    ).to_dict()
    if diagnostics.get("content_kind") != FULLTEXT:
        raise ProviderFailure(
            NO_RESULT, "arXiv official HTML was not classified as usable full text."
        )
    diagnostics.setdefault("extraction", {})
    diagnostics["extraction"] = {
        **dict(diagnostics.get("extraction") or {}),
        "parser": "latexml_html",
        "source_url": source_url,
        "word_count": _markdown_word_count(markdown_text),
        "formula_block_count": markdown_text.count("$$") // 2,
        "reference_count": len(extracted_references),
        "asset_count": len(extracted_assets),
        "section_hints": [dict(item) for item in section_hints],
        **noise_diagnostics,
        **inline_figure_diagnostics,
        **semantic_preparation.diagnostics,
        **{
            "semantic_block_inserted_count": insertion_diagnostics.get(
                "inserted_count", 0
            ),
            "semantic_block_appended_count": insertion_diagnostics.get(
                "appended_count", 0
            ),
        },
    }
    semantic_loss_count = int(
        semantic_preparation.diagnostics.get("semantic_block_loss_count") or 0
    )
    diagnostics["semantic_losses"] = {
        "table_fallback_count": semantic_loss_count,
        "table_semantic_loss_count": semantic_loss_count,
    }
    merged_metadata = _merge_arxiv_metadata_layers(
        metadata,
        html_metadata=html_frontmatter,
        references=extracted_references,
    )
    return ArxivHtmlExtraction(
        markdown_text=markdown_text,
        merged_metadata=merged_metadata,
        extracted_assets=extracted_assets,
        section_hints=[dict(item) for item in section_hints],
        diagnostics=diagnostics,
        warnings=[*semantic_preparation.warnings, *insertion_warnings],
    )


class ArxivClient(ProviderClient):
    name = "arxiv"

    def __init__(
        self,
        transport: HttpTransport,
        env: Mapping[str, str],
        api_client: Any | None = None,
    ) -> None:
        self.transport = transport
        self.env = dict(env)
        self.user_agent = build_user_agent(env)
        self.api_enrichment_enabled = api_client is not None
        self.api_client = api_client or InternalArxivApiClient(
            transport=self.transport,
            user_agent=self.user_agent,
        )

    def probe_status(self) -> ProviderStatusResult:
        return summarize_capability_status(
            self.name,
            official_provider=self.official_provider,
            checks=[
                build_provider_status_check(
                    "metadata_api",
                    OK,
                    "arXiv API metadata route is an optional metadata enrichment path.",
                    details={
                        "mode": "arxiv_api",
                        "client": "internal_atom",
                        "client_delay_seconds": ARXIV_API_DELAY_SECONDS,
                        "client_num_retries": ARXIV_API_NUM_RETRIES,
                    },
                ),
                build_provider_status_check(
                    "html_route",
                    OK,
                    "arXiv official HTML route is the primary full-text path and is available without local converters.",
                    details={"mode": "direct_http_html"},
                ),
                build_provider_status_check(
                    PDF_FALLBACK,
                    OK,
                    (
                        "arXiv PDF fallback is available as text-only full text when official HTML "
                        "is not usable."
                    ),
                    details={"mode": "direct_http_pdf"},
                ),
            ],
        )

    def _html_headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": self.user_agent,
        }

    def _image_headers(self) -> dict[str, str]:
        return {
            "Accept": ARXIV_IMAGE_ACCEPT,
            "User-Agent": self.user_agent,
        }

    def _pdf_headers(self, *, referer: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": PDF_ACCEPT_HEADER,
            "User-Agent": self.user_agent,
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def fetch_metadata(self, query: Mapping[str, str | None]) -> ProviderMetadata:
        arxiv_id = (
            normalize_arxiv_id(query.get("arxiv_id"))
            or arxiv_id_from_doi(query.get("doi"))
            or arxiv_id_from_query(query.get("landing_page_url"))
            or arxiv_id_from_query(query.get("url"))
        )
        if not arxiv_id:
            raise ProviderFailure(
                NOT_SUPPORTED,
                "arXiv metadata retrieval requires an arXiv ID or arXiv DOI.",
            )
        try:
            search = ArxivSearch(id_list=[arxiv_id], max_results=1)
            results = list(self.api_client.results(search))
        except Exception as exc:
            raise ProviderFailure(
                ERROR, f"arXiv API metadata retrieval failed: {exc}"
            ) from exc
        if not results:
            raise ProviderFailure(
                NO_RESULT, f"arXiv API returned no result for {arxiv_id}."
            )
        return metadata_from_arxiv_result(results[0], requested_arxiv_id=arxiv_id)

    def _ensure_derived_metadata(
        self, doi: str, metadata: Mapping[str, Any]
    ) -> ProviderMetadata:
        arxiv_id = _arxiv_id_from_metadata_or_doi(doi, metadata)
        if not arxiv_id:
            raise ProviderFailure(
                NOT_SUPPORTED,
                "arXiv full-text retrieval requires an arXiv ID or arXiv DOI.",
            )
        return _minimal_arxiv_metadata(arxiv_id, doi=doi, metadata=metadata)

    def _fetch_api_metadata_optional(
        self, arxiv_id: str
    ) -> tuple[ProviderMetadata | None, list[str]]:
        try:
            return self.fetch_metadata({"arxiv_id": arxiv_id}), []
        except ProviderFailure as exc:
            warning = (
                "arXiv API metadata retrieval failed; using official HTML front matter and derived "
                f"arXiv URLs from identifier {arxiv_id} ({exc.message})."
            )
            return None, [warning]

    def _payload_with_api_metadata(
        self,
        payload: RawFulltextPayload,
        *,
        derived_metadata: Mapping[str, Any],
        api_metadata: Mapping[str, Any] | None,
        metadata_warnings: Sequence[str],
    ) -> RawFulltextPayload:
        warnings = [*list(payload.warnings), *list(metadata_warnings)]
        if not api_metadata:
            payload.warnings = warnings
            return payload
        content = payload.content
        content_metadata = (
            content.merged_metadata if content is not None else payload.merged_metadata
        )
        references = (
            list(content_metadata.get("references") or [])
            if isinstance(content_metadata, Mapping)
            else []
        )
        merged_metadata = _merge_arxiv_metadata_layers(
            derived_metadata,
            html_metadata=content_metadata
            if isinstance(content_metadata, Mapping)
            else None,
            api_metadata=api_metadata,
            references=references,
        )
        payload.merged_metadata = merged_metadata
        payload.warnings = warnings
        if content is not None:
            payload.content = replace(content, merged_metadata=merged_metadata)
        return payload

    def _fetch_html_payload(
        self, api_metadata: Mapping[str, Any]
    ) -> RawFulltextPayload:
        arxiv_id = normalize_arxiv_id(str(api_metadata.get("arxiv_id") or ""))
        html_url = normalize_text(
            str(api_metadata.get("html_url") or "")
        ) or canonical_arxiv_html_url(arxiv_id)
        if not html_url:
            raise ProviderFailure(
                NO_RESULT, "arXiv metadata did not expose an HTML candidate."
            )
        try:
            response = self.transport.request(
                "GET",
                html_url,
                headers=self._html_headers(),
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                retry_on_transient=True,
            )
        except RequestFailure as exc:
            raise map_request_failure(exc) from exc

        body = bytes(response.get("body") or b"")
        final_url = urllib.parse.urljoin(
            html_url, normalize_text(str(response.get("url") or "")) or html_url
        )
        content_type = _first_header_value(
            response.get("headers"), "content-type", "text/html"
        )
        if not _looks_like_html(content_type, body):
            raise ProviderFailure(
                NO_RESULT, "arXiv official HTML candidate did not return HTML."
            )
        html_text = body.decode("utf-8", errors="replace")
        extraction = _extract_arxiv_html_markdown(
            html_text, final_url, metadata=api_metadata
        )
        return build_provider_payload(
            provider=self.name,
            route_kind="html",
            source_url=final_url,
            content_type=content_type,
            body=body,
            markdown_text=extraction.markdown_text,
            merged_metadata=extraction.merged_metadata,
            diagnostics={
                "availability_diagnostics": extraction.diagnostics,
                "extraction": extraction.diagnostics.get("extraction"),
                "semantic_losses": extraction.diagnostics.get("semantic_losses"),
            },
            reason="Downloaded full text from arXiv official HTML.",
            extracted_assets=extraction.extracted_assets,
            warnings=extraction.warnings,
            trace_markers=[fulltext_marker(self.name, "ok", route="html")],
        )

    def _fetch_pdf_payload(
        self,
        api_metadata: Mapping[str, Any],
        *,
        previous_failure_message: str,
    ) -> RawFulltextPayload:
        arxiv_id = normalize_arxiv_id(str(api_metadata.get("arxiv_id") or ""))
        candidates = _dedupe_strings(
            [
                str(api_metadata.get("pdf_url") or ""),
                canonical_arxiv_pdf_url(arxiv_id),
            ]
        )
        if not candidates:
            raise ProviderFailure(
                NO_RESULT, "arXiv metadata did not expose a PDF candidate."
            )
        referer = normalize_text(
            str(
                api_metadata.get("html_url")
                or api_metadata.get("landing_page_url")
                or ""
            )
        )
        try:
            pdf_result = PdfFallbackStrategy(
                transport=self.transport,
                headers=self._pdf_headers(referer=referer),
                timeout=DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
                fetcher=fetch_pdf_over_http,
            ).fetch(candidates)
        except PdfFetchFailure as exc:
            raise ProviderFailure(NO_RESULT, exc.message) from exc
        final_url = urllib.parse.urljoin(
            pdf_result.source_url or candidates[0], pdf_result.final_url
        )
        return build_provider_payload(
            provider=self.name,
            route_kind=PDF_FALLBACK,
            source_url=final_url,
            content_type=PDF_MIME_TYPE,
            body=pdf_result.pdf_bytes,
            markdown_text=pdf_result.markdown_text,
            merged_metadata=api_metadata,
            diagnostics={
                PDF_FALLBACK: {
                    "candidates": candidates,
                    "previous_failure_message": previous_failure_message,
                }
            },
            reason="Downloaded full text from arXiv PDF fallback after arXiv official HTML was not usable.",
            suggested_filename=pdf_result.suggested_filename,
            html_failure_message=previous_failure_message,
            warnings=[
                "Full text was extracted from arXiv PDF fallback after arXiv official HTML was not usable."
            ],
            content_needs_local_copy=True,
            needs_local_copy=True,
        )

    def fetch_raw_fulltext(
        self,
        doi: str,
        metadata: ProviderMetadata,
        *,
        context: RuntimeContext | None = None,
    ) -> RawFulltextPayload:
        del context
        derived_metadata = self._ensure_derived_metadata(doi, metadata)
        arxiv_id = normalize_arxiv_id(str(derived_metadata.get("arxiv_id") or ""))

        def run_html(_state: ProviderWaterfallState) -> RawFulltextPayload:
            return self._fetch_html_payload(derived_metadata)

        def run_pdf(state: ProviderWaterfallState) -> RawFulltextPayload:
            failure_messages = [
                f"{label}: {failure.message}"
                for label in ("html",)
                if (failure := state.failure(label)) is not None
            ]
            previous_failure_message = (
                "; ".join(failure_messages) or "arXiv official HTML route failed."
            )
            return self._fetch_pdf_payload(
                derived_metadata, previous_failure_message=previous_failure_message
            )

        payload = run_provider_waterfall(
            [
                ProviderWaterfallStep(
                    label="html",
                    run=run_html,
                    failure_marker=fulltext_marker(self.name, "fail", route="html"),
                    success_markers=(fulltext_marker(self.name, "ok", route="html"),),
                    continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,
                    failure_warning=lambda failure, _state: (
                        "arXiv official HTML route was not usable "
                        f"({failure.message}); attempting PDF fallback."
                    ),
                ),
                ProviderWaterfallStep(
                    label="pdf",
                    run=run_pdf,
                    failure_marker=fulltext_marker(self.name, "fail", route="pdf"),
                    success_markers=(
                        fulltext_marker(self.name, "ok", route=PDF_FALLBACK),
                    ),
                    continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,
                    failure_warning=lambda failure, _state: (
                        f"arXiv PDF fallback was not usable ({failure.message})."
                    ),
                ),
            ],
            initial_warnings=[],
        )
        if not self.api_enrichment_enabled:
            return payload
        api_metadata, metadata_warnings = self._fetch_api_metadata_optional(arxiv_id)
        return self._payload_with_api_metadata(
            payload,
            derived_metadata=derived_metadata,
            api_metadata=api_metadata,
            metadata_warnings=metadata_warnings,
        )

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
        del metadata
        context = self._runtime_context(context, output_dir=output_dir)
        if output_dir is None or asset_profile == "none":
            return empty_asset_results()
        content = raw_payload.content
        route_kind = normalize_text(
            content.route_kind if content is not None else ""
        ).lower()
        if route_kind != "html":
            return empty_asset_results()
        extracted_assets = [
            dict(item)
            for item in (content.extracted_assets if content is not None else [])
            if _asset_has_download_candidate(item)
        ]
        if not extracted_assets:
            return empty_asset_results()
        merged_metadata = (
            content.merged_metadata
            if content is not None
            else raw_payload.merged_metadata
        )
        arxiv_id = normalize_arxiv_id(
            str((merged_metadata or {}).get("arxiv_id") or "")
        ) or _arxiv_id_from_metadata_or_doi(doi, merged_metadata or {})
        article_id = arxiv_id or normalize_text(doi) or raw_payload.source_url
        asset_download_concurrency = _arxiv_asset_download_concurrency(context.env)
        initial_result = html_assets.download_figure_assets(
            self.transport,
            article_id=article_id,
            assets=extracted_assets,
            output_dir=output_dir,
            user_agent=self.user_agent,
            asset_profile=asset_profile,
            headers=self._image_headers(),
            asset_download_concurrency=asset_download_concurrency,
        )
        retry_assets = _assets_for_arxiv_network_retry(
            extracted_assets,
            initial_result.get("asset_failures") or [],
        )
        if not retry_assets:
            return initial_result
        retry_result = html_assets.download_figure_assets(
            self.transport,
            article_id=article_id,
            assets=retry_assets,
            output_dir=output_dir,
            user_agent=self.user_agent,
            asset_profile=asset_profile,
            headers=self._image_headers(),
            asset_download_concurrency=1,
        )
        return _merge_arxiv_asset_download_results(
            initial_result,
            retry_result,
            retried_assets=retry_assets,
        )

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
        merged_metadata = (
            content.merged_metadata
            if content is not None
            else raw_payload.merged_metadata
        )
        article_metadata = dict(
            merged_metadata if isinstance(merged_metadata, Mapping) else metadata
        )
        arxiv_id = normalize_arxiv_id(str(article_metadata.get("arxiv_id") or ""))
        doi = canonical_arxiv_doi(arxiv_id) or str(
            article_metadata.get("doi") or metadata.get("doi") or ""
        )
        route = normalize_text(
            content.route_kind if content is not None else ""
        ).lower()
        source = {
            PDF_FALLBACK: "arxiv_pdf",
            "html": "arxiv_html",
        }.get(route, "arxiv_html")
        markdown_text = str(
            (content.markdown_text if content is not None else "") or ""
        ).strip()
        default_route = PDF_FALLBACK if route == PDF_FALLBACK else "html"
        trace = list(
            raw_payload.trace
            or trace_from_markers(
                [fulltext_marker(self.name, "ok", route=default_route)]
            )
        )
        warnings = list(raw_payload.warnings)
        if asset_failures:
            warnings.append(
                f"arXiv related assets were only partially downloaded ({len(asset_failures)} failed)."
            )
        if not markdown_text:
            warnings.append("arXiv retrieval did not produce usable Markdown.")
            return metadata_only_article(
                source=source,
                metadata=article_metadata,
                doi=doi or None,
                warnings=warnings,
                trace=trace,
            )
        availability_diagnostics = (
            dict(content.diagnostics.get("availability_diagnostics") or {})
            if content is not None
            and isinstance(content.diagnostics.get("availability_diagnostics"), Mapping)
            else None
        )
        semantic_losses = (
            dict(content.diagnostics.get("semantic_losses") or {})
            if content is not None
            and isinstance(content.diagnostics.get("semantic_losses"), Mapping)
            else (
                dict(availability_diagnostics.get("semantic_losses") or {})
                if isinstance(availability_diagnostics, Mapping)
                and isinstance(availability_diagnostics.get("semantic_losses"), Mapping)
                else None
            )
        )
        extraction_payload = (
            content.diagnostics.get("extraction")
            if content is not None
            and isinstance(content.diagnostics.get("extraction"), Mapping)
            else {}
        )
        section_hints = (
            list(extraction_payload.get("section_hints") or [])
            if isinstance(extraction_payload, Mapping)
            else []
        )
        article = article_from_markdown(
            source=source,
            metadata=article_metadata,
            doi=doi or None,
            markdown_text=markdown_text,
            section_hints=section_hints,
            assets=[dict(item) for item in (downloaded_assets or [])],
            warnings=warnings,
            trace=trace,
            availability_diagnostics=availability_diagnostics,
            semantic_losses=semantic_losses,
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
        if (
            normalize_text(content.route_kind if content is not None else "").lower()
            != PDF_FALLBACK
        ):
            return artifacts
        return ProviderArtifacts(
            assets=list(artifacts.assets),
            asset_failures=list(artifacts.asset_failures),
            allow_related_assets=False,
            text_only=True,
            skip_warning=(
                "arXiv PDF fallback currently returns text-only full text; "
                "figure and supplementary asset downloads are not implemented for PDF fallback."
            ),
            skip_trace=trace_from_markers(
                [download_marker("arxiv_assets_skipped_text_only")]
            ),
        )


__all__ = [
    "ArxivClient",
    "ArxivHtmlExtraction",
    "arxiv_metadata_probe_short_circuit",
    "metadata_from_arxiv_result",
    "minimal_arxiv_metadata",
]
