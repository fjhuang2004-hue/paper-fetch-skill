"""Wiley Online Library DOM extractor.

Wiley's HTML structure (onlinelibrary.wiley.com):
  - Body container: ``section.article-section__full``
  - Paragraphs:    plain ``<p>`` tags (no special class)
  - Headings:      h2.article-section__title, h3.article-section__sub-title, h4.section3
  - Figures:       ``<figure class="figure">`` → ``<img src="/cms/asset/UUID/file.png">``
                   (relative URL — rewritten to absolute onlinelibrary.wiley.com)
  - Captions:      ``<figcaption class="figure__caption">`` with noise prefix
                   "FIGURE X Open in figure viewer PowerPoint"
  - Tables:        ``<table class="table article-section__table pgwide">``
  - Back-matter:   Acknowledgments, Author Contributions, Conflict of Interest,
                   Data Availability, Supporting Information
"""

from __future__ import annotations

import re
from pathlib import Path
from bs4 import Tag

from ..utils import normalize_text

_FIGURE_LABEL_RE = re.compile(r"^(?:FIGURE|Fig\.?)\s*\d+\s*", re.IGNORECASE)
_FIGURE_NOISE_PREFIX_RE = re.compile(
    r"^(?:FIGURE|Fig\.?)\s*\d+\s*Open in figure viewer\s*PowerPoint\s*",
    re.IGNORECASE,
)
_FIGURE_NOISE_SUFFIX = "Open in figure viewer PowerPoint"

_BACK_MATTER_KEYWORDS = [
    "acknowledgment",
    "acknowledgements",
    "acknowledgments",
    "author contribution",
    "author contributions",
    "conflict of interest",
    "conflicts of interest",
    "conflict of interest statement",
    "data availability",
    "data availability statement",
    "supporting information",
]

_WILEY_CDN_BASE = "https://onlinelibrary.wiley.com"


def _is_back_matter(text: str) -> bool:
    """Check if a heading text signals back-matter (stop extraction)."""
    lowered = text.lower().strip()
    for keyword in _BACK_MATTER_KEYWORDS:
        if lowered == keyword or lowered.startswith(keyword):
            return True
    return False


def _clean_caption(text: str) -> str:
    """Remove Wiley figure caption noise.

    'FIGURE 1 Open in figure viewer PowerPoint (a) Tβ CD spectra...'
    → '(a) Tβ CD spectra...'

    'Figure 2 Open in figure viewer PowerPoint Results of...'
    → 'Results of...'
    """
    cleaned = _FIGURE_NOISE_PREFIX_RE.sub("", text).strip()
    # If the regex didn't match, try suffix removal
    if _FIGURE_NOISE_SUFFIX.lower() in cleaned.lower():
        idx = cleaned.lower().find(_FIGURE_NOISE_SUFFIX.lower())
        cleaned = cleaned[idx + len(_FIGURE_NOISE_SUFFIX):].strip()
    # Remove residual "FIGURE X" label if still at start
    cleaned = _FIGURE_LABEL_RE.sub("", cleaned).strip()
    return cleaned


def extract_body_markdown(body_container: Tag | None) -> str:
    """Walk Wiley's ``section.article-section__full`` and return markdown.

    Parameters
    ----------
    body_container:
        The container element (as passed by ``markdown.py``, already
        cleaned of abstract via ``wiley_body_container()`` hook).

    Returns
    -------
    Markdown string with headings, paragraphs, figures and tables.
    """
    if body_container is None:
        return ""

    fulltext = body_container.select_one("section.article-section__full")
    if fulltext is None:
        # Fallback: use the container itself
        fulltext = body_container

    items: list[tuple[str, str]] = []
    seen_back_matter = False

    for el in fulltext.descendants:
        if seen_back_matter:
            break
        if not isinstance(el, Tag):
            continue

        tag = el.name.lower() if el.name else ""

        # ── Headings ──
        if tag in ("h2", "h3", "h4"):
            heading_text = el.get_text(" ", strip=True)
            if not heading_text:
                continue
            if _is_back_matter(heading_text):
                seen_back_matter = True
                break
            level = int(tag[1])
            items.append((f"h{level}", heading_text))

        # ── Paragraphs ──
        elif tag == "p":
            # Skip if inside a figure / table / list — handled separately
            if el.find_parent("figure") is not None:
                continue
            if el.find_parent("table") is not None:
                continue
            if el.find_parent(["ul", "ol"]) is not None:
                continue
            # Skip paragraphs that only contain math images (no actual text)
            text = normalize_text(el.get_text(" ", strip=True))
            if not text:
                continue
            # Skip short noise fragments (author affiliations, etc.)
            if len(text) < 40:
                continue
            items.append(("p", text))

        # ── Figures ──
        elif tag == "figure":
            img = el.find("img")
            if img is None:
                continue
            img_url = img.get("src", "")
            if not img_url:
                continue
            # Resolve relative Wiley CDN URLs to absolute
            if img_url.startswith("/cms/asset/") or img_url.startswith("/cms/"):
                img_url = _WILEY_CDN_BASE + img_url
            items.append(("fig_img", img_url))

            caption_el = el.find("figcaption")
            if caption_el and isinstance(caption_el, Tag):
                caption_text = normalize_text(caption_el.get_text(" ", strip=True))
                caption_text = _clean_caption(caption_text)
                if caption_text:
                    items.append(("fig_cap", caption_text))

        # ── Tables ──
        elif tag == "table":
            # Skip support-info tables
            table_classes = " ".join(el.get("class") or [])
            if "support-info" in table_classes:
                continue
            rows = []
            for tr in el.find_all("tr"):
                if not isinstance(tr, Tag):
                    continue
                cells = []
                for cell in tr.find_all(["th", "td"]):
                    if isinstance(cell, Tag):
                        cells.append(normalize_text(cell.get_text(" ", strip=True)))
                if cells and any(c for c in cells):
                    rows.append(cells)
            if rows:
                items.append(("table", _table_to_markdown(rows)))

        # ── List items ──
        elif tag == "li":
            # Only top-level li (not nested inside another li)
            parent = el.parent
            if parent and isinstance(parent, Tag) and parent.name in ("ul", "ol"):
                # Check we're not inside another li
                grandparent = parent.parent
                if grandparent and isinstance(grandparent, Tag) and grandparent.name in ("li",):
                    continue
                text = normalize_text(el.get_text(" ", strip=True))
                if text and len(text) > 20:
                    items.append(("li", text))

    # ── Assemble markdown ──
    return _assemble_markdown(items)


def _table_to_markdown(rows: list[list[str]]) -> str:
    """Convert a list of row-lists to a GitHub-flavoured markdown table."""
    if not rows:
        return ""
    # Normalize column count
    max_cols = max(len(r) for r in rows)
    padded = [r + [""] * (max_cols - len(r)) for r in rows]

    lines: list[str] = []
    lines.append("| " + " | ".join(padded[0]) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _assemble_markdown(items: list[tuple[str, str]]) -> str:
    """Convert extracted items into markdown string."""
    lines: list[str] = []
    pending_caption: str | None = None

    for kind, text in items:
        if kind == "fig_img":
            # Flush any pending caption that was orphaned
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            lines.append(f"\n![]({text})\n")
        elif kind == "fig_cap":
            # Caption follows its image — hold it
            if lines and lines[-1].startswith("![]("):
                lines.append(f"\n**Figure.** {text}\n")
            else:
                pending_caption = text
        elif kind.startswith("h"):
            # Flush pending caption before heading
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            level = int(kind[1])
            prefix = "#" * level
            lines.append(f"\n{prefix} {text}\n")
        elif kind == "table":
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            lines.append(f"\n{text}\n")
        elif kind == "li":
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            lines.append(f"- {text}")
        else:
            # paragraph
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            lines.append(f"\n{text}\n")

    # Final flush
    if pending_caption:
        lines.append(f"\n**Figure.** {pending_caption}\n")

    return "\n".join(lines)


def extract_abstract(body_container: Tag | None) -> str:
    """Extract abstract text from the Wiley article page."""
    if body_container is None:
        return ""
    abstract_el = body_container.select_one("section.article-section__abstract")
    if abstract_el is None:
        # Try finding by heading
        for h in body_container.find_all(["h2", "h3"]):
            if h.get_text(" ", strip=True).lower().startswith("abstract"):
                parent = h.find_parent("section") or h.parent
                abstract_el = parent
                break
    if abstract_el is None:
        return ""
    # Remove the "Abstract" heading itself
    heading = abstract_el.find(["h2", "h3"])
    if heading and isinstance(heading, Tag):
        heading_text = heading.get_text(" ", strip=True).lower()
        if heading_text == "abstract":
            heading.decompose()
    return normalize_text(abstract_el.get_text(" ", strip=True))


def rewrite_image_urls_to_local(markdown_text: str, output_dir: str) -> str:
    """Rewrite ``![]()`` image URLs in markdown to ``images/basename``
    for images already downloaded to ``output_dir/images/``.

    Works with any image URL — replaces absolute CDN/HTTP URLs
    with relative ``images/<basename>`` when the file exists locally.
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
