"""ACS provider-owned browser-workflow rules."""

from __future__ import annotations

from functools import partial
from pathlib import Path
import re
from typing import Any, Mapping

from bs4 import BeautifulSoup, Tag

from ..extraction.html.parsing import choose_parser
from ..utils import normalize_text
from ._html_authors import (
    ATYPON_AUTHOR_NOISE_TEXT,
    AuthorExtractionPipeline,
    AuthorStep,
    extract_jsonld_authors,
    extract_meta_authors,
)
from ._html_references import extract_numbered_references_from_html


ACS_JSONLD_ARTICLE_TYPES = frozenset({"article", "scholarlyarticle", "newsarticle"})
# SITE_UI_COPY_REGRESSION_MARKER: ACS Publications article chrome selectors.
# STRUCTURAL_UI_COPY_HOOK: ACS provider cleanup policy removes these only from ACS article HTML.
ACS_DOM_CHROME_SELECTORS = (
    ".article__copy",
    ".article__cc-license",
    ".article__tags",
    ".articleHeaderHistoryDropzone",
    ".articleCitedByDropzone",
    ".TermsAndConditionsDropzone3",
    ".authorInformationSection",
    ".refs-header-label",
    ".references-count",
    "ol#references",
    "script",
)
# SITE_UI_COPY_REGRESSION_MARKER: ACS Publications copy-link chrome labels.
# STRUCTURAL_UI_COPY_HOOK: ACS provider cleanup policy removes these only after ACS markdown rendering.
ACS_MARKDOWN_CHROME_PATTERNS = (
    re.compile(
        r"\s*Click to copy article link\s+Article link copied!$",
        flags=re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"\s*Click to copy section link\s+Section link copied!",
        flags=re.IGNORECASE,
    ),
)
ACS_EMPTY_ABSTRACT_PAIR_PATTERN = re.compile(
    r"(## Abstract\n\n)(## Abstract\n\n)",
    flags=re.IGNORECASE,
)


def _extract_jsonld_authors(html_text: str) -> list[str]:
    return extract_jsonld_authors(
        html_text,
        article_types=ACS_JSONLD_ARTICLE_TYPES,
    )


_AUTHOR_PIPELINE = AuthorExtractionPipeline(
    AuthorStep(
        "meta",
        partial(extract_meta_authors, keys={"citation_author", "dc.creator"}),
    ),
    AuthorStep("jsonld", _extract_jsonld_authors),
)


def extract_authors(html_text: str) -> list[str]:
    return _AUTHOR_PIPELINE(html_text)


def _decompose_matching(container: Any, selectors: tuple[str, ...]) -> None:
    if not isinstance(container, Tag):
        return
    for selector in selectors:
        for node in list(container.select(selector)):
            node.decompose()


def acs_before_block_normalization(container: Any) -> None:
    from .atypon_browser_workflow.profile import (
        _drop_promotional_blocks,
        _promo_block_tokens,
    )

    _decompose_matching(container, ACS_DOM_CHROME_SELECTORS)
    _drop_promotional_blocks(container, promo_block_tokens=_promo_block_tokens("acs"))


def acs_body_container(container: Any) -> None:
    _decompose_matching(container, ACS_DOM_CHROME_SELECTORS)


def _clean_acs_markdown_chrome(markdown_text: str) -> str:
    text = markdown_text
    for pattern in ACS_MARKDOWN_CHROME_PATTERNS:
        text = pattern.sub("", text)
    text = ACS_EMPTY_ABSTRACT_PAIR_PATTERN.sub(r"\1", text)
    return text


def _clean_reference_text(node: Tag) -> str:
    citation = node.select_one(".NLM_citation") or node
    clone_soup = BeautifulSoup(str(citation), choose_parser())
    clone = clone_soup.find()
    if not isinstance(clone, Tag):
        return ""
    for selector in (
        ".casAbstract",
        ".casContent",
        ".casRecord",
        ".links-group",
        ".NLM_ref-label",
        ".refLabel",
        ".referenceLinks",
        ".references__suffix",
        ".google-scholar",
        ".ext-link",
        "a[href*='scholar.google']",
        "a[href*='getFTRLinkout']",
        "script",
    ):
        for match in list(clone.select(selector)):
            match.decompose()
    text = normalize_text(clone.get_text(" ", strip=True))
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"DOI:\s+", "DOI: ", text, flags=re.IGNORECASE)
    return normalize_text(text)


def _reference_year(text: str) -> str | None:
    match = re.search(r"\b((?:18|19|20)\d{2})\b", text)
    return match.group(1) if match else None


def _reference_doi(node: Tag) -> str | None:
    citation = node.select_one(".NLM_citation")
    doi = normalize_text(str(citation.get("data-doi") or "")) if isinstance(citation, Tag) else ""
    return doi or None


def extract_references(html_text: str) -> list[dict[str, str | None]]:
    if not normalize_text(html_text):
        return []
    soup = BeautifulSoup(html_text, choose_parser())
    nodes = [node for node in soup.select("ol#references > li") if isinstance(node, Tag)]
    if not nodes:
        return extract_numbered_references_from_html(html_text)

    references: list[dict[str, str | None]] = []
    for index, node in enumerate(nodes, start=1):
        raw = _clean_reference_text(node)
        if not raw:
            continue
        references.append(
            {
                "label": f"{index}.",
                "raw": raw,
                "doi": _reference_doi(node),
                "year": _reference_year(raw),
            }
        )
    return references


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
    markdown_text = _clean_acs_markdown_chrome(markdown_text)
    extracted_authors = extract_authors(html_text)
    if extracted_authors:
        finalized["extracted_authors"] = extracted_authors
    extracted_references = extract_references(html_text)
    if extracted_references:
        finalized["references"] = extracted_references
    return markdown_text, finalized


# ── Unicode sub/sup for chemical formulas ──
_SUB = str.maketrans({
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋",
})
_SUP = str.maketrans({
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻",
})


def _normalise_chem(text: str) -> str:
    """Convert residual <sub>/<sup> HTML to Unicode."""
    text = re.sub(r"<sub>([^<]*)</sub>", lambda m: m.group(1).translate(_SUB), text, flags=re.IGNORECASE)
    text = re.sub(r"<sup>([^<]*)</sup>", lambda m: m.group(1).translate(_SUP), text, flags=re.IGNORECASE)
    return text


def extract_body_markdown(body_container: Tag) -> str:
    """Extract structured markdown from an ACS article body container.

    ACS paragraphs are ``<div class='NLM_p'>``, not ``<p>``.
    Body sections are ``<div class='NLM_sec'>`` with h2/h3 headings.
    Stops at back-matter (Supporting Information / Acknowledgments / References).
    """
    BACK_MATTER = {
        "supporting information", "acknowledgments", "references",
        "author information", "author contributions", "cited by",
        "data availability", "terms & conditions", "associated content",
    }

    def _normalise(text: str) -> str:
        return normalize_text(text).lower().strip()

    def _is_back_matter(heading_text: str) -> bool:
        return _normalise(heading_text) in BACK_MATTER

    def _is_figure_label(el: Tag) -> bool:
        return "fig-label" in (el.get("class") or [])

    def _inside_figure(el: Tag) -> bool:
        return el.find_parent("figure") is not None

    def _is_nlm_paragraph(el: Tag) -> bool:
        """ACS uses <div class='NLM_p'> for body paragraphs."""
        if el.name != "div":
            return False
        cls = el.get("class") or []
        return "NLM_p" in cls

    # ── Scope to the full-text container only ──
    # Everything outside .hlFld-FullText is header/front-matter noise.
    fulltext = body_container.select_one(".hlFld-FullText")
    if fulltext is None:
        # Some ACS pages might not have the class — fall back to body_container
        fulltext = body_container

    items: list[tuple[str, str]] = []  # [("h2"|"h3"|"p"|"li"|"table", text)]
    seen_back_matter = False

    for el in fulltext.descendants:
        if seen_back_matter:
            break
        if not isinstance(el, Tag):
            continue

        tag = el.name.lower() if el.name else ""

        # Skip anything inside a <figure> (captions)
        if _inside_figure(el):
            continue

        # Skip chrome divs
        if tag == "div":
            cls = el.get("class") or []
            if any(c in ("article_content-header", "article__copy", "article__cc-license") for c in cls):
                continue

        # ── Headings ──
        if tag in ("h2", "h3", "h4"):
            if _is_figure_label(el):
                continue
            heading_text = el.get_text(strip=True)
            if not heading_text:
                continue
            if _is_back_matter(heading_text):
                seen_back_matter = True
                break
            level = int(tag[1])
            items.append((f"h{level}", heading_text))

        # ── ACS paragraphs (div.NLM_p) ──
        # Threshold >30 chars filters out "Click to copy" chrome fragments.
        elif _is_nlm_paragraph(el):
            text = normalize_text(el.get_text(" ", strip=True))
            if text and len(text) > 30:
                items.append(("p", text))

        # ── Regular <p> paragraphs (fallback for non-NLM_p content) ──
        # Threshold >40 chars filters out short chrome / figure-label runts.
        elif tag == "p":
            text = normalize_text(el.get_text(" ", strip=True))
            if text and len(text) > 40:
                items.append(("p", text))

        # ── List items ──
        elif tag == "li":
            if el.parent and el.parent.name in ("ul", "ol"):
                text = normalize_text(el.get_text(" ", strip=True))
                if text and len(text) > 15:
                    items.append(("li", text))

        # ── Tables ──
        elif tag == "table":
            text = normalize_text(el.get_text("\n", strip=True))
            if text and len(text) > 20:
                items.append(("table", text))

    # ── Build markdown ──
    _PREFIX: dict[str, str] = {"h2": "## ", "h3": "### ", "h4": "#### ", "li": "- "}
    lines: list[str] = []
    for kind, text in items:
        prefix = _PREFIX.get(kind)
        if prefix is not None:
            lines.append(f"{prefix}{text}")
        else:
            lines.append(text)
        lines.append("")

    return _normalise_chem("\n".join(lines))


def rewrite_image_urls_to_local(markdown_text: str, output_dir: str) -> str:
    """Rewrite ``![Figure N](CDN_url)`` → ``![Figure N](images/basename)``
    for images already downloaded to ``output_dir/images/``.
    """
    img_dir = Path(output_dir) / "images"
    img_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def _rewrite(match):
        alt_text, url = match.group(1), match.group(2)
        basename = url.rsplit("/", 1)[-1].split("?")[0]
        if (img_dir / basename).exists():
            return f"![{alt_text}](images/{basename})"
        return match.group(0)

    return img_pattern.sub(_rewrite, markdown_text)


def scoped_asset_extractor(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
    from .atypon_browser_workflow.asset_scopes import extract_scoped_html_assets

    return extract_scoped_html_assets(*args, **kwargs)


__all__ = [
    "ATYPON_AUTHOR_NOISE_TEXT",
    "extract_authors",
    "extract_references",
    "acs_before_block_normalization",
    "acs_body_container",
    "extract_body_markdown",
    "finalize_extraction",
    "scoped_asset_extractor",
]
