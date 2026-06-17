"""RSC (Royal Society of Chemistry) DOM extractor.

RSC's articlehtml endpoint (``/en/content/articlehtml/{year}/{journal}/{doi}``)
delivers clean HTML under ``#wrapper``.  The landing page
(``/en/content/articlelanding/...``) loads full text via AJAX into
``#pnlArticleContentLoaded``.

**Articlehtml** ``#wrapper`` direct children::

    div.left_head / div.right_head     ← navigation (skip)
    div.article_info                   ← DOI / journal / year (skip)
    p.header_text                      ← authors (skip)
    p.bold.italic                      ← publication date (skip)
    div.abstract                       ← h2 Abstract + p
    h2 / h3                            ← section headings (no class)
    p / p.otherpara                    ← body paragraphs
    div                                ← unnamed wrappers around body paragraphs
    div.image_table                    ← figure (table wrapping img + caption)
    table:not(div.image_table table)   ← data table
    ...
    h2 Author contributions            ← back-matter (stop)

**Landing page** ``#pnlArticleContentLoaded`` direct children::

    h2.h--heading2 / h3.h--heading3    ← section headings
    p                                  ← body paragraphs
    div.img-tbl                        ← figure (figure > a > img + figcaption)
    div.ref-list / div.footnotes       ← back-matter (skip)

Because articlehtml wraps some body paragraphs in unnamed ``<div>``
containers, we walk **all descendants** of the content root (not just
direct children), skipping only inside excluded containers.
"""

from __future__ import annotations

import re
from pathlib import Path
from bs4 import Tag

from ..utils import normalize_text

# RSC image base URL (relative paths like /image/article/2022/GC/...)
_RSC_IMG_BASE = "https://pubs.rsc.org"

# Class-based containers whose descendants are skipped (chrome, back-matter,
# or handled at the top level like figures/tables).
_EXCLUDED_CLASSES = {
    "left_head", "right_head", "article_info", "abstract",
    "image_table", "img-tbl", "ref-list", "footnotes",
    "article-copyright", "header_text",
}

# Tag-based containers whose descendants are skipped (figures, tables).
_EXCLUDED_TAGS = {"figure", "table"}

# Headings that signal back-matter — stop extraction entirely.
_BACK_MATTER_KEYWORDS = [
    "author contribution",
    "author contributions",
    "conflict of interest",
    "conflicts of interest",
    "acknowledgement",
    "acknowledgements",
    "acknowledgments",
    "data availability",
    "data availability statement",
    "notes and references",
    "references",
    "supporting information",
    "supplementary material",
    "supplementary data",
    "footnote",
    "footnotes",
    "appendix",
]


def _is_back_matter(text: str) -> bool:
    """Check if a heading text signals back-matter (stop extraction)."""
    lowered = text.lower().strip()
    for keyword in _BACK_MATTER_KEYWORDS:
        if lowered == keyword or lowered.startswith(keyword):
            return True
    return False


def _is_excluded_container(el: Tag, root: Tag) -> bool:
    """Return True if *el* is inside a container that should be skipped."""
    for ancestor in el.parents:
        if ancestor is root:
            break
        if not isinstance(ancestor, Tag):
            continue
        a_cls = set(ancestor.get("class") or [])
        a_tag = ancestor.name.lower() if ancestor.name else ""
        if a_cls & _EXCLUDED_CLASSES:
            return True
        if a_tag in _EXCLUDED_TAGS:
            return True
    return False


# ── body_container hooks ─────────────────────────────────────────────


def rsc_body_container(container: Tag | None) -> None:
    """Locate ``#wrapper`` (articlehtml) or ``#pnlArticleContentLoaded``
    (landing page) and promote it as the sole body content."""
    from bs4 import Tag as _Tag
    if not isinstance(container, _Tag):
        return

    wrapper = container.select_one("#wrapper")
    if wrapper is None:
        wrapper = container.select_one("#pnlArticleContentLoaded")
    if wrapper is None:
        wrapper = container.select_one("#pnlArticleContent")

    if wrapper is not None and isinstance(wrapper, _Tag):
        text_len = len(wrapper.get_text(" ", strip=True))
        if text_len >= 500:
            wrapper.extract()
            container.clear()
            container.append(wrapper)


def rsc_asset_body_container(container: Tag | None) -> None:
    """Asset extraction shares the same container logic, plus rewrites
    image URLs so the generic figure-asset extractor picks up hi-res
    versions from ``<a>`` wrappers instead of low-res ``data-original``."""
    rsc_body_container(container)
    _rewrite_figure_urls_for_assets(container)


def _rewrite_figure_urls_for_assets(container: Tag | None) -> None:
    """Replace ``<img>`` src with hi-res URL from wrapping ``<a>`` href.

    RSC landing pages lazy-load figures::

        <a href=\\"...f1_hi-res.gif\\">
          <img src=\\"LoadingBackGround.JPG\\" data-original=\\"...f1.gif\\"/>
        </a>

    The generic ``extract_figure_assets`` only reads ``<img>`` attributes,
    so it picks up the low-res thumbnail.  We rewrite the ``<img>`` src
    to the hi-res URL so downstream extraction gets the good version.
    """
    if container is None:
        return
    for a_tag in container.select("a[href]"):
        href = (a_tag.get("href") or "").strip()
        if not href or not href.endswith("_hi-res.gif"):
            continue
        img = a_tag.find("img")
        if img is None:
            continue
        if not isinstance(img, Tag):
            continue
        # Only rewrite if the current src is a placeholder or low-res.
        current_src = (img.get("src") or "").lower()
        if "loadingbackground" in current_src or not current_src:
            img["src"] = href
            if img.get("data-original"):
                img["data-original"] = href


# ── main extractor ───────────────────────────────────────────────────


def _find_content_root(body_container: Tag) -> Tag | None:
    """After ``rsc_body_container``, the container holds ``#wrapper`` or
    ``#pnlArticleContentLoaded`` as its only child.  Return that child
    (or the container itself as fallback)."""
    for child in body_container.children:
        if isinstance(child, Tag):
            cid = child.get("id", "")
            if cid in ("wrapper", "pnlArticleContentLoaded", "pnlArticleContent"):
                return child
    # Fallback: if the container itself looks like content, use it.
    if body_container.select_one("h2, h3, p"):
        return body_container
    return None


def extract_body_markdown(body_container: Tag | None) -> str:
    """Walk RSC article content and return markdown.

    Walks all descendants of the content root (``#wrapper`` or
    ``#pnlArticleContentLoaded``), skipping elements inside excluded
    containers (chrome, figures, tables, back-matter).

    Parameters
    ----------
    body_container:
        The pre-cleaned article body element (after ``rsc_body_container``
        has promoted the content root).

    Returns
    -------
    Markdown string with headings, paragraphs, figures and tables.
    """
    if body_container is None:
        return ""

    root = _find_content_root(body_container)
    if root is None:
        return ""

    items: list[tuple[str, str]] = []
    seen_back_matter = False

    for el in root.descendants:
        if seen_back_matter:
            break
        if not isinstance(el, Tag):
            continue
        if _is_excluded_container(el, root):
            continue

        tag = el.name.lower() if el.name else ""

        # ── Headings ──
        if tag in ("h2", "h3", "h4"):
            heading_text = normalize_text(el.get_text(" ", strip=True))
            if not heading_text:
                continue
            if "abstract" == heading_text.lower():
                continue  # abstract is extracted separately
            if _is_back_matter(heading_text):
                seen_back_matter = True
                break
            level = int(tag[1])
            items.append((f"h{level}", heading_text))

        # ── Paragraphs ──
        elif tag == "p":
            classes = el.get("class") or []
            # Skip author header (even when reached via descendant walk).
            if "header_text" in classes:
                continue
            # Skip publication date line.
            if "bold" in classes and "italic" in classes:
                continue

            text = normalize_text(el.get_text(" ", strip=True))
            if not text:
                continue
            if len(text) < 40:
                continue
            items.append(("p", text))

        # ── Figures (div.image_table – articlehtml) ──
        elif tag == "div" and "image_table" in (el.get("class") or []):
            _extract_image_table_figure(el, items)

        # ── Figures (div.img-tbl – landing page) ──
        elif tag == "div" and "img-tbl" in (el.get("class") or []):
            _extract_landing_figure(el, items)

        # ── standalone <figure> (landing page) ──
        elif tag == "figure":
            _extract_figure_tag(el, items)

        # ── Data tables (not inside image_table — already excluded) ──
        elif tag == "table":
            rows = _extract_table_rows(el)
            if rows:
                items.append(("table", _table_to_markdown(rows)))

        # ── Ordered lists ──
        elif tag == "ol":
            for li in el.find_all("li", recursive=False):
                if isinstance(li, Tag):
                    text = normalize_text(li.get_text(" ", strip=True))
                    if text and len(text) > 10:
                        items.append(("li", text))

    return _assemble_markdown(items)


# ── figure helpers ───────────────────────────────────────────────────


def _extract_image_table_figure(el: Tag, items: list[tuple[str, str]]) -> None:
    """Extract figure from articlehtml ``div.image_table``.

    Prefers the hi-res image from the ``<a>`` wrapper's ``href``
    (e.g. ``..._hi-res.gif``) over the thumbnail ``<img>`` ``src``.
    """
    # Prefer <a href> for hi-res; fall back to <img src>.
    img_src = ""
    a_tag = el.select_one("td.imgHolder a")
    if a_tag and isinstance(a_tag, Tag):
        img_src = a_tag.get("href", "")
    if not img_src:
        img_tag = el.find("img")
        if img_tag and isinstance(img_tag, Tag):
            img_src = img_tag.get("src", "")
    if not img_src:
        return
    if img_src.startswith("/"):
        img_src = _RSC_IMG_BASE + img_src
    items.append(("fig_img", img_src))

    # Caption is in td.image_title.
    caption_el = el.select_one("td.image_title")
    if caption_el and isinstance(caption_el, Tag):
        cap_text = normalize_text(caption_el.get_text(" ", strip=True))
        if cap_text:
            items.append(("fig_cap", cap_text))


def _extract_landing_figure(el: Tag, items: list[tuple[str, str]]) -> None:
    """Extract figure from landing page ``div.img-tbl``.

    Prefers the hi-res image from the ``<a>`` wrapper's ``href``
    over the lazy-loaded ``data-original`` thumbnail."""
    # Prefer <a href> for hi-res; fall back to data-original then src.
    img_src = ""
    a_tag = el.find("a")
    if a_tag and isinstance(a_tag, Tag):
        img_src = a_tag.get("href", "")
    if not img_src:
        img_tag = el.find("img")
        if img_tag and isinstance(img_tag, Tag):
            img_src = img_tag.get("data-original", "") or img_tag.get("src", "")
    if not img_src or "LoadingBackGround" in img_src:
        return
    if img_src.startswith("/"):
        img_src = _RSC_IMG_BASE + img_src
    items.append(("fig_img", img_src))

    caption_el = el.select_one("figcaption.img-tbl__caption")
    if caption_el and isinstance(caption_el, Tag):
        cap_text = normalize_text(caption_el.get_text(" ", strip=True))
        if cap_text:
            items.append(("fig_cap", cap_text))


def _extract_figure_tag(el: Tag, items: list[tuple[str, str]]) -> None:
    """Extract from standalone ``<figure>`` element (landing page).

    Prefers the hi-res image from the ``<a>`` wrapper's ``href``."""
    img_src = ""
    a_tag = el.find("a")
    if a_tag and isinstance(a_tag, Tag):
        img_src = a_tag.get("href", "")
    if not img_src:
        img_tag = el.find("img")
        if img_tag and isinstance(img_tag, Tag):
            img_src = img_tag.get("data-original", "") or img_tag.get("src", "")
    if not img_src or "LoadingBackGround" in img_src:
        return
    if img_src.startswith("/"):
        img_src = _RSC_IMG_BASE + img_src
    items.append(("fig_img", img_src))

    caption_el = el.find("figcaption")
    if caption_el and isinstance(caption_el, Tag):
        cap_text = normalize_text(caption_el.get_text(" ", strip=True))
        if cap_text:
            items.append(("fig_cap", cap_text))


# ── table helpers ────────────────────────────────────────────────────


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


# ── abstract extraction ─────────────────────────────────────────────


def extract_abstract(body_container: Tag | None) -> str:
    """Extract abstract text from ``div.abstract``."""
    if body_container is None:
        return ""

    # Look inside the content root first.
    root = _find_content_root(body_container) or body_container
    abstract_el = root.select_one("div.abstract")
    if abstract_el is None:
        abstract_el = root.select_one("[class*='abstract']")

    if abstract_el is None:
        return ""

    heading = abstract_el.find(["h2", "h3"])
    if heading and isinstance(heading, Tag):
        h_text = heading.get_text(" ", strip=True).lower()
        if h_text == "abstract":
            heading.decompose()

    return normalize_text(abstract_el.get_text(" ", strip=True))


# ── markdown assembly ────────────────────────────────────────────────


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


# ── image URL rewriting (for bridge_windows.py) ─────────────────────-


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


# ── author extraction ────────────────────────────────────────────────


def extract_authors(html_text: str) -> list[str]:
    """Extract author names from ``p.header_text`` bold spans."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    header_p = soup.select_one("p.header_text")
    if header_p is None:
        return []
    authors: list[str] = []
    for bold in header_p.find_all("span", class_="bold"):
        name = normalize_text(bold.get_text(" ", strip=True))
        if name and name.lower() != "and":
            authors.append(name)
    return authors
