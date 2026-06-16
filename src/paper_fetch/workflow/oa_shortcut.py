"""OA shortcut pathway — fetch full-text from PubMed Central without browser.

When Crossref metadata signals an OA article (creativecommons license):
    1. Search Europe PMC for the PMCID
    2. Fetch JATS XML via PMC E-utilities efetch
    3. Extract title / abstract / sections / paragraphs → markdown
    4. Build ArticleModel via ``article_from_markdown()``

If anything fails or the article is not OA, return None so the caller
falls through to the existing browser-based path.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from typing import Any, Mapping

from ..http import HttpTransport
from ..models import article_from_markdown
from ..utils import normalize_text, safe_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
_EPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_PMC_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_JATS_NS = "http://jats.nlm.nih.gov/ns/archiving/1.0"
_MIN_USABLE_WORDS = 100


# ===================================================================
# Public entry point
# ===================================================================


def try_oa_shortcut(
    doi: str,
    metadata: Mapping[str, Any],
    transport: HttpTransport,
) -> Any | None:  # tuple[ArticleModel, str] | None, but avoid circular import
    """Attempt OA full-text retrieval from PubMed Central.

    Returns ``(ArticleModel, markdown_text)`` on success or ``None`` when
    the caller should fall through to the browser-based provider pipeline.
    """
    if not _check_is_oa(metadata):
        logger.debug("OA shortcut: %s is not OA, skipping", doi)
        return None

    logger.info("OA shortcut: trying PMC for %s", doi)

    # ── 1. Get PMCID from EPMC search ────────────────────────────
    pmid_result = _search_epmc(doi, transport)
    pmcid = safe_text(pmid_result.get("pmcid")) or None if pmid_result else None

    if not pmcid:
        logger.info("OA shortcut: no PMCID for %s, falling back to browser", doi)
        return None

    # ── 2. Fetch JATS XML from PMC ───────────────────────────────
    xml_text = _fetch_pmc_xml(pmcid, transport)
    if xml_text is None:
        logger.info("OA shortcut: PMC efetch failed for %s", pmcid)
        return None

    # ── 3. JATS XML → markdown ───────────────────────────────────
    markdown_text = _jats_xml_to_markdown(xml_text)
    if markdown_text is None or _word_count(markdown_text) < _MIN_USABLE_WORDS:
        logger.info(
            "OA shortcut: insufficient content from PMC (%d words), falling back",
            _word_count(markdown_text) if markdown_text else 0,
        )
        return None

    logger.info("OA shortcut: PMC XML → %d words", _word_count(markdown_text))

    # ── 4. Build ArticleModel ────────────────────────────────────
    try:
        article = article_from_markdown(
            source="oa_shortcut",
            metadata=dict(metadata),
            doi=doi,
            markdown_text=markdown_text,
        )
        logger.info("OA shortcut: built ArticleModel for %s", doi)
        return article, markdown_text
    except Exception as exc:
        logger.warning("OA shortcut: article_from_markdown failed for %s: %s", doi, exc)
        return None


# ===================================================================
# OA check
# ===================================================================


def _check_is_oa(metadata: Mapping[str, Any]) -> bool:
    license_urls = metadata.get("license_urls") or []
    if not license_urls:
        return False
    for url in license_urls:
        if isinstance(url, str) and "creativecommons" in url.lower():
            return True
    return False


# ===================================================================
# EPMC → PMCID lookup
# ===================================================================


def _search_epmc(doi: str, transport: HttpTransport) -> dict[str, Any] | None:
    """Query the EPMC search API by DOI, return the first result item."""
    try:
        response = transport.request(
            "GET",
            _EPMC_SEARCH_URL,
            query={"query": f"DOI:{doi}", "format": "json", "resultType": "core"},
            timeout=15,
            retry_on_transient=True,
        )
        body = response.get("body", b"{}")
        raw = bytes(body) if isinstance(body, (bytes, bytearray)) else str(body).encode()
        data = json.loads(raw)
        results = data.get("resultList", {}).get("result", [])
        return results[0] if results else None
    except Exception as exc:
        logger.debug("EPMC search failed for %s: %s", doi, exc)
        return None


# ===================================================================
# PMC efetch
# ===================================================================


def _fetch_pmc_xml(pmcid: str, transport: HttpTransport) -> str | None:
    """Fetch the full JATS XML for *pmcid* via NCBI E-utilities efetch."""
    try:
        response = transport.request(
            "GET",
            _PMC_EFETCH_URL,
            query={"db": "pmc", "id": pmcid},
            timeout=30,
            retry_on_transient=True,
        )
        body = response.get("body", b"")
        if isinstance(body, (bytes, bytearray)):
            return bytes(body).decode("utf-8", errors="replace")
        return str(body) if body else None
    except Exception as exc:
        logger.debug("PMC efetch failed for %s: %s", pmcid, exc)
        return None


# ===================================================================
# JATS XML → markdown
# ===================================================================


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from an ElementTree tag string."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _itertext(elem: ET.Element | None) -> str:
    """Concatenate all text nodes inside *elem*, ignoring tags."""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def _jats_xml_to_markdown(xml_text: str) -> str | None:
    """Convert a JATS XML document (as returned by PMC efetch) to markdown.

    Handles the standard ``<pmc-articleset><article>…</article></pmc-articleset>``
    wrapper.  Extracts:
        - article title (``<article-meta>/<title-group>/<article-title>``)
        - abstract   (``<article-meta>/<abstract>``)
        - body sections (``<sec>`` / ``<p>``)
    """
    ET.register_namespace("", _JATS_NS)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.debug("Failed to parse JATS XML")
        return None

    # Locate the <article> element
    article: ET.Element | None = None
    for candidate in root.iter():
        if _strip_ns(candidate.tag) == "article":
            article = candidate
            break

    if article is None:
        return None

    def _find(parent: ET.Element, tag: str) -> ET.Element | None:
        """Try namespace-qualified first, then bare tag."""
        value = parent.find(f"{{{_JATS_NS}}}{tag}")
        if value is not None:
            return value
        return parent.find(tag)

    body = _find(article, "body")
    if body is None:
        return None

    parts: list[str] = []

    # -- article-meta (title + abstract) -------------------------------
    front = _find(article, "front")
    if front is not None:
        meta = _find(front, "article-meta")
        if meta is not None:
            # Title
            tg = _find(meta, "title-group")
            if tg is not None:
                atitle = _find(tg, "article-title")
                if atitle is not None:
                    title_text = _itertext(atitle)
                    if title_text:
                        parts.append(f"# {title_text}\n")

            # Abstract
            _emit_abstract(meta, parts)

    # -- body sections -------------------------------------------------
    for elem in body:
        tag = _strip_ns(elem.tag)
        if tag == "sec":
            _emit_sec(elem, parts, level=2)
        elif tag == "p":
            text = _itertext(elem)
            if text:
                parts.append(f"\n{text}\n")
        elif tag in ("disp-formula", "table-wrap", "fig"):
            # Skip structured elements – the markdown pipeline
            # doesn't need them, and they'd produce garbled text.
            pass

    result = "\n".join(parts)
    if not normalize_text(result):
        return None
    return result


def _emit_abstract(meta: ET.Element, parts: list[str]) -> None:
    """Extract ``<abstract>`` from ``<article-meta>`` and emit as markdown."""
    abstract = meta.find(f"{{{_JATS_NS}}}abstract")
    if abstract is None:
        abstract = meta.find("abstract")
    if abstract is None:
        return

    paragraphs: list[str] = []
    for child in abstract:
        if _strip_ns(child.tag) == "p":
            text = _itertext(child)
            if text:
                paragraphs.append(text)
    if paragraphs:
        parts.append("\n## Abstract\n")
        for p_text in paragraphs:
            parts.append(f"\n{p_text}\n")


def _emit_sec(section: ET.Element, parts: list[str], *, level: int) -> None:
    """Recursively emit a ``<sec>`` element as markdown headings + paragraphs."""
    title_elem = section.find(f"{{{_JATS_NS}}}title")
    if title_elem is None:
        title_elem = section.find("title")
    if title_elem is not None:
        title_text = _itertext(title_elem)
        if title_text:
            prefix = "#" * min(level, 4)
            parts.append(f"\n{prefix} {title_text}\n")

    for child in section:
        tag = _strip_ns(child.tag)
        if tag == "p":
            text = _itertext(child)
            if text:
                parts.append(f"\n{text}\n")
        elif tag == "sec":
            _emit_sec(child, parts, level=level + 1)
        elif tag in ("disp-formula", "table-wrap", "fig"):
            pass


# ===================================================================
# Utils
# ===================================================================


def _word_count(text: str) -> int:
    return len(normalize_text(text).split())
