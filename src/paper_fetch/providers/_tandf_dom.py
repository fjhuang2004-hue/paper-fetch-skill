"""Taylor & Francis (tandfonline.com) DOM extractor.

T&F is Atypon-based, similar to ACS.  Key structure:

  - Full-text container: ``div.hlFld-Fulltext``
  - Sections:           ``div.NLM_sec.NLM_sec_level_N``
  - Headings:           h2.section-heading-2 / h3.section-heading-3 / h4.section-heading-4
  - Paragraphs:         plain ``<p>`` (sometimes ``<p class="last">``)
  - Figures:            ``div.figureView`` → ``<img>`` + ``<p class="captionText">``
  - Tables:             ``<table>`` inside NLM_sec
  - Back-matter:        Disclosure / Funding / Acknowledgments / References /
                        Author Contributions / Data Availability / Supplementary
  - Abstract:           ``div.hlFld-Abstract``
"""

from __future__ import annotations

import re
from pathlib import Path
from bs4 import Tag

from ..utils import normalize_text

# Remove inline citation markers like "[ Citation 1 ]", "[ Citation 6–8 ]",
# "[ Citation 2 , Citation 5 ]".
_CITATION_RE = re.compile(r'\s*\[\s*Citation[^\]]+\]')


def _clean_citations(text: str) -> str:
    """Strip ``[ Citation N ]`` markers from paragraph text."""
    return _CITATION_RE.sub("", text).strip()


_BACK_MATTER_KEYWORDS = [
    "acknowledgment",
    "acknowledgements",
    "acknowledgments",
    "author contribution",
    "author contributions",
    "conflict of interest",
    "conflicts of interest",
    "declaration of conflicting interests",
    "declaration of interest",
    "disclosure statement",
    "disclosure of interest",
    "data availability",
    "data availability statement",
    "funding",
    "references",
    "supplementary material",
    "supplementary data",
    "supporting information",
    "additional information",
    "notes",
    "footnotes",
]

# T&F CDN image base
_TANDF_IMG_BASE = "https://www.tandfonline.com"


def _is_back_matter(text: str) -> bool:
    """Check if a heading text signals back-matter (stop extraction)."""
    lowered = text.lower().strip()
    for keyword in _BACK_MATTER_KEYWORDS:
        if lowered == keyword or lowered.startswith(keyword):
            return True
    return False


def extract_body_markdown(body_container: Tag | None) -> str:
    """Walk T&F's ``div.hlFld-Fulltext`` and return markdown.

    Parameters
    ----------
    body_container:
        The pre-cleaned article body element, already stripped of
        abstract via the existing ``tandf_body_container()`` hook.

    Returns
    -------
    Markdown string with headings, paragraphs, figures and tables.
    """
    if body_container is None:
        return ""

    fulltext = body_container.select_one(".hlFld-Fulltext")
    if fulltext is None:
        fulltext = body_container

    items: list[tuple[str, str]] = []
    seen_back_matter = False

    for el in fulltext.descendants:
        if seen_back_matter:
            break
        if not isinstance(el, Tag):
            continue

        # Skip abstract and keyword blocks (handled separately)
        if el.find_parent(class_="hlFld-Abstract") is not None:
            continue
        if el.find_parent(class_="hlFld-KeywordText") is not None:
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
            # Skip captions (handled with figures)
            classes = el.get("class") or []
            if "captionText" in classes:
                continue
            if "kwd-title" in classes:
                continue
            if "csl-response" in classes:
                continue
            # Skip if inside figure / table / list
            if el.find_parent("figure") is not None:
                continue
            if el.find_parent("table") is not None:
                continue
            if el.find_parent(["ul", "ol"]) is not None:
                continue
            if el.find_parent(class_="figureView") is not None:
                continue
            text = normalize_text(el.get_text(" ", strip=True))
            text = _clean_citations(text)
            if not text:
                continue
            if len(text) < 40:
                continue
            items.append(("p", text))

        # ── Figures ──
        elif tag == "div" and "figureView" in (el.get("class") or []):
            img = el.find("img")
            if img is None:
                continue
            img_url = img.get("src", "")
            if not img_url:
                continue
            # Resolve relative URLs
            if img_url.startswith("/"):
                img_url = _TANDF_IMG_BASE + img_url
            items.append(("fig_img", img_url))

            caption_el = el.find("p", class_="captionText")
            if caption_el and isinstance(caption_el, Tag):
                caption_text = normalize_text(caption_el.get_text(" ", strip=True))
                if caption_text:
                    items.append(("fig_cap", caption_text))

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

        # ── List items ──
        elif tag == "li":
            parent = el.parent
            if parent and isinstance(parent, Tag) and parent.name in ("ul", "ol"):
                grandparent = parent.parent
                if grandparent and isinstance(grandparent, Tag) and grandparent.name in ("li",):
                    continue
                text = normalize_text(el.get_text(" ", strip=True))
                if text and len(text) > 20:
                    items.append(("li", text))

    return _assemble_markdown(items)


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


def extract_abstract(body_container: Tag | None) -> str:
    """Extract abstract text from the T&F article page."""
    if body_container is None:
        return ""
    abstract_el = body_container.select_one(".hlFld-Abstract")
    if abstract_el is None:
        return ""
    heading = abstract_el.find(["h2", "h3"])
    if heading and isinstance(heading, Tag):
        heading_text = heading.get_text(" ", strip=True).lower()
        if heading_text == "abstract":
            heading.decompose()
    return _clean_citations(normalize_text(abstract_el.get_text(" ", strip=True)))


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
