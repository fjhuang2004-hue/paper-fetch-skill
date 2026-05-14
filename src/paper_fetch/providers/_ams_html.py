"""AMS browser-workflow HTML callbacks."""

from __future__ import annotations

import re
import urllib.parse
from functools import partial
from typing import Any

from ..common_patterns import FIGURE_LABEL_PATTERN, TABLE_LABEL_PATTERN
from ..extraction.html.provider_rules import cleanup_policy_for_profile
from ..extraction.html.parsing import choose_parser
from ..extraction.html.formula_rules import (
    MATHML_SCRIPT_TYPES,
    is_display_formula_node,
    mathml_element_from_html_node,
)
from ..extraction.html.inline import render_html_inline_node
from ..extraction.html.shared import soup_root as _soup_root
from ..extraction.html.ui_tokens import RELATED_CONTENT_CHROME_TOKENS
from ..quality.html_signals import ams_blocking_fallback_signals
from ..utils import normalize_text
from ._article_markdown_math import (
    render_external_mathml_expression,
    render_mathml_expression,
)
from ._html_authors import (
    ATYPON_AUTHOR_NOISE_TEXT,
    AuthorExtractionPipeline,
    extract_meta_authors,
    extract_property_authors,
    extract_selector_authors,
)
from ._html_references import extract_numbered_references_from_html

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    BeautifulSoup = None
    NavigableString = None
    Tag = None


# SITE_UI_COPY_REGRESSION_MARKER: site-owned UI copy; rerun extraction rules
# when publisher text changes. These tokens stop scanning after the article
# body; provider_rules handles availability/container chrome before this stage.
# STRUCTURAL_UI_COPY_HOOK: provider-specific post-content cutoff, not generic
# body denylist.
AMS_POST_CONTENT_BREAK_TOKENS = (
    "article type",
    "issue section",
    "most read",
    "most cited",
    *RELATED_CONTENT_CHROME_TOKENS,
    "ams publications",
)

# AMS Atypon XSL emits display equation ids as E1/E02/E2a and unnumbered
# equation ids as UE1/UE02. Keep this provider-owned because it is DOM-source
# naming, not a generic equation label convention.
AMS_EQUATION_ID_PATTERN = re.compile(r"^E0*([0-9]+[A-Za-z]?)$", flags=re.IGNORECASE)
AMS_UNNUMBERED_EQUATION_ID_PATTERN = re.compile(
    r"^UE0*[0-9]+[A-Za-z]?$",
    flags=re.IGNORECASE,
)
AMS_EQUATION_LABEL_PATTERN = re.compile(
    r"\b(?:eq(?:uation)?\.?)\s*[\(\[]?\s*([0-9]+[A-Za-z]?)\s*[\)\]]?\.?",
    flags=re.IGNORECASE,
)
AMS_PROSE_PARENTHESIS_PATTERN = re.compile(
    r"(</(?:sub|sup)>)(\((?P<inner>[^()\n]{1,120})\))",
    flags=re.IGNORECASE,
)
AMS_MATHJAX_RENDERED_SELECTORS = (
    ".MathJax_CHTML",
    ".MathJax_SVG",
    ".MathJax",
    ".mjx-chtml",
    "mjx-container",
)
AMS_INLINE_MARKDOWN_TAGS = (
    "inline-formula",
    "math",
    "i",
    "em",
    "b",
    "strong",
    "sub",
    "sup",
)
AMS_INLINE_SKIP_ANCESTOR_TAGS = {"math", "script", "style", "table"}


def _ams_node_text(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    return normalize_text(node.get_text(" ", strip=True))


def _extract_ams_property_authors(html_text: str) -> list[str]:
    return extract_property_authors(
        html_text,
        selectors="[property='author']",
        ignored_text=ATYPON_AUTHOR_NOISE_TEXT,
        reject_email=True,
    )


def _extract_ams_selector_authors(html_text: str) -> list[str]:
    return extract_selector_authors(
        html_text,
        selectors=(
            "[property='author'] [property='name']",
            ".article__authors a",
            ".authors a",
        ),
        ignored_text=ATYPON_AUTHOR_NOISE_TEXT,
        node_text=_ams_node_text,
        reject_email=True,
        reject_affiliation=True,
    )


_AUTHOR_EXTRACTION_PIPELINE = AuthorExtractionPipeline(
    partial(extract_meta_authors, keys={"citation_author", "dc.creator"}),
    _extract_ams_property_authors,
    _extract_ams_selector_authors,
)


def extract_authors(html_text: str) -> list[str]:
    return _AUTHOR_EXTRACTION_PIPELINE(html_text)


def extract_references(html_text: str) -> list[dict[str, str | None]]:
    if BeautifulSoup is None:
        return []
    numbered_references = extract_numbered_references_from_html(html_text)
    if numbered_references:
        return numbered_references
    soup = BeautifulSoup(html_text, choose_parser())
    references: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for node in soup.select("meta[name='citation_reference']"):
        raw = normalize_text(str(node.get("content") or ""))
        if not raw or raw in seen:
            continue
        seen.add(raw)
        references.append({"raw": raw})
    return references


blocking_fallback_signals = ams_blocking_fallback_signals


def dom_postprocess(container: Any, *, stage: str | None = None) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    if normalize_text(stage).lower() not in {
        "asset_figure_extraction",
        "asset_body_container",
        "before_block_normalization",
        "body_container",
        "after_block_normalization",
    }:
        return
    _normalize_ams_dom(container)


def markdown_postprocess(
    markdown_text: str,
    *,
    stage: str | None = None,
    heading_text: str | None = None,
    **context: Any,
) -> str:
    del context
    if stage == "heading_category":
        normalized_heading = normalize_text(heading_text or "").rstrip(".").lower()
        # AMS fixtures place Acknowledgments before Data availability/Appendix;
        # keeping it in body preserves that author-intended order for rendering.
        if normalized_heading in {
            "acknowledgment",
            "acknowledgments",
            "acknowledgement",
            "acknowledgements",
        }:
            return "body_heading"
        return ""
    return _normalize_ams_markdown_text(markdown_text)


def _normalize_ams_markdown_text(markdown_text: str) -> str:
    text = re.sub(r"\bFig\s+\.\s+", "Fig. ", markdown_text)
    text = re.sub(r"\bFigure\s+\.\s+", "Figure ", text)
    text = re.sub(r"\bTable\s+\.\s+", "Table ", text)
    text = re.sub(
        r"<(sup|sub)>\s*<\1>(.*?)</\1>\s*</\1>",
        r"<\1>\2</\1>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<sub>\s*([^<>]*?)\s*</sub>\s*<sub>\s*([,;])\s*</sub>\s*<sub>\s*([^<>]*?)\s*</sub>",
        r"<sub>\1\2\3</sub>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<sub>\s*([^<>]*?)\s*</sub>\s*<sub>\s*([,;][^<>]*?)\s*</sub>",
        r"<sub>\1\2</sub>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"</sup>\s*<sup>\s*([,;])\s*</sup>\s*<sup>",
        r"</sup>\1<sup>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(\*[\w\u0370-\u03ffµμ]+\*)\s+<(sub|sup)>",
        r"\1<\2>",
        text,
        flags=re.IGNORECASE,
    )
    text = AMS_PROSE_PARENTHESIS_PATTERN.sub(_restore_ams_prose_parenthesis_match, text)
    return _reorder_ams_backmatter_sections(text)


def _ams_parenthetical_looks_like_math_argument(value: str) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return True
    if re.fullmatch(
        r"[\w\u0370-\u03ffµμ]+\s*(?:[,;]\s*[\w\u0370-\u03ffµμ]+)*",
        normalized,
        flags=re.IGNORECASE,
    ):
        tokens = re.findall(r"[\w\u0370-\u03ffµμ]+", normalized)
        return bool(tokens) and all(
            len(token) == 1 or token.isdigit() for token in tokens
        )
    return bool(
        re.fullmatch(
            r"[0-9\s,.;:+\-−*/=<>^_{}\[\]\\|]+",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _restore_ams_prose_parenthesis_match(match: re.Match[str]) -> str:
    closing_tag = match.group(1)
    parenthetical = match.group(2)
    inner = match.group("inner")
    if _ams_parenthetical_looks_like_math_argument(inner):
        return match.group(0)
    if re.search(r"[A-Za-z\u0370-\u03ffµμ]{2,}", normalize_text(inner)):
        return f"{closing_tag} {parenthetical}"
    return match.group(0)


def _markdown_heading_info(block: str) -> tuple[int, str] | None:
    first_line = normalize_text(block.splitlines()[0] if block.splitlines() else "")
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", first_line)
    if not match:
        return None
    return len(match.group(1)), normalize_text(match.group(2)).rstrip(".:")


def _is_markdown_heading(block: str, target: str) -> bool:
    heading = _markdown_heading_info(block)
    if heading is None:
        return False
    _, text = heading
    normalized = normalize_text(text).lower()
    if target == "appendix":
        return normalized == target or normalized.startswith(f"{target} ")
    return normalized == target


def _heading_level(block: str) -> int | None:
    heading = _markdown_heading_info(block)
    return heading[0] if heading is not None else None


def _section_end_index(blocks: list[str], start: int) -> int:
    start_level = _heading_level(blocks[start])
    if start_level is None:
        return start + 1
    index = start + 1
    while index < len(blocks):
        level = _heading_level(blocks[index])
        if level is not None and level <= start_level:
            break
        index += 1
    return index


def _reorder_ams_backmatter_sections(markdown_text: str) -> str:
    # AMS pages can render Data availability after Appendix even when the
    # source article treats it as pre-appendix back matter. This remains
    # provider-owned until another publisher shows the same ordering artifact.
    blocks = [
        block for block in re.split(r"\n\s*\n", markdown_text) if normalize_text(block)
    ]
    data_start = next(
        (
            index
            for index, block in enumerate(blocks)
            if _is_markdown_heading(block, "data availability statement")
        ),
        -1,
    )
    appendix_start = next(
        (
            index
            for index, block in enumerate(blocks)
            if _is_markdown_heading(block, "appendix")
        ),
        -1,
    )
    if data_start < 0 or appendix_start < 0 or data_start < appendix_start:
        return markdown_text

    data_end = _section_end_index(blocks, data_start)
    data_section = blocks[data_start:data_end]
    del blocks[data_start:data_end]

    appendix_start = next(
        (
            index
            for index, block in enumerate(blocks)
            if _is_markdown_heading(block, "appendix")
        ),
        len(blocks),
    )
    ack_start = next(
        (
            index
            for index, block in enumerate(blocks)
            if _is_markdown_heading(block, "acknowledgments")
        ),
        -1,
    )
    insert_at = appendix_start
    if ack_start >= 0 and ack_start < appendix_start:
        insert_at = _section_end_index(blocks, ack_start)
    blocks[insert_at:insert_at] = data_section
    return "\n\n".join(blocks)


def _normalize_ams_label_text(text: str, *, kind: str | None = None) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""

    def figure_replacement(match: re.Match[str]) -> str:
        raw = match.group(0).lower()
        number = match.group(1)
        return f"Figure {number}." if raw.startswith("figure") else f"Fig. {number}."

    def table_replacement(match: re.Match[str]) -> str:
        return f"Table {match.group(1)}."

    if kind in {None, "figure"}:
        normalized = FIGURE_LABEL_PATTERN.sub(figure_replacement, normalized)
    if kind in {None, "table"}:
        normalized = TABLE_LABEL_PATTERN.sub(table_replacement, normalized)
    return normalized


def _normalize_nested_sup_sub(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    for node in list(container.find_all(["sup", "sub"])):
        if not isinstance(node, Tag):
            continue
        nested = node.find(node.name)
        if not isinstance(nested, Tag):
            continue
        nested_text = normalize_text(nested.get_text(" ", strip=True))
        if (
            not nested_text
            or normalize_text(node.get_text(" ", strip=True)) != nested_text
        ):
            continue
        node.clear()
        node.append(nested_text)


def _normalize_ams_formula_dom(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    for node in list(container.select("div.formula")):
        if not isinstance(node, Tag):
            continue
        label = _ams_equation_label(node)
        if label:
            node["data-equation-label"] = label
            node.attrs.pop("data-no-equation-label", None)
        elif _is_ams_unnumbered_formula(node):
            node["data-no-equation-label"] = "true"
            node.attrs.pop("data-equation-label", None)
        raw_classes = node.get("class") or []
        classes = (
            raw_classes.split() if isinstance(raw_classes, str) else list(raw_classes)
        )
        if "display-formula" not in classes:
            classes.append("display-formula")
            node["class"] = classes
    for selector in AMS_MATHJAX_RENDERED_SELECTORS:
        for node in list(container.select(selector)):
            if isinstance(node, Tag):
                _move_rendered_mathml_source_before_node(node)
    for selector in AMS_MATHJAX_RENDERED_SELECTORS:
        for node in list(container.select(selector)):
            if isinstance(node, Tag):
                node.decompose()
    for script in list(container.find_all("script")):
        if not isinstance(script, Tag):
            continue
        if not _is_mathml_script(script):
            continue
        parent = script.parent if isinstance(script.parent, Tag) else None
        if parent is not None and parent.find("math") is None:
            _insert_mathml_from_script(script)
        if parent is not None and parent.find("math") is not None:
            script.decompose()


def _ams_equation_label(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    candidates: list[str] = []
    for attr_name in ("data-label", "aria-label", "title"):
        value = normalize_text(str(node.get(attr_name) or ""))
        if value:
            candidates.append(value)
    for candidate in (
        node.select_one(".label"),
        node.find_previous_sibling(class_="label"),
    ):
        if isinstance(candidate, Tag):
            text = normalize_text(candidate.get_text(" ", strip=True))
            if text:
                candidates.append(text)
    for text in candidates:
        match = AMS_EQUATION_LABEL_PATTERN.search(text)
        if match:
            return f"Equation {match.group(1)}."

    node_id = normalize_text(str(node.get("id") or ""))
    id_match = AMS_EQUATION_ID_PATTERN.match(node_id)
    if id_match:
        return f"Equation {id_match.group(1)}."
    return ""


def _is_ams_unnumbered_formula(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    node_id = normalize_text(str(node.get("id") or ""))
    return bool(AMS_UNNUMBERED_EQUATION_ID_PATTERN.match(node_id))


def _is_mathml_script(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    if normalize_text(node.name or "").lower() != "script":
        return False
    script_type = normalize_text(str(node.get("type") or "")).lower()
    return script_type in MATHML_SCRIPT_TYPES


def _math_node_has_payload(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    return bool(normalize_text(node.get_text(" ", strip=True)))


def _has_mathml_outside_node(parent: Any, node: Any) -> bool:
    if Tag is None or not isinstance(parent, Tag) or not isinstance(node, Tag):
        return False
    for math_node in parent.find_all("math"):
        if not isinstance(math_node, Tag):
            continue
        if node in getattr(math_node, "parents", ()):
            continue
        if _math_node_has_payload(math_node):
            return True
    for script in parent.find_all("script"):
        if script is node or node in getattr(script, "parents", ()):
            continue
        if _is_mathml_script(script):
            return True
    return False


def _move_rendered_mathml_source_before_node(node: Any) -> None:
    if Tag is None or not isinstance(node, Tag):
        return
    parent = node.parent if isinstance(node.parent, Tag) else None
    if parent is None or _has_mathml_outside_node(parent, node):
        return
    for math_node in node.find_all("math"):
        if isinstance(math_node, Tag) and _math_node_has_payload(math_node):
            node.insert_before(math_node.extract())
            return
    for script in node.find_all("script"):
        if isinstance(script, Tag) and _is_mathml_script(script):
            node.insert_before(script.extract())
            return


def _insert_mathml_from_script(script: Any) -> bool:
    if BeautifulSoup is None or Tag is None or not isinstance(script, Tag):
        return False
    raw_mathml = (
        script.string if script.string is not None else script.decode_contents()
    )
    if not normalize_text(str(raw_mathml or "")):
        return False
    fragment = BeautifulSoup(str(raw_mathml or ""), choose_parser())
    math_node = fragment.find("math")
    if not isinstance(math_node, Tag) or not _math_node_has_payload(math_node):
        return False
    script.insert_before(math_node.extract())
    return True


def _render_ams_mathml_inline(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    element = mathml_element_from_html_node(node)
    if element is None:
        return ""
    expression = normalize_text(
        render_external_mathml_expression(element, display_mode=False)
    )
    if not expression:
        expression = normalize_text(render_mathml_expression(element))
    return f"${expression}$" if expression else ""


def _math_node_from_mathml_script(node: Any) -> Any:
    if BeautifulSoup is None or Tag is None or not isinstance(node, Tag):
        return None
    if not _is_mathml_script(node):
        return None
    raw_mathml = node.string if node.string is not None else node.decode_contents()
    if not normalize_text(str(raw_mathml or "")):
        return None
    fragment = BeautifulSoup(str(raw_mathml or ""), choose_parser())
    math_node = fragment.find("math")
    return math_node if isinstance(math_node, Tag) else None


def _ams_raw_inline_markdown_from_node(node: Any) -> str | None:
    if Tag is None or not isinstance(node, Tag):
        return None
    name = normalize_text(node.name or "").lower()
    if name == "inline-formula":
        math_node = node.find("math")
        if isinstance(math_node, Tag):
            return _render_ams_mathml_inline(math_node) or None
        for script in node.find_all("script"):
            math_from_script = _math_node_from_mathml_script(script)
            if isinstance(math_from_script, Tag):
                return _render_ams_mathml_inline(math_from_script) or None
        return None
    if name == "math":
        if is_display_formula_node(node):
            return None
        return _render_ams_mathml_inline(node) or None
    if name == "script":
        math_from_script = _math_node_from_mathml_script(node)
        if isinstance(math_from_script, Tag):
            return _render_ams_mathml_inline(math_from_script) or None
    return None


def _first_text_in_ams_subtree(node: Any) -> str | None:
    if NavigableString is not None and isinstance(node, NavigableString):
        return str(node)
    if Tag is None or not isinstance(node, Tag):
        return None
    if normalize_text(node.name or "").lower() in {"math", "script", "style"}:
        return None
    for child in node.descendants:
        if NavigableString is not None and isinstance(child, NavigableString):
            return str(child)
    return None


def _next_ams_text_after_node(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    current: Any = node
    while isinstance(getattr(current, "parent", None), Tag):
        pending_space = ""
        for sibling in current.next_siblings:
            text = _first_text_in_ams_subtree(sibling)
            if text is None:
                continue
            if normalize_text(text):
                return f"{pending_space}{text}"
            pending_space += text
        current = current.parent
    return ""


def _restore_ams_supsub_parenthesis_spacing(source_node: Any, text: str) -> str:
    if Tag is None or not isinstance(source_node, Tag) or not text:
        return text
    candidates: list[Any] = []
    if normalize_text(source_node.name or "").lower() in {"sub", "sup"}:
        candidates.append(source_node)
    candidates.extend(source_node.find_all(["sub", "sup"]))
    restored = text
    for candidate in candidates:
        if not isinstance(candidate, Tag):
            continue
        next_text = _next_ams_text_after_node(candidate)
        if not re.match(r"\s+\(", next_text or ""):
            continue
        tag_name = normalize_text(candidate.name or "").lower()
        marker = f"</{tag_name}>("
        replacement = f"</{tag_name}> ("
        if marker in restored:
            restored = restored.replace(marker, replacement, 1)
    return restored


def _normalize_ams_inline_text_spacing(text: str, *, source_node: Any = None) -> str:
    normalized = normalize_text(text)
    normalized = _restore_ams_supsub_parenthesis_spacing(source_node, normalized)
    return normalize_text(normalized)


def _render_ams_inline_text(node: Any) -> str:
    try:
        text = render_html_inline_node(
            node,
            policy="body",
            raw_markdown_from_node=_ams_raw_inline_markdown_from_node,
        )
    except Exception:
        from ._html_section_markdown import render_clean_text_from_html

        text = render_clean_text_from_html(node, collapse_prose_line_breaks=True)
    return _normalize_ams_inline_text_spacing(text, source_node=node)


def _caption_paragraph_texts(wrapper: Any) -> list[str]:
    if Tag is None or not isinstance(wrapper, Tag):
        return []
    texts: list[str] = []
    for paragraph in wrapper.find_all("p", recursive=False):
        if not isinstance(paragraph, Tag):
            continue
        class_blob = " ".join(
            str(item) for item in (paragraph.get("class") or [])
        ).lower()
        if "citation" in class_blob:
            continue
        text = _render_ams_inline_text(paragraph)
        if text:
            texts.append(text)
        paragraph.decompose()
    return texts


def _normalize_ams_figure_captions(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    for wrapper in list(container.select(".figure-text-wrapper")):
        if not isinstance(wrapper, Tag):
            continue
        figcaption = wrapper.find("figcaption")
        if not isinstance(figcaption, Tag):
            continue
        label = _normalize_ams_label_text(
            figcaption.get_text(" ", strip=True),
            kind="figure",
        )
        caption_parts = _caption_paragraph_texts(wrapper)
        if label or caption_parts:
            figcaption.clear()
            figcaption.append(
                " ".join(part for part in [label, *caption_parts] if part)
            )


def _normalize_ams_table_captions(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    for table_wrap in list(container.select(".tableWrap")):
        if not isinstance(table_wrap, Tag):
            continue
        label_node = table_wrap.select_one(".tableWrapLabel")
        if isinstance(label_node, Tag):
            label = _normalize_ams_label_text(
                label_node.get_text(" ", strip=True),
                kind="table",
            )
            if label:
                label_node.clear()
                label_node.append(label)
        caption_node = table_wrap.select_one(".tableWrapCaption")
        if isinstance(caption_node, Tag):
            for paragraph in caption_node.find_all("p"):
                if not isinstance(paragraph, Tag):
                    continue
                text = _render_ams_inline_text(paragraph)
                if text:
                    paragraph.clear()
                    paragraph.append(text)


def _footnote_label_text(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    for selector in (".label", "label"):
        label_node = node.select_one(selector)
        if isinstance(label_node, Tag):
            text = normalize_text(label_node.get_text("", strip=True)).strip("[]().")
            if text:
                return text
    node_id = normalize_text(str(node.get("id") or ""))
    match = re.search(r"(?:^|[-_])FN([A-Za-z0-9]+)$", node_id, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _normalize_ams_footnotes(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    soup = _soup_root(container)
    if soup is None:
        return
    for group in list(container.select(".footnoteGroup")):
        if not isinstance(group, Tag):
            continue
        notes: list[tuple[str, str]] = []
        for note in group.select(".footnote"):
            if not isinstance(note, Tag):
                continue
            label = _footnote_label_text(note)
            if not label:
                continue
            for label_node in list(note.select(".label, label")):
                if isinstance(label_node, Tag):
                    label_node.decompose()
            text = _render_ams_inline_text(note)
            if text:
                notes.append((label, text))
        if not notes:
            continue
        group.clear()
        heading = soup.new_tag("h2")
        heading.string = "Footnotes"
        group.append(heading)
        for label, text in notes:
            paragraph = soup.new_tag("p")
            paragraph.append(f"<sup>{label}</sup> {text}")
            group.append(paragraph)


def _is_descendant_of_any_ams_node(node: Any, ancestors: list[Any]) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    return any(
        isinstance(ancestor, Tag) and ancestor in getattr(node, "parents", ())
        for ancestor in ancestors
    )


def _has_ams_inline_skip_ancestor(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return True
    for parent in getattr(node, "parents", ()):
        if not isinstance(parent, Tag):
            continue
        parent_name = normalize_text(parent.name or "").lower()
        if parent_name in AMS_INLINE_SKIP_ANCESTOR_TAGS:
            return True
    return False


def _should_normalize_ams_inline_node(node: Any) -> bool:
    if Tag is None or not isinstance(node, Tag):
        return False
    name = normalize_text(node.name or "").lower()
    if name not in AMS_INLINE_MARKDOWN_TAGS:
        return False
    if name == "math":
        return not _has_ams_inline_skip_ancestor(node) and not is_display_formula_node(
            node
        )
    if _has_ams_inline_skip_ancestor(node):
        return False
    if is_display_formula_node(node):
        return False
    rendered = _render_ams_inline_text(node)
    if not rendered:
        return False
    source_text = normalize_text(node.get_text(" ", strip=True))
    return rendered != source_text


def _normalize_ams_inline_markup_nodes(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    selected: list[Any] = []
    for node in list(container.find_all(AMS_INLINE_MARKDOWN_TAGS)):
        if not isinstance(node, Tag) or node.parent is None:
            continue
        if _is_descendant_of_any_ams_node(node, selected):
            continue
        if not _should_normalize_ams_inline_node(node):
            continue
        selected.append(node)
    for node in selected:
        if not isinstance(node, Tag) or node.parent is None:
            continue
        rendered = _render_ams_inline_text(node)
        if not rendered:
            continue
        replacement = (
            NavigableString(rendered) if NavigableString is not None else rendered
        )
        node.replace_with(replacement)


def _normalize_ams_asset_nodes(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    nodes: list[Tag] = []
    for selector in ("figure", ".tableWrap"):
        try:
            nodes.extend(
                node for node in container.select(selector) if isinstance(node, Tag)
            )
        except Exception:
            continue
    for node in nodes:
        full_size_href = _ams_gallery_href(node) or _ams_full_image_src(node)
        inline_image = _ams_inline_image(node)
        if isinstance(inline_image, Tag):
            lazy_src = normalize_text(str(inline_image.get("data-image-src") or ""))
            if lazy_src:
                inline_image["data-src"] = lazy_src
            if full_size_href:
                inline_image["data-full-size"] = full_size_href
        for image in node.find_all("img"):
            if not isinstance(image, Tag):
                continue
            lazy_src = normalize_text(str(image.get("data-image-src") or ""))
            if lazy_src:
                image["data-src"] = lazy_src


def _drop_ams_chrome(container: Any) -> None:
    if Tag is None or not isinstance(container, Tag):
        return
    cleanup_policy = cleanup_policy_for_profile("ams")
    for selector in cleanup_policy.dom_postprocess_cleanup_selectors:
        try:
            matches = list(container.select(selector))
        except Exception:
            continue
        for node in matches:
            if isinstance(node, Tag) and node is not container:
                node.decompose()


def _normalize_ams_dom(container: Any) -> None:
    _normalize_nested_sup_sub(container)
    _normalize_ams_formula_dom(container)
    _normalize_ams_figure_captions(container)
    _normalize_ams_table_captions(container)
    _normalize_ams_footnotes(container)
    _normalize_ams_inline_markup_nodes(container)
    _normalize_ams_asset_nodes(container)
    _drop_ams_chrome(container)


def _ams_gallery_href(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    for anchor in node.find_all("a", href=True):
        href = normalize_text(str(anchor.get("href") or ""))
        if not href or href.startswith("#"):
            continue
        hint_blob = " ".join(
            normalize_text(str(value or "")).lower()
            for value in (
                anchor.get_text(" ", strip=True),
                anchor.get("title"),
                anchor.get("aria-label"),
            )
        )
        if "/full-" in href.lower() or "view in gallery" in hint_blob:
            return href
    return ""


def _ams_full_image_src(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    for image in node.find_all("img"):
        candidate = normalize_text(str(image.get("data-image-src") or ""))
        if "/full-" in candidate.lower() or "/full/" in candidate.lower():
            return candidate
    return ""


def _ams_inline_image(node: Any) -> Any:
    if Tag is None or not isinstance(node, Tag):
        return None
    for image in node.find_all("img"):
        if not isinstance(image, Tag):
            continue
        if image.find_parent("pf-box") is not None:
            continue
        return image
    return None


def _normalize_ams_asset_html(html_text: str) -> str:
    if BeautifulSoup is None:
        return html_text
    soup = BeautifulSoup(html_text, choose_parser())
    _normalize_ams_dom(soup)
    return str(soup)


def _table_label_text(node: Any) -> str:
    if Tag is None or not isinstance(node, Tag):
        return "Table"
    for selector in (".tableWrapLabel", ".label"):
        candidate = node.select_one(selector)
        if isinstance(candidate, Tag):
            text = _normalize_ams_label_text(
                candidate.get_text(" ", strip=True), kind="table"
            )
            if text:
                return text
    title = _normalize_ams_label_text(str(node.get("title") or ""), kind="table")
    match = TABLE_LABEL_PATTERN.search(title)
    if match:
        return f"Table {match.group(1)}."
    text = _normalize_ams_label_text(node.get_text(" ", strip=True), kind="table")
    match = TABLE_LABEL_PATTERN.search(text)
    if match:
        return f"Table {match.group(1)}."
    return "Table"


def _table_caption_text(node: Any, label: str) -> str:
    if Tag is None or not isinstance(node, Tag):
        return ""
    candidates: list[str] = []
    for selector in (".tableWrapCaption", ".caption", "figcaption", "caption"):
        caption_node = node.select_one(selector)
        if isinstance(caption_node, Tag):
            text = _render_ams_inline_text(caption_node)
            if text:
                candidates.append(text)
    title = normalize_text(str(node.get("title") or ""))
    if title:
        candidates.append(title)
    label_text = normalize_text(label).rstrip(".")
    for text in candidates:
        if label_text:
            text = re.sub(
                rf"^{re.escape(label_text)}\.?\s*", "", text, flags=re.IGNORECASE
            )
        text = normalize_text(text).lstrip(".:;,-) ]")
        if text:
            return text
    return ""


def _absolute_url(source_url: str, value: str) -> str:
    return urllib.parse.urljoin(source_url, normalize_text(value))


def _ams_asset_url_keys(asset: dict[str, str]) -> set[str]:
    return {
        normalize_text(str(asset.get(field) or ""))
        for field in (
            "url",
            "full_size_url",
            "preview_url",
            "source_url",
            "original_url",
            "path",
        )
        if normalize_text(str(asset.get(field) or ""))
    }


def _extract_ams_table_assets(html_text: str, source_url: str) -> list[dict[str, str]]:
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(_normalize_ams_asset_html(html_text), choose_parser())
    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in soup.select(".tableWrap"):
        if not isinstance(node, Tag):
            continue
        image = _ams_inline_image(node)
        full_size_url = _ams_gallery_href(node) or _ams_full_image_src(node)
        preview_url = ""
        if isinstance(image, Tag):
            preview_url = normalize_text(
                str(
                    image.get("data-src")
                    or image.get("data-image-src")
                    or image.get("src")
                    or ""
                )
            )
            full_size_url = (
                normalize_text(str(image.get("data-full-size") or "")) or full_size_url
            )
        url = full_size_url or preview_url
        if not url:
            continue
        absolute = _absolute_url(source_url, url)
        if absolute in seen:
            continue
        seen.add(absolute)
        label = _table_label_text(node)
        caption = _table_caption_text(node, label)
        asset: dict[str, str] = {
            "kind": "table",
            "heading": label or "Table",
            "caption": caption,
            "url": absolute,
            "section": "body",
        }
        dom_id = normalize_text(str(node.get("id") or ""))
        if dom_id:
            asset["dom_id"] = dom_id
        if preview_url:
            asset["preview_url"] = _absolute_url(source_url, preview_url)
        if full_size_url:
            asset["full_size_url"] = _absolute_url(source_url, full_size_url)
        assets.append(asset)
    return assets


def scoped_asset_extractor(
    body_html_text: str,
    source_url: str,
    *,
    asset_profile,
    supplementary_html_text: str | None = None,
) -> list[dict[str, str]]:
    from .atypon_browser_workflow.asset_scopes import extract_scoped_html_assets

    normalized_body_html = _normalize_ams_asset_html(body_html_text)
    # The Atypon shared extractor owns figures, formulas, and supplementary assets.
    # AMS adds image-only tableWrap screenshots here because they are table surrogates.
    assets = extract_scoped_html_assets(
        normalized_body_html,
        source_url,
        asset_profile=asset_profile,
        supplementary_html_text=(
            _normalize_ams_asset_html(supplementary_html_text)
            if supplementary_html_text is not None
            else None
        ),
    )
    table_assets = _extract_ams_table_assets(normalized_body_html, source_url)
    table_urls = {url for asset in table_assets for url in _ams_asset_url_keys(asset)}
    if table_urls:
        assets = [
            asset for asset in assets if not (_ams_asset_url_keys(asset) & table_urls)
        ]
    assets.extend(table_assets)
    return assets


def refine_selected_container(
    node: Any,
    *,
    direct_child_tags,
    class_tokens,
    container_completeness_score,
    score_container,
) -> Any:
    del direct_child_tags, class_tokens
    root = node
    while getattr(root, "parent", None) is not None:
        root = root.parent

    candidates: list[Any] = []
    for selector in (
        ".component-content-item.component-container.container-fulltext-display",
        "#articleBody",
        "#contentRoot",
        ".component-content-item.component-content-html",
    ):
        try:
            candidates.extend(root.select(selector))
        except Exception:
            continue
    if not candidates:
        return node

    def candidate_key(candidate: Any) -> tuple[int, int, float]:
        text_length = len(normalize_text(candidate.get_text(" ", strip=True)))
        return (
            container_completeness_score(candidate),
            text_length,
            score_container(candidate),
        )

    return max(candidates, key=candidate_key)


def select_content_nodes(
    container: Any,
    *,
    structural_abstract_nodes,
    nodes_from_selectors,
    content_abstract_selectors,
    content_body_selectors,
    select_availability_nodes,
    dedupe_top_level_nodes,
    is_tag,
) -> list[Any]:
    del content_abstract_selectors, content_body_selectors
    abstract_nodes = list(structural_abstract_nodes(container))
    if not abstract_nodes:
        abstract_nodes = nodes_from_selectors(
            container,
            (
                "#abstracts",
                "section[role='doc-abstract']",
                ".abstract",
                ".abstractSection",
                ".NLM_abstract",
                ".component-content-summary.abstract_or_excerpt",
                ".component-container.container-abstract-display",
                "section.abstract",
            ),
        )
    body_nodes = nodes_from_selectors(
        container,
        (
            "#articleBody",
            "#contentRoot",
            "#bodymatter",
            "[property='articleBody']",
            "[itemprop='articleBody']",
            ".article__body",
            ".article-body",
            ".article__fulltext",
            ".articleFullText",
            ".NLM_body",
            ".component-content-html",
            ".container-fulltext-display",
            ".body",
            "section.body",
        ),
    )
    availability_nodes = select_availability_nodes(container, body_nodes)
    selected = [
        node
        for node in [*abstract_nodes, *body_nodes, *availability_nodes]
        if is_tag(node)
    ]
    return dedupe_top_level_nodes(selected)


def _is_acknowledgment_heading(value: Any) -> bool:
    normalized = normalize_text(str(value or "")).rstrip(".: ").lower()
    return normalized in {
        "acknowledgment",
        "acknowledgments",
        "acknowledgement",
        "acknowledgements",
    }


def _is_ams_data_availability_heading(value: Any) -> bool:
    normalized = normalize_text(str(value or "")).rstrip(".: ").lower()
    return normalized in {"data availability", "data availability statement"}


def _normalize_ams_section_hints(section_hints: Any) -> list[dict[str, Any]]:
    normalized_hints: list[dict[str, Any]] = []
    for index, hint in enumerate(section_hints or []):
        if not isinstance(hint, dict):
            continue
        normalized_hint = dict(hint)
        heading = normalize_text(str(normalized_hint.get("heading") or "")).rstrip(
            ".: "
        )
        if heading:
            normalized_hint["heading"] = heading
        if _is_acknowledgment_heading(normalized_hint.get("heading")):
            normalized_hint["kind"] = "body"
        if _is_ams_data_availability_heading(normalized_hint.get("heading")):
            normalized_hint["kind"] = "data_availability"
        normalized_hint.setdefault("order", index)
        normalized_hints.append(normalized_hint)
    return normalized_hints


def normalize_article_model(article: Any) -> Any:
    for section in getattr(article, "sections", []) or []:
        text = getattr(section, "text", "")
        if isinstance(text, str) and text:
            section.text = _normalize_ams_markdown_text(text)
    return article


def finalize_extraction(
    html_text: str,
    source_url: str,
    markdown_text: str,
    extraction: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    del source_url, metadata
    extraction = dict(extraction)
    extraction["section_hints"] = _normalize_ams_section_hints(
        extraction.get("section_hints")
    )
    authors = extraction.get("extracted_authors")
    if not authors:
        extracted_authors = extract_authors(html_text)
        extraction["extracted_authors"] = extracted_authors
    references = extract_references(html_text)
    if references:
        extraction["references"] = references
    markdown_text = _normalize_ams_markdown_text(markdown_text)
    return markdown_text, extraction
