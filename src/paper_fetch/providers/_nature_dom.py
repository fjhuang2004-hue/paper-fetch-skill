"""Springer Nature dedicated DOM extractor for the bridge path.

Wraps the existing ``html_springer_nature.py`` extraction pipeline
and exposes the standard bridge interface used by markdown.py:
``extract_abstract()``, ``extract_body_markdown()``, and
``rewrite_image_urls_to_local()``.

Nature.com articles use:
- ``article`` or ``main`` root → ``div.c-article-body`` → ``div.main-content``
- Sections in ``<section data-title="...">``
- Content in ``div.c-article-section__content``
- Figures in ``<figure>`` with ``<img>`` and ``<figcaption>``
- Images served from ``media.springernature.com`` / ``nature.com`` CDN
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..extraction.html.parsing import choose_parser
from ..utils import normalize_text
from .html_springer_nature import (
    extract_springer_nature_markdown,
    select_nature_abstract_section,
    clean_springer_nature_text_fragment,
)
from ._html_section_markdown import render_section_markdown

# Nature CDN domains
_NATURE_IMAGE_HOSTS = (
    "media.springernature.com",
    "www.nature.com",
    "static-content.springer.com",
)


def extract_abstract(soup: BeautifulSoup) -> str:
    """Extract abstract text from a Nature article page.

    Uses the existing ``select_nature_abstract_section()`` helper
    from ``html_springer_nature.py``, then extracts clean text.
    """
    body = soup.select_one("div.c-article-body") or soup
    section = select_nature_abstract_section(body)
    if section is None:
        return ""
    text = section.get_text(" ", strip=True)
    return clean_springer_nature_text_fragment(text)


def extract_body_markdown(html_text: str, source_url: str) -> str:
    """Extract full article body as Markdown.

    Delegates to ``extract_springer_nature_markdown()`` which handles
    the complete pipeline: DOM parsing, chrome pruning, section
    rendering, and post-processing.
    """
    return extract_springer_nature_markdown(html_text, source_url)


def extract_body_markdown_from_soup(soup_or_elem: BeautifulSoup | Tag, source_url: str = "") -> str:
    """Extract body from a pre-parsed BeautifulSoup object.

    When called from markdown.py with ``_raw_body`` (deepcopy of container
    before normalization), we serialize back to HTML and pass through the
    standard extraction pipeline.
    """
    html = str(soup_or_elem)
    return extract_springer_nature_markdown(html, source_url)


def rewrite_image_urls_to_local(md_text: str, output_dir: str) -> str:
    """Rewrite CDN image URLs to local ``images/`` paths.

    Scans for ``![](https://media.springernature.com/...)`` and similar
    patterns, replacing them with ``![](images/basename)``.
    """
    out_path = Path(output_dir)

    def _replace(m: re.Match) -> str:
        prefix = m.group(1)  # "!"
        alt = m.group(2) or ""
        url = m.group(3)
        basename = url.rsplit("/", 1)[-1].split("?")[0]
        local = out_path / "images" / basename
        if local.exists():
            return f"{prefix}[{alt}](images/{basename})"
        return m.group(0)

    md_text = re.sub(
        r"(!)\[([^\]]*)\]\(((?:https?:)?//[^)\s]+)\)",
        _replace,
        md_text,
    )
    return md_text
