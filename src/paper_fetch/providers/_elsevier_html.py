"""ScienceDirect (Elsevier) provider-owned DOM extraction rules.

ScienceDirect renders full-text articles as server-side HTML inside ``#body``.
Paragraphs are ``<div class='u-margin-s-bottom'>`` (NOT ``<p>``).
Body sections are ``<section id='sec1'/'s0005'/…>`` with h2/h3 headings.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from bs4 import BeautifulSoup, Tag

from ..extraction.html.parsing import choose_parser
from ..utils import normalize_text
from ._html_authors import (
    AuthorExtractionPipeline,
    AuthorStep,
    extract_jsonld_authors,
    extract_meta_authors,
)
from ._html_references import extract_numbered_references_from_html


# ── Back-matter headings: stop extraction when any h2 matches ──
_BACK_MATTER = {
    "declaration of competing interest",
    "declaration of competing interests",
    "credit authorship contribution statement",
    "acknowledgements",
    "acknowledgments",
    "funding",
    "appendix a. supplementary data",
    "appendix. supplementary data",
    "supplementary data",
    "references",
    "data availability",
    "author contributions",
    "additional information",
}

# ── Section heading tags ──
_HEADING_TAGS = frozenset({"h2", "h3", "h4"})


def _normalise(text: str) -> str:
    return normalize_text(text).lower().strip()


def _is_back_matter(heading_text: str) -> bool:
    return _normalise(heading_text) in _BACK_MATTER


def _resolve_level(tag_name: str) -> int:
    """h2 → 2, h3 → 3, h4 → 4."""
    return int(tag_name[1])


def _clean_citation_text(text: str) -> str:
    """Collapse whitespace around citation markers like ', , , ,'."""
    return re.sub(r"\s*,\s*(,\s*)+", ", ", text)


def extract_body_markdown(body_container: Tag) -> str:
    """Extract structured markdown from a ScienceDirect ``#body`` container.

    Returns markdown with:
    - ``## Section`` / ``### Sub-section`` headings
    - Paragraph text from ``div.u-margin-s-bottom``
    - ``![Figure N](CDN_url)`` placeholders for images
    - ``> Figure N: caption`` for figure captions
    """
    # ── Scope to #body to exclude sidebar/outline ──
    fulltext = body_container.select_one("#body")
    if fulltext is None:
        fulltext = body_container

    items: list[tuple[str, str]] = []  # [("h2"|"h3"|"p"|"fig"|"table", text)]
    seen_back_matter = False

    # Walk #body children in document order
    for el in fulltext.descendants:
        if seen_back_matter:
            break
        if not isinstance(el, Tag):
            continue

        tag = el.name.lower() if el.name else ""

        # ── Headings ──
        if tag in _HEADING_TAGS:
            heading_text = el.get_text(strip=True)
            if not heading_text:
                continue
            if _is_back_matter(heading_text):
                seen_back_matter = True
                break
            level = _resolve_level(tag)
            items.append((f"h{level}", heading_text))

        # ── Paragraphs (div.u-margin-s-bottom) ──
        elif tag == "div" and "u-margin-s-bottom" in (el.get("class") or []):
            # Skip if inside a figure caption area
            if el.find_parent("figure") is not None:
                continue
            # Skip if this is an abstract paragraph (already handled separately)
            pid = el.get("id", "")
            if pid.startswith("abspara"):
                continue
            text = normalize_text(el.get_text(" ", strip=True))
            text = _clean_citation_text(text)
            if text and len(text) > 40:
                items.append(("p", text))

        # ── Figures ──
        elif tag == "figure":
            # Find image URL — prefer the <img> src
            img = el.find("img")
            img_url = img.get("src", "") if img else ""
            if not img_url:
                continue

            # Find caption text from <span class="captions">
            caption_el = el.select_one("span.captions")
            caption_text = ""
            if caption_el:
                caption_text = normalize_text(caption_el.get_text(" ", strip=True))
                # Remove download link noise
                caption_text = re.sub(
                    r"Download\s*:\s*Download[^.]+\.\s*",
                    "",
                    caption_text,
                    flags=re.IGNORECASE,
                ).strip()

            if img_url:
                items.append(("fig_img", img_url))
            if caption_text:
                items.append(("fig_cap", caption_text))

        # ── Tables ──
        elif tag == "table":
            # Extract table as structured text
            rows: list[str] = []
            for tr in el.find_all("tr"):
                cells = [
                    normalize_text(cell.get_text(" ", strip=True))
                    for cell in tr.find_all(["th", "td"])
                ]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                items.append(("table", "\n".join(rows)))

        # ── Lists ──
        elif tag == "li":
            parent = el.parent
            if parent and parent.name in ("ul", "ol"):
                text = normalize_text(el.get_text(" ", strip=True))
                if text and len(text) > 20:
                    items.append(("li", text))

    # ── Build markdown ──
    _PREFIX: dict[str, str] = {
        "h2": "## ",
        "h3": "### ",
        "h4": "#### ",
        "li": "- ",
    }
    lines: list[str] = []
    for kind, text in items:
        if kind == "fig_img":
            basename = text.rsplit("/", 1)[-1].split("?")[0]
            lines.append(f"![Figure]({text})")
        elif kind == "fig_cap":
            lines.append(f"> {text}")
            lines.append("")
        elif kind == "table":
            lines.append(text)
            lines.append("")
        elif kind in _PREFIX:
            lines.append(f"{_PREFIX[kind]}{text}")
            lines.append("")
        else:  # "p"
            lines.append(text)
            lines.append("")

    return "\n".join(lines)


def rewrite_image_urls_to_local(markdown_text: str, output_dir: str) -> str:
    """Rewrite ``![Figure](CDN_url)`` → ``![Figure](images/basename)``
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


# ── Author extraction ──
_AUTHOR_PIPELINE = AuthorExtractionPipeline(
    AuthorStep(
        "meta",
        lambda html: extract_meta_authors(
            html, keys={"citation_author", "dc.creator"}
        ),
    ),
    AuthorStep("jsonld", lambda html: extract_jsonld_authors(html)),
)


def extract_authors(html_text: str) -> list[str]:
    return _AUTHOR_PIPELINE(html_text)


def extract_references(html_text: str) -> list[dict[str, str | None]]:
    if not normalize_text(html_text):
        return []
    soup = BeautifulSoup(html_text, choose_parser())
    refs: list[dict[str, str | None]] = []
    for i, li in enumerate(
        soup.select("ol.references li, #references li, .ref-list li, ol.bibliography li"), start=1
    ):
        text = normalize_text(li.get_text(" ", strip=True))
        if text:
            refs.append({"label": f"{i}.", "raw": text, "doi": None, "year": None})
    if not refs:
        return extract_numbered_references_from_html(html_text)
    return refs


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
    extracted_references = extract_references(html_text)
    if extracted_references:
        finalized["references"] = extracted_references
    return markdown_text, finalized


__all__ = [
    "extract_body_markdown",
    "extract_authors",
    "extract_references",
    "finalize_extraction",
    "rewrite_image_urls_to_local",
]
