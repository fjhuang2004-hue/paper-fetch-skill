"""Shared section-aware HTML-to-Markdown helpers."""

from __future__ import annotations

import re
from typing import Any

from ..extraction.html.formula_rules import (
    formula_image_url_from_node,
    is_display_formula_node,
    is_formula_container,
    looks_like_formula_image,
    mathml_element_from_html_node,
)
from ..extraction.html.inline import (
    InlineToken,
    html_inline_tokens,
    needs_space_between_inline_text,
    render_html_inline_node,
    render_inline_tokens,
)
from ..extraction.html.semantics import has_explicit_reference_marker, normalize_section_title
from ..formula.convert import normalize_latex_macros
from ..models import normalize_text
from ._article_markdown_math import render_external_mathml_expression, render_mathml_expression
from ..markdown.citations import is_citation_link, numeric_citation_payload
from .html_noise import HTML_BLOCK_TAGS, HTML_DROP_TAGS, should_drop_html_element

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:  # pragma: no cover - exercised implicitly when dependency is absent
    BeautifulSoup = None
    NavigableString = None
    Tag = None

HEADING_TAG_PATTERN = re.compile(r"^h[1-6]$")
INLINE_IMAGE_SPACING_PATTERN = re.compile(r"(?<=[^\s])(!\[)")
INLINE_WHITESPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")
LINE_EDGE_WHITESPACE_PATTERN = re.compile(r" *\n *")
MARKDOWN_BLANK_RUN_PATTERN = re.compile(r"\n{3,}")
ORDERED_LIST_PREFIX_PATTERN = re.compile(r"^\s*(?:\(?\d+[A-Za-z]?\)?|[ivxlcdm]+)[.)]\s+", flags=re.IGNORECASE)
UNORDERED_LIST_PREFIX_PATTERN = re.compile(r"^\s*[•◦▪▫‣⁃∙●○◾◽◼□■]\s*")
MARKDOWN_LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
FIGURE_LABEL_PATTERN = re.compile(r"^\s*(?:fig(?:ure)?\.?)\s*(\d+[A-Za-z]?)\s*[:.]?\s*(.*)$", flags=re.IGNORECASE)
FIGURE_ID_PATTERN = re.compile(r"(?:^|[-_ ])figure[-_ ]?(\d+[A-Za-z]?)$", flags=re.IGNORECASE)
FIGURE_TRAILING_LINK_PATTERN = re.compile(r"\b(?:PowerPoint slide|Full size image)\b.*$", flags=re.IGNORECASE)
FIGURE_DESCRIPTION_SELECTORS = (
    "figcaption",
    ".c-article-section__figure-description",
    ".figure__caption-text",
)
INLINE_FIGURE_SRC_ATTR = "data-paper-fetch-inline-src"
INLINE_FIGURE_ALT_ATTR = "data-paper-fetch-inline-alt"

def _render_heading_inline_node(node: Any, *, text_style: str | None = None) -> str:
    return render_html_inline_node(node, policy="heading", text_style=text_style)


def render_heading_text_from_html(node: Any) -> str:
    return _render_heading_inline_node(node)


def extract_section_title(section: Any) -> str:
    if BeautifulSoup is None or section is None:
        return ""
    heading = section.find(HEADING_TAG_PATTERN)
    if heading is None:
        return ""
    return render_heading_text_from_html(heading)


def _select_first(node: Any, selectors: tuple[str, ...]) -> Any:
    if BeautifulSoup is None or node is None:
        return None
    for selector in selectors:
        match = node.select_one(selector)
        if match is not None:
            return match
    return None


def section_has_direct_renderable_content(
    section: Any,
    *,
    section_content_selectors: tuple[str, ...] = ("div.c-article-section__content",),
) -> bool:
    if BeautifulSoup is None or section is None:
        return False
    content_root = _select_first(section, section_content_selectors) or section
    for child in content_root.children:
        if isinstance(child, NavigableString):
            if normalize_text(str(child)):
                return True
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in {"header", "footer"}:
            continue
        if child.name in HTML_DROP_TAGS or should_drop_html_element(child):
            continue
        if child.name in {"p", "blockquote", "pre", "ul", "ol", "figure", "table"}:
            return True
        if _is_figure_container(child):
            return True
        if child.name in {"div", "article", "main"}:
            if child.find("section", recursive=False) is None and render_clean_text_from_html(child):
                return True
    return False


def render_section_markdown(
    section: Any,
    lines: list[str],
    *,
    level: int,
    force_heading: str | None = None,
    section_content_selectors: tuple[str, ...] = ("div.c-article-section__content",),
) -> None:
    heading = force_heading or extract_section_title(section)
    content_root = _select_first(section, section_content_selectors) or section
    rendered_content: list[str] = []
    render_container_markdown(
        content_root,
        rendered_content,
        level=level + 1,
        skip_first_heading=heading or None,
        section_content_selectors=section_content_selectors,
    )
    if not rendered_content:
        return
    if heading:
        lines.extend([f"{'#' * max(2, min(level, 6))} {heading}", ""])
    lines.extend(rendered_content)


def render_container_markdown(
    node: Any,
    lines: list[str],
    *,
    level: int,
    skip_first_heading: str | None = None,
    section_content_selectors: tuple[str, ...] = ("div.c-article-section__content",),
) -> None:
    if BeautifulSoup is None or node is None:
        return

    for child in node.children:
        if isinstance(child, NavigableString):
            text = normalize_prose_markdown_line_breaks(str(child))
            if text:
                lines.extend([text, ""])
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in {"header", "footer"}:
            continue
        if child.name in HTML_DROP_TAGS or should_drop_html_element(child):
            continue
        if child.name == "section":
            render_section_markdown(
                child,
                lines,
                level=level,
                section_content_selectors=section_content_selectors,
            )
            continue
        if _is_div_section_container(child):
            render_section_markdown(
                child,
                lines,
                level=level,
                section_content_selectors=section_content_selectors,
            )
            continue
        if child.name and HEADING_TAG_PATTERN.match(child.name):
            heading_text = render_heading_text_from_html(child)
            if (
                skip_first_heading
                and normalize_section_title(heading_text) == normalize_section_title(skip_first_heading)
            ):
                continue
            if heading_text:
                lines.extend([f"{'#' * max(2, min(level, 6))} {heading_text}", ""])
            continue
        if child.name in {"p", "blockquote"}:
            text = render_clean_text_from_html(child, collapse_prose_line_breaks=True)
            if text:
                lines.extend([text, ""])
            continue
        if child.name == "pre":
            text = render_clean_text_from_html(child)
            if text:
                lines.extend([text, ""])
            continue
        if child.name in {"ul", "ol"}:
            start = 1
            if child.name == "ol":
                try:
                    start = int(normalize_text(str(child.get("start") or "1")) or "1")
                except ValueError:
                    start = 1
            for index, item in enumerate(child.find_all("li", recursive=False)):
                text = render_clean_text_from_html(item, collapse_prose_line_breaks=True)
                if text:
                    if child.name == "ol":
                        text = ORDERED_LIST_PREFIX_PATTERN.sub("", text)
                        lines.append(f"{start + index}. {text}")
                    else:
                        text = UNORDERED_LIST_PREFIX_PATTERN.sub("", text)
                        lines.append(f"- {text}")
            if lines and lines[-1]:
                lines.append("")
            continue
        if _is_figure_container(child):
            render_figure_markdown(child, lines)
            continue
        if child.name == "figure":
            continue
        if child.name == "table":
            text = render_clean_text_from_html(child)
            if text:
                lines.extend([text, ""])
            continue
        if child.name in {"div", "article", "main"}:
            render_container_markdown(
                child,
                lines,
                level=level,
                skip_first_heading=skip_first_heading,
                section_content_selectors=section_content_selectors,
            )
            continue
        text = render_clean_text_from_html(child)
        if text:
            lines.extend([text, ""])


def _node_attr_text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    attrs = getattr(node, "attrs", None) or {}
    parts = [normalize_text(node.name or "")]
    for key in ("id", "class", "data-test", "data-container-section"):
        value = attrs.get(key)
        if isinstance(value, (list, tuple, set)):
            parts.extend(normalize_text(str(item)) for item in value)
        else:
            parts.append(normalize_text(str(value or "")))
    return " ".join(part.lower() for part in parts if part)


def _is_formula_container(node: Any) -> bool:
    return is_formula_container(node)


def _is_display_formula_node(node: Any) -> bool:
    return is_display_formula_node(node)


def _first_formula_image_url(node: Any) -> str:
    return formula_image_url_from_node(node)


def _is_formula_image_node(node: Any) -> bool:
    return looks_like_formula_image(node)


def _render_formula_image_node(node: Any) -> str:
    url = _first_formula_image_url(node)
    if not url:
        return ""
    return f"![Formula]({url})"


def _render_mathml_node(node: Any) -> str:
    element = mathml_element_from_html_node(node)
    if element is None:
        return ""
    display_mode = _is_display_formula_node(node)
    expression = normalize_text(render_external_mathml_expression(element, display_mode=display_mode))
    if not expression:
        expression = normalize_text(render_mathml_expression(element))
    if not expression:
        return ""
    return f"\n\n$$\n{expression}\n$$\n\n" if display_mode else f"${expression}$"


def _render_formula_container(node: Any) -> str:
    mathml = _render_mathml_node(node)
    if mathml:
        return mathml
    latex = _formula_latex_from_node(node)
    if latex:
        return f"\n\n$$\n{latex}\n$$\n\n" if _is_display_formula_node(node) else latex
    image_url = _first_formula_image_url(node)
    if image_url:
        rendered = f"![Formula]({image_url})"
        return f"\n\n{rendered}\n\n" if _is_display_formula_node(node) else rendered
    if _is_formula_container(node):
        return "[Formula unavailable]"
    return ""


def _is_figure_container(node: Any) -> bool:
    if not isinstance(node, Tag):
        return False
    if node.name == "figure":
        return True
    identity = _node_attr_text(node)
    if "figure" not in identity:
        return False
    return node.find("figure") is not None or node.find("img") is not None or node.find("figcaption") is not None


def _is_div_section_container(node: Any) -> bool:
    if not isinstance(node, Tag) or normalize_text(node.name or "").lower() != "div":
        return False
    class_values = getattr(node, "attrs", {}).get("class") or []
    if isinstance(class_values, str):
        classes = {item.lower() for item in class_values.split()}
    else:
        classes = {normalize_text(str(item)).lower() for item in class_values}
    return bool(classes & {"section", "section_2"}) and node.find(HEADING_TAG_PATTERN) is not None


def _clean_figure_text_candidate(text: str) -> str:
    normalized = normalize_text(text.replace("\n", " "))
    if not normalized:
        return ""
    normalized = FIGURE_TRAILING_LINK_PATTERN.sub("", normalized).strip()
    return normalize_text(normalized)


def _figure_label_from_text(text: str) -> tuple[str, str]:
    normalized = _clean_figure_text_candidate(text)
    match = FIGURE_LABEL_PATTERN.match(normalized)
    if match is None:
        return "", normalized
    return f"Figure {match.group(1)}.", normalize_text(match.group(2))


def _figure_label_from_node(node: Any) -> str:
    current = node
    while isinstance(current, Tag):
        identity = _node_attr_text(current)
        match = FIGURE_ID_PATTERN.search(identity)
        if match is not None:
            return f"Figure {match.group(1)}."
        current = current.parent if isinstance(getattr(current, "parent", None), Tag) else None
    return ""


def _iter_figure_text_candidates(node: Any) -> list[str]:
    if not isinstance(node, Tag):
        return []
    caption_candidates: list[str] = []
    description_candidates: list[str] = []
    for selector in FIGURE_DESCRIPTION_SELECTORS:
        for match in node.select(selector):
            if not isinstance(match, Tag):
                continue
            text = render_clean_text_from_html(match)
            if not text:
                continue
            if selector == ".c-article-section__figure-description":
                if text not in description_candidates:
                    description_candidates.append(text)
                continue
            if text not in caption_candidates:
                caption_candidates.append(text)
    if caption_candidates:
        return caption_candidates + [text for text in description_candidates if text not in caption_candidates]
    if description_candidates:
        return description_candidates

    candidates: list[str] = []
    data_title = normalize_text(str(node.get("data-title") or ""))
    if data_title and data_title not in candidates:
        candidates.append(data_title)
    if candidates:
        return candidates
    image = node.find("img")
    if isinstance(image, Tag):
        alt_text = normalize_text(str(image.get("alt") or ""))
        if alt_text and alt_text not in candidates:
            candidates.append(alt_text)
    return candidates


def _iter_inline_figure_images(node: Any) -> list[tuple[str, str]]:
    if not isinstance(node, Tag):
        return []

    images: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_image(candidate: Any) -> None:
        if not isinstance(candidate, Tag):
            return
        src = normalize_text(str(candidate.get(INLINE_FIGURE_SRC_ATTR) or ""))
        if not src:
            return
        alt = normalize_text(str(candidate.get(INLINE_FIGURE_ALT_ATTR) or "Figure")) or "Figure"
        item = (src, alt)
        if item in seen:
            return
        seen.add(item)
        images.append(item)

    add_image(node)
    for image in node.find_all("img"):
        add_image(image)
    return images


def _append_inline_figure_image(lines: list[str], src: str, alt: str) -> None:
    lines.extend([f"![{alt or 'Figure'}]({src})", ""])


def render_figure_markdown(node: Any, lines: list[str]) -> None:
    if not isinstance(node, Tag):
        return

    inline_images = _iter_inline_figure_images(node)
    figure_items: list[tuple[str, str]] = []
    for text in _iter_figure_text_candidates(node):
        label, remainder = _figure_label_from_text(text)
        candidate = _clean_figure_text_candidate(remainder if label else text)
        item = (label, candidate)
        if (label or candidate) and item not in figure_items:
            figure_items.append(item)

    fallback_label = _figure_label_from_node(node)
    if not figure_items and fallback_label:
        figure_items.append((fallback_label, ""))
    if not figure_items:
        for src, alt in inline_images:
            _append_inline_figure_image(lines, src, alt)
        return

    if inline_images and len(inline_images) == len(figure_items) and len(inline_images) > 1:
        for index, (label, caption) in enumerate(figure_items):
            src, alt = inline_images[index]
            _append_inline_figure_image(lines, src, alt)
            active_label = label or (fallback_label if index == 0 else "")
            if active_label:
                line = f"**{active_label}**"
                if caption:
                    line = f"{line} {caption}"
            else:
                line = caption
            if line:
                lines.extend([line, ""])
        return

    for src, alt in inline_images:
        _append_inline_figure_image(lines, src, alt)

    for index, (label, caption) in enumerate(figure_items):
        active_label = label or (fallback_label if index == 0 else "")
        if active_label:
            line = f"**{active_label}**"
            if caption:
                line = f"{line} {caption}"
        else:
            line = caption
        if line:
            lines.extend([line, ""])


def _has_explicit_citation_marker(node: Any) -> bool:
    return has_explicit_reference_marker(node)


def _numeric_citation_payload_from_html(node: Any) -> str | None:
    if not isinstance(node, Tag):
        return None
    text = normalize_text(node.get_text("", strip=True))
    payload = numeric_citation_payload(text.strip("[]"))
    if payload is None:
        return None
    href = normalize_text(str(node.get("href") or ""))
    if node.name == "a" and (_has_explicit_citation_marker(node) or is_citation_link(href, text)):
        return payload
    if node.name == "sup":
        anchors = [match for match in node.find_all("a") if isinstance(match, Tag)]
        if anchors and all(_numeric_citation_payload_from_html(anchor) for anchor in anchors):
            return payload
    return None


def _is_linebreak_sensitive_markdown_block(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if normalize_text(line)]
    if len(lines) <= 1:
        return False
    if any(
        line.startswith(("$$", "|", "```", "~~~", "![", "#"))
        or MARKDOWN_LIST_ITEM_PATTERN.match(line)
        for line in lines
    ):
        return True
    return "$$\n" in block or "\n$$" in block


def normalize_prose_markdown_line_breaks(text: str) -> str:
    normalized = MARKDOWN_BLANK_RUN_PATTERN.sub("\n\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    parts = re.split(r"(\n\s*\n)", normalized)
    collapsed: list[str] = []
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r"\n\s*\n", part):
            collapsed.append("\n\n")
            continue
        if _is_linebreak_sensitive_markdown_block(part):
            collapsed.append(normalize_text(part))
            continue
        collapsed.append(normalize_text(re.sub(r"\s*\n\s*", " ", part)))
    return normalize_text("".join(collapsed))


def render_clean_text_from_html(node: Any, *, collapse_prose_line_breaks: bool = False) -> str:
    rendered = render_clean_html_node(node)
    rendered = INLINE_IMAGE_SPACING_PATTERN.sub(r" \1", rendered)
    rendered = INLINE_WHITESPACE_PATTERN.sub(" ", rendered)
    rendered = LINE_EDGE_WHITESPACE_PATTERN.sub("\n", rendered)
    rendered = MARKDOWN_BLANK_RUN_PATTERN.sub("\n\n", rendered)
    if collapse_prose_line_breaks:
        rendered = normalize_prose_markdown_line_breaks(rendered)
    return normalize_text(rendered)


def render_clean_html_node(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name in HTML_DROP_TAGS:
        return ""
    if _is_mathjax_tex_node(node):
        return normalize_latex_macros(node.get_text("", strip=False).strip())
    if normalize_text(node.name or "").lower() == "math":
        return _render_mathml_node(node)
    if _is_formula_image_node(node):
        return _render_formula_image_node(node)
    if _is_formula_container(node):
        rendered_formula = _render_formula_container(node)
        if rendered_formula:
            return rendered_formula
    if node.name == "br":
        return "\n"
    if node.name == "figure":
        caption = node.find("figcaption")
        return render_clean_html_node(caption)
    if _is_inline_html_node(node):
        return _render_clean_inline_node(node)

    rendered = render_clean_children(node)
    if not rendered.strip():
        return ""
    if node.name in {"li"}:
        return f"\n\n{rendered}\n\n"
    if node.name in HTML_BLOCK_TAGS:
        return f"\n\n{rendered}\n\n"
    return rendered


def _is_inline_html_node(node: Any) -> bool:
    if not isinstance(node, Tag):
        return False
    name = normalize_text(node.name or "").lower()
    return bool(name) and name not in HTML_BLOCK_TAGS and name not in {"figure", "table"}


def _raw_inline_markdown_from_node(node: Any) -> str | None:
    if not isinstance(node, Tag):
        return None
    if _is_mathjax_tex_node(node):
        return normalize_latex_macros(node.get_text("", strip=False).strip()) or None
    if normalize_text(node.name or "").lower() == "math":
        return _render_mathml_node(node) or None
    if _is_formula_image_node(node):
        return _render_formula_image_node(node) or None
    if _is_formula_container(node):
        return _render_formula_container(node) or None
    return None


def _drop_inline_node(node: Any) -> bool:
    return isinstance(node, Tag) and node.name in HTML_DROP_TAGS


def _render_clean_inline_node(node: Any) -> str:
    return render_html_inline_node(
        node,
        policy="body",
        citation_payload_from_node=_numeric_citation_payload_from_html,
        raw_markdown_from_node=_raw_inline_markdown_from_node,
        drop_node=_drop_inline_node,
        render_text_styles=False,
        break_render="\n",
    )


def _render_clean_inline_tokens(tokens: list[InlineToken]) -> str:
    return render_inline_tokens(tokens, policy="body", break_render="\n")


def render_clean_children(node: Any) -> str:
    text = ""
    inline_tokens: list[InlineToken] = []

    def flush_inline_tokens() -> None:
        nonlocal text, inline_tokens
        if not inline_tokens:
            return
        rendered_inline = _render_clean_inline_tokens(inline_tokens)
        if rendered_inline:
            if needs_space_between_inline_text(
                text,
                rendered_inline,
                right_is_markdown_image=rendered_inline.startswith("!["),
            ):
                text += " "
            text += rendered_inline
        inline_tokens = []

    for child in node.children:
        if isinstance(child, NavigableString):
            inline_tokens.extend(html_inline_tokens(child))
            continue
        if not isinstance(child, Tag):
            continue
        if _is_inline_html_node(child):
            inline_tokens.extend(
                html_inline_tokens(
                    child,
                    citation_payload_from_node=_numeric_citation_payload_from_html,
                    raw_markdown_from_node=_raw_inline_markdown_from_node,
                    drop_node=_drop_inline_node,
                    render_text_styles=False,
                )
            )
            continue
        flush_inline_tokens()
        rendered = render_clean_html_node(child)
        if not rendered:
            continue
        if needs_space_between_inline_text(
            text,
            rendered,
            right_is_markdown_image=rendered.startswith("!["),
        ):
            text += " "
        text += rendered
    flush_inline_tokens()
    return text


def _is_mathjax_tex_node(node: Any) -> bool:
    if not isinstance(node, Tag):
        return False
    name = normalize_text(node.name or "").lower()
    if name == "tex-math":
        return True
    classes = getattr(node, "attrs", {}).get("class") or []
    if isinstance(classes, str):
        class_values = classes.split()
    else:
        class_values = [str(value) for value in classes]
    normalized_classes = {normalize_text(value).lower() for value in class_values}
    return bool(normalized_classes & {"mathjax-tex", "tex", "tex2jax_ignore"})


def _formula_latex_from_node(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    candidates: list[Any] = []
    if _is_mathjax_tex_node(node):
        candidates.append(node)
    candidates.extend(candidate for candidate in node.find_all("tex-math") if isinstance(candidate, Tag))
    try:
        candidates.extend(candidate for candidate in node.select(".mathjax-tex, .tex, .tex2jax_ignore") if isinstance(candidate, Tag))
    except Exception:
        pass
    seen: set[int] = set()
    for candidate in candidates:
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        latex = normalize_latex_macros(candidate.get_text("", strip=False).strip())
        if latex:
            return latex
    return ""


def needs_space_between(left: str, right: str, previous_child: Any, child: Any) -> bool:
    del previous_child, child
    return needs_space_between_inline_text(left, right, right_is_markdown_image=right.startswith("!["))
