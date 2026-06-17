"""ASM (American Society for Microbiology) DOM extractor.

ASM platform: journals.asm.org (Atypon Literatum with pb frontend).
Full-text HTML lives inside ``<article>`` → ``#bodymatter``.

Structure::

    <article typeof="ScholarlyArticle">
      <section id="primary-abstract">       ← abstract
        <h2>ABSTRACT</h2>
        <div>...abstract text...</div>
        <section id="abs-sec-1">            ← IMPORTANCE / significance
      <section id="bodymatter">             ← body root
        <section id="sec-1" data-type="intro">
          <h2>INTRODUCTION</h2>
          <div>...paragraph...</div>        ← paragraphs are <div>, NOT <p>!
          <div>...paragraph...</div>
          <section id="sec-1-1">            ← nested subsection
            <h3>...</h3>
            <div>...</div>
          <div class="figure-wrap">         ← figure wrapper
            <figure class="graphic" id="F1">
              <img data-viewer-src=".../large/..." src=".../medium/...">
              <figcaption>...</figcaption>
          <div class="table-wrap">          ← table wrapper
            <table>...</table>
        <section id="acknowledgments">       ← back-matter cutoff
        <section id="bibliography">          ← references

Key differences from classic Atypon (ACS/T&F):
- Paragraphs use ``<div>`` without class, not ``<p>`` or ``div.NLM_p``.
- Figures wrap in ``div.figure-wrap``, not ``div.figureView``.
- Hi-res image URL is in ``data-viewer-src`` attribute.
"""

from __future__ import annotations

import re
from pathlib import Path
from bs4 import Tag

from ..utils import normalize_text

# Image base URL — relative paths like /cms/... need this prefix.
_ASM_IMG_BASE = "https://journals.asm.org"

# Section ids that signal back-matter — stop extraction when encountered.
_BACK_MATTER_IDS: set[str] = {
    "acknowledgments",
    "data-availability",
    "bibliography",
    "supplementary-materials",
    "supplementary-material",
    "footnotes",
    "footnote",
    "appendices",
    "appendix",
}

# Section-level tags that should be skipped entirely (chrome / sidebar).
_SKIP_TAGS: set[str] = {"nav", "header", "footer"}

# Minimum character count for a body paragraph.
_MIN_PARAGRAPH_CHARS = 40


# ── helpers ───────────────────────────────────────────────────────────


def _is_back_matter_section(section: Tag) -> bool:
    """Check if a ``<section>`` signals back-matter by its ``id`` or heading text."""
    sid = (section.get("id") or "").lower().strip()
    if sid in _BACK_MATTER_IDS:
        return True
    # Also check heading text as a fallback.
    heading = section.find(["h2", "h3", "h4"])
    if heading and isinstance(heading, Tag):
        text = normalize_text(heading.get_text(" ", strip=True)).lower()
        if text in {"acknowledgments", "acknowledgements", "references",
                     "supplementary material", "supplementary materials",
                     "data availability", "data availability statement",
                     "author contributions", "conflicts of interest",
                     "conflict of interest", "footnotes", "footnote",
                     "appendices", "appendix"}:
            return True
    return False


def _is_skip_tag(el: Tag) -> bool:
    """Return True if *el* should be skipped (nav / header / footer)."""
    return el.name.lower() in _SKIP_TAGS if el.name else False


def _extract_heading(el: Tag) -> str | None:
    """Extract heading text from h2/h3/h4, skipping 'ABSTRACT'."""
    text = normalize_text(el.get_text(" ", strip=True))
    if not text:
        return None
    if text.lower() == "abstract":
        return None
    return text


# ── figure extraction ─────────────────────────────────────────────────


def _extract_figure(figure_wrap: Tag) -> tuple[str, str] | None:
    """Extract image URL and caption from ``div.figure-wrap``.

    Prefers ``data-viewer-src`` (hi-res large image) over ``src`` (medium).
    Returns ``(img_url, caption)`` or ``None``.
    """
    figure = figure_wrap.find("figure")
    if not isinstance(figure, Tag):
        return None

    img = figure.find("img")
    if not isinstance(img, Tag):
        return None

    # Prefer data-viewer-src (large) over src (medium).
    img_src = (img.get("data-viewer-src") or img.get("src") or "").strip()
    if not img_src:
        return None

    if img_src.startswith("/"):
        img_src = _ASM_IMG_BASE + img_src

    # Caption from <figcaption>, stripping the repeated label (e.g. "Fig 1").
    caption = ""
    figcaption = figure.find("figcaption")
    if isinstance(figcaption, Tag):
        # Remove heading span to avoid duplication.
        heading_span = figcaption.find("span", class_="heading")
        if isinstance(heading_span, Tag):
            heading_span.decompose()
        caption = normalize_text(figcaption.get_text(" ", strip=True))

    return img_src, caption


# ── table extraction ──────────────────────────────────────────────────


def _extract_table_rows(table: Tag) -> list[list[str]]:
    """Extract rows from a ``<table>``, skipping empty rows."""
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        cells: list[str] = []
        for cell in tr.find_all(["th", "td"]):
            if isinstance(cell, Tag):
                cells.append(normalize_text(cell.get_text(" ", strip=True)))
        if cells and any(c for c in cells):
            rows.append(cells)
    return rows


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


# ── recursive section walker ──────────────────────────────────────────


def _walk_section(section: Tag, items: list[tuple[str, str]],
                  seen_back_matter: bool = False) -> bool:
    """Recursively walk a ``<section>``'s direct children.

    Emits ``(kind, text)`` tuples into *items*.  Returns ``True`` if a
    back-matter section was encountered (caller should stop).
    """
    if seen_back_matter:
        return True

    for child in section.children:
        if not isinstance(child, Tag):
            continue
        if _is_skip_tag(child):
            continue

        tag = child.name.lower() if child.name else ""

        # ── Headings ──
        if tag in ("h2", "h3", "h4"):
            heading = _extract_heading(child)
            if heading:
                level = int(tag[1])
                items.append((f"h{level}", heading))

        # ── Nested section ──
        elif tag == "section":
            if _is_back_matter_section(child):
                return True
            if _walk_section(child, items, seen_back_matter):
                return True

        # ── Figure ──
        elif tag == "div" and "figure-wrap" in (child.get("class") or []):
            result = _extract_figure(child)
            if result:
                img_src, caption = result
                items.append(("fig_img", img_src))
                if caption:
                    items.append(("fig_cap", caption))
            else:
                # Figure contains no <img> — could be a table wrapped in a
                # figure (older ASM articles).  Check for a nested table.
                table = child.select_one(".table-wrap table")
                if isinstance(table, Tag):
                    rows = _extract_table_rows(table)
                    if rows:
                        items.append(("table", _table_to_markdown(rows)))
                        # Also capture the figcaption as a label.
                        figcaption = child.select_one("figcaption")
                        if isinstance(figcaption, Tag):
                            cap = normalize_text(
                                figcaption.get_text(" ", strip=True)
                            )
                            if cap:
                                items.append(("p", cap))

        # ── Table ──
        elif tag == "div" and "table-wrap" in (child.get("class") or []):
            table = child.find("table")
            if isinstance(table, Tag):
                rows = _extract_table_rows(table)
                if rows:
                    items.append(("table", _table_to_markdown(rows)))

        # ── Body paragraph (plain <div>) ──
        elif tag == "div":
            classes = child.get("class") or []
            # Skip known non-paragraph divs.
            if any(c in {"figure-wrap", "table-wrap", "label", "heading"}
                   for c in classes):
                continue
            text = normalize_text(child.get_text(" ", strip=True))
            if text and len(text) >= _MIN_PARAGRAPH_CHARS:
                items.append(("p", text))

        # ── Ordered / unordered lists ──
        elif tag in ("ol", "ul"):
            for li in child.find_all("li", recursive=False):
                if isinstance(li, Tag):
                    text = normalize_text(li.get_text(" ", strip=True))
                    if text and len(text) > 10:
                        items.append(("li", text))

    return seen_back_matter


# ── main extractor ────────────────────────────────────────────────────


def _find_bodymatter(container: Tag) -> Tag | None:
    """Find the content root inside the article container.

    ``#bodymatter`` wraps everything in a ``div.core-container`` — we return
    that inner div so the walker sees ``<section>`` children directly.
    """
    bodymatter = container.select_one("#bodymatter")
    if bodymatter is not None:
        core = bodymatter.select_one(".core-container")
        if core is not None:
            return core
        return bodymatter
    # Fallback: look for the article element itself if bodymatter is absent
    # (e.g. paywalled pages that only show abstract).
    article = container.find("article")
    if isinstance(article, Tag):
        return article
    return None


def extract_body_markdown(body_container: Tag | None) -> str:
    """Walk ASM article content and return markdown.

    Walks ``#bodymatter`` → top-level ``<section>`` elements, recursively
    descending into nested sections.  Stops at back-matter sections
    (acknowledgments, bibliography, etc.).

    Parameters
    ----------
    body_container:
        The pre-cleaned article body element.

    Returns
    -------
    Markdown string with headings, paragraphs, figures and tables.
    """
    if body_container is None:
        return ""

    root = _find_bodymatter(body_container)
    if root is None:
        return ""

    items: list[tuple[str, str]] = []
    seen_back_matter = False

    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if _is_skip_tag(child):
            continue

        tag = child.name.lower() if child.name else ""

        if tag == "section":
            if _is_back_matter_section(child):
                break
            if _walk_section(child, items, seen_back_matter):
                break

    return _assemble_markdown(items)


# ── abstract extraction ───────────────────────────────────────────────


def extract_abstract(body_container: Tag | None) -> str:
    """Extract abstract text from ASM article.

    ASM has two frontend variants:

    - **New pb frontend**: ``#primary-abstract`` → ``<h2>ABSTRACT</h2>`` →
      ``<div>`` (body) + ``#abs-sec-1`` (IMPORTANCE/Significance).
    - **Old Atypon frontend**: ``#abstract`` as a ``<section>`` with the
      abstract text directly.

    Returns the abstract body, optionally followed by the IMPORTANCE
    paragraph.
    """
    if body_container is None:
        return ""

    # ── New pb frontend ──
    abstract_section = body_container.select_one("#primary-abstract")
    if abstract_section is not None:
        heading = abstract_section.find("h2")
        if isinstance(heading, Tag) and heading.get_text(strip=True).lower() == "abstract":
            heading.decompose()

        parts: list[str] = []

        # Main abstract body — first <div>.
        for child in abstract_section.children:
            if isinstance(child, Tag) and child.name == "div":
                text = normalize_text(child.get_text(" ", strip=True))
                if text:
                    parts.append(text)
                break

        # IMPORTANCE paragraph.
        importance = abstract_section.select_one("#abs-sec-1")
        if isinstance(importance, Tag):
            text = normalize_text(importance.get_text(" ", strip=True))
            if text:
                parts.append(text)

        return "\n\n".join(parts)

    # ── Old Atypon frontend ──
    abstract_el = body_container.select_one("#abstract")
    if abstract_el is not None:
        heading = abstract_el.find("h2")
        if isinstance(heading, Tag) and heading.get_text(strip=True).lower() == "abstract":
            heading.decompose()
        return normalize_text(abstract_el.get_text(" ", strip=True))

    # ── Fallback: #abstracts (legacy div) ──
    abstracts_div = body_container.select_one("#abstracts")
    if abstracts_div is not None:
        return normalize_text(abstracts_div.get_text(" ", strip=True))

    return ""


# ── markdown assembly ─────────────────────────────────────────────────


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


# ── image URL rewriting (for bridge_windows.py) ──────────────────────


def rewrite_image_urls_to_local(markdown_text: str, output_dir: str) -> str:
    """Rewrite ``![]()`` image URLs to ``images/basename`` for local files."""
    img_dir = Path(output_dir) / "images"
    img_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def _rewrite(match: re.Match) -> str:
        alt_text, url = match.group(1), match.group(2)
        basename = url.rsplit("/", 1)[-1].split("?")[0]
        if (img_dir / basename).exists():
            return f"![{alt_text}](images/{basename})"
        return match.group(0)

    return img_pattern.sub(_rewrite, markdown_text)
