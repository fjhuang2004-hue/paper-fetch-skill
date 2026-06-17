"""PNAS (pnas.org) DOM extractor.

PNAS uses its own custom platform (NOT Atypon).  Key structure:

  - Full-text container: ``#bodymatter [property="articleBody"]``
  - Sections:           ``<section id="sec-N">`` and ``<section id="sec-N-M">``
  - Headings:           h2 (top-level) / h3 (subsection) inside sections
  - Paragraphs:         plain ``<div>`` (NOT ``<p>``!)
  - Figures:            ``div.figure-wrap`` → ``figure.graphic`` → ``<img>`` + ``<figcaption>``
  - Table images:       ``figure.table`` → ``<img>`` (e.g. Table 1 as inline GIF)
  - Back-matter:        Materials and Methods / Data, Materials, and Software
                        Availability / Acknowledgments / Author contributions /
                        Competing interests / Supporting Information / References
  - Abstract:           ``<section id="abstract">`` (outside #bodymatter)
  - Significance:       ``<section id="executive-summary-abstract">`` (outside #bodymatter)
"""

from __future__ import annotations

import re
from pathlib import Path
from bs4 import Tag

from ..utils import normalize_text

_PNAS_IMG_BASE = "https://www.pnas.org"

_BACK_MATTER_KEYWORDS = [
    "materials and methods",
    "experimental procedures",
    "data, materials, and software availability",
    "data availability",
    "acknowledgments",
    "acknowledgements",
    "author contributions",
    "author contribution",
    "competing interests",
    "conflict of interest",
    "declaration of interests",
    "supporting information",
    "supplementary material",
    "supplementary data",
    "references",
    "footnotes",
    "notes",
]

# PNAS inline citation patterns: superscript numbers like ¹ or <sup>1,2,5-7</sup>
_CITATION_RE = re.compile(r'[¹²³⁰-⁹⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ]+')


def _is_back_matter(text: str) -> bool:
    """Check if a heading text signals back-matter (stop extraction)."""
    lowered = text.lower().strip().rstrip(".")
    for keyword in _BACK_MATTER_KEYWORDS:
        if lowered == keyword or lowered.startswith(keyword):
            return True
    return False


def extract_significance(soup: Tag | None) -> str:
    """Extract the Significance statement from PNAS article."""
    if soup is None:
        return ""
    sig_section = soup.select_one("#executive-summary-abstract")
    if sig_section is None:
        return ""
    # Remove the heading itself
    heading = sig_section.find(["h2", "h3"])
    if heading and isinstance(heading, Tag):
        heading.decompose()
    return normalize_text(sig_section.get_text(" ", strip=True))


def extract_abstract(soup: Tag | None) -> str:
    """Extract abstract text from the PNAS article page."""
    if soup is None:
        return ""
    abstract_sec = soup.select_one("#abstract")
    if abstract_sec is None:
        return ""
    # Remove the heading itself
    heading = abstract_sec.find(["h2", "h3"])
    if heading and isinstance(heading, Tag):
        heading.decompose()
    return normalize_text(abstract_sec.get_text(" ", strip=True))


def extract_body_markdown(soup_or_elem: Tag | None) -> str:
    """Walk PNAS ``#bodymatter`` sections and return markdown.

    Parameters
    ----------
    soup_or_elem:
        Full-page BeautifulSoup object OR the ``#bodymatter`` element itself.

    Returns
    -------
    Markdown string with headings, paragraphs, figures and tables.
    """
    if soup_or_elem is None:
        return ""

    # Accept either a full soup or the #bodymatter element directly.
    # Check the direct-element case first — Tag.select_one('#bodymatter')
    # looks for a CHILD with that id, not the element itself.
    if isinstance(soup_or_elem, Tag) and soup_or_elem.get("id") == "bodymatter":
        bodymatter = soup_or_elem
    elif hasattr(soup_or_elem, "select_one"):
        bodymatter = soup_or_elem.select_one("#bodymatter")
    else:
        bodymatter = None
    if bodymatter is None:
        return ""

    # bodymatter may wrap sections in a div.core-container
    content_root = bodymatter.select_one(".core-container") or bodymatter

    items: list[tuple[str, str]] = []
    seen_back_matter = False

    # Walk top-level sections (not nested inside another section)
    for section in content_root.find_all("section", recursive=False):
        if seen_back_matter:
            break
        if not isinstance(section, Tag):
            continue

        # Check section heading for back-matter
        heading = section.find(["h2", "h3"])
        if heading and isinstance(heading, Tag):
            heading_text = heading.get_text(" ", strip=True)
            if _is_back_matter(heading_text):
                seen_back_matter = True
                break

        # Process all content inside this section
        _walk_section(section, items)

    return _assemble_markdown(items)


def _walk_section(section: Tag, items: list[tuple[str, str]]) -> None:
    """Walk a section element and collect markdown items."""
    for el in section.children:
        if not isinstance(el, Tag):
            continue

        tag = el.name.lower() if el.name else ""

        # ── Headings ──
        if tag in ("h2", "h3", "h4"):
            heading_text = el.get_text(" ", strip=True)
            if not heading_text:
                continue
            if _is_back_matter(heading_text):
                continue
            level = int(tag[1])
            items.append((f"h{level}", heading_text))

        # ── Figures ──
        elif tag == "div" and "figure-wrap" in (el.get("class") or []):
            figure = el.find("figure")
            if figure and isinstance(figure, Tag):
                img = figure.find("img")
                if img is None:
                    continue
                img_url = img.get("src", "")
                if not img_url:
                    continue
                if img_url.startswith("/"):
                    img_url = _PNAS_IMG_BASE + img_url
                items.append(("fig_img", img_url))

                figcaption = figure.find("figcaption")
                if figcaption and isinstance(figcaption, Tag):
                    caption_text = normalize_text(figcaption.get_text(" ", strip=True))
                    if caption_text:
                        items.append(("fig_cap", caption_text))

        # ── Paragraphs (plain <div> without class, not figure-wrap) ──
        elif tag == "div":
            classes = el.get("class") or []
            if classes:
                continue  # skip styled divs (we already handled figure-wrap)
            # Skip if contains only figures
            if el.find("figure") is not None:
                continue
            text = normalize_text(el.get_text(" ", strip=True))
            if not text:
                continue
            if len(text) < 40:
                continue
            items.append(("p", text))

        # ── Regular paragraphs (if any) ──
        elif tag == "p":
            if el.find_parent("figure") is not None:
                continue
            if el.find_parent("table") is not None:
                continue
            if el.find_parent(["ul", "ol"]) is not None:
                continue
            text = normalize_text(el.get_text(" ", strip=True))
            if not text:
                continue
            if len(text) < 40:
                continue
            items.append(("p", text))

        # ── Tables ──
        elif tag == "table":
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

        # ── Lists ──
        elif tag in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                if not isinstance(li, Tag):
                    continue
                text = normalize_text(li.get_text(" ", strip=True))
                if text and len(text) > 20:
                    items.append(("li", text))

        # ── Nested sections (sec-N-M) ──
        elif tag == "section":
            # Check subsection heading for back-matter
            sub_heading = el.find(["h2", "h3"])
            if sub_heading and isinstance(sub_heading, Tag):
                sub_text = sub_heading.get_text(" ", strip=True)
                if _is_back_matter(sub_text):
                    continue
            _walk_section(el, items)


def _table_to_markdown(rows: list[list[str]]) -> str:
    """Convert a list of row-lists to a GitHub-flavoured markdown table."""
    if not rows:
        return ""
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
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            lines.append(f"\n![]({text})\n")
        elif kind == "fig_cap":
            if lines and lines[-1].startswith("![]("):
                lines.append(f"\n**Figure.** {text}\n")
            else:
                pending_caption = text
        elif kind.startswith("h"):
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
            if pending_caption:
                lines.append(f"\n**Figure.** {pending_caption}\n")
                pending_caption = None
            lines.append(f"\n{text}\n")

    if pending_caption:
        lines.append(f"\n**Figure.** {pending_caption}\n")

    return "\n".join(lines)


def rewrite_image_urls_to_local(markdown_text: str, output_dir: str) -> str:
    """Rewrite ``![]()`` image URLs to ``images/basename`` for local files."""
    img_dir = Path(output_dir) / "images"
    img_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def _rewrite(match):
        alt_text, url = match.group(1), match.group(2)
        basename = url.rsplit("/", 1)[-1].split("?")[0]
        if (img_dir / basename).exists():
            return f"![{alt_text}](images/{basename})"
        return match.group(0)

    return img_pattern.sub(_rewrite, markdown_text)
