"""Generic NLM/JATS XML extraction helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import re
import urllib.parse
import xml.etree.ElementTree as ET

from ..models import SemanticLosses
from ..publisher_identity import extract_doi, normalize_doi
from ..utils import dedupe_authors, normalize_text
from ._article_markdown_common import (
    XLINK_HREF,
    XLINK_TITLE,
    child_text,
    collect_conversion_notes,
    first_child,
    first_descendant,
    iter_children,
    iter_descendants,
    normalize_lines,
    normalize_table_cell_text,
    render_figure_block,
    render_inline_text,
    render_table_block,
    xml_local_name,
)
from ._article_markdown_math import FormulaRenderResult, render_display_formula_result


JATS_BLOCK_LOCAL_NAMES = {
    "disp-formula",
    "fig",
    "list",
    "supplementary-material",
    "table",
    "table-wrap",
}


@dataclass(frozen=True)
class JatsExtraction:
    metadata: dict[str, Any]
    abstract_sections: list[dict[str, Any]]
    markdown_text: str
    assets: list[dict[str, Any]]
    references: list[dict[str, Any]]
    semantic_losses: SemanticLosses
    conversion_notes: list[str] = field(default_factory=list)


def _text_from_first_descendant(element: ET.Element | None, local_name: str) -> str:
    return normalize_text(render_inline_text(first_descendant(element, local_name)))


def _attribute_text(element: ET.Element | None, *names: str) -> str:
    if element is None:
        return ""
    for name in names:
        value = normalize_text(str(element.get(name) or ""))
        if value:
            return value
    return ""


def _element_id(element: ET.Element | None) -> str:
    return _attribute_text(element, "id", "{http://www.w3.org/XML/1998/namespace}id")


def _href(element: ET.Element | None) -> str:
    return _attribute_text(element, XLINK_HREF, "href")


def _urljoin(base_url: str, value: str | None) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    return urllib.parse.urljoin(base_url, normalized)


def _extract_contrib_name(contrib: ET.Element) -> str:
    name = first_child(contrib, "name")
    if name is not None:
        given = normalize_text(child_text(name, "given-names") or child_text(name, "given-name"))
        surname = normalize_text(child_text(name, "surname"))
        if given or surname:
            return normalize_text(" ".join(item for item in (given, surname) if item))
    return normalize_text(child_text(contrib, "collab") or child_text(contrib, "collaboration"))


def extract_jats_authors(root: ET.Element) -> list[str]:
    authors: list[str] = []
    for contrib in iter_descendants(root, "contrib"):
        contrib_type = normalize_text(str(contrib.get("contrib-type") or "")).lower()
        if contrib_type and contrib_type != "author":
            continue
        name = _extract_contrib_name(contrib)
        if name:
            authors.append(name)
    return dedupe_authors(authors)


def _article_meta(root: ET.Element) -> ET.Element | None:
    front = first_child(root, "front")
    return first_descendant(front, "article-meta")


def _journal_meta(root: ET.Element) -> ET.Element | None:
    front = first_child(root, "front")
    return first_descendant(front, "journal-meta")


def _article_id(article_meta: ET.Element | None, pub_id_type: str) -> str:
    for node in iter_children(article_meta, "article-id"):
        if normalize_text(str(node.get("pub-id-type") or "")).lower() == pub_id_type:
            return normalize_text("".join(node.itertext()))
    return ""


def _publication_date(article_meta: ET.Element | None) -> str:
    pub_dates = iter_children(article_meta, "pub-date")
    if not pub_dates:
        return ""
    preferred = pub_dates[0]
    for candidate in pub_dates:
        pub_type = normalize_text(str(candidate.get("pub-type") or candidate.get("date-type") or "")).lower()
        if pub_type in {"epub", "ppub", "collection"}:
            preferred = candidate
            break
    parts = [
        normalize_text(child_text(preferred, "day")),
        normalize_text(child_text(preferred, "month")),
        normalize_text(child_text(preferred, "year")),
    ]
    return normalize_text(" ".join(part for part in parts if part))


def _license_urls(article_meta: ET.Element | None) -> list[str]:
    urls: list[str] = []
    permissions = first_child(article_meta, "permissions")
    for node in iter_descendants(permissions, "ext-link"):
        href = _href(node)
        if href and href not in urls:
            urls.append(href)
    return urls


def extract_jats_metadata(
    root: ET.Element,
    *,
    base_metadata: Mapping[str, Any] | None = None,
    source_url: str = "",
) -> dict[str, Any]:
    base = dict(base_metadata or {})
    article_meta = _article_meta(root)
    journal_meta = _journal_meta(root)
    title = _text_from_first_descendant(article_meta, "article-title")
    doi = normalize_doi(_article_id(article_meta, "doi") or str(base.get("doi") or ""))
    journal_title = _text_from_first_descendant(journal_meta, "journal-title")
    abstract_node = first_child(article_meta, "abstract")
    abstract_text = normalize_text("\n\n".join(_render_paragraph_texts(abstract_node)))

    metadata = dict(base)
    metadata.update(
        {
            "title": title or normalize_text(str(base.get("title") or "")) or None,
            "doi": doi or normalize_doi(str(base.get("doi") or "")) or None,
            "journal_title": journal_title or normalize_text(str(base.get("journal_title") or "")) or None,
            "published": _publication_date(article_meta) or normalize_text(str(base.get("published") or "")) or None,
            "authors": extract_jats_authors(root) or list(base.get("authors") or []),
            "abstract": abstract_text or normalize_text(str(base.get("abstract") or "")) or None,
            "landing_page_url": normalize_text(str(base.get("landing_page_url") or source_url or "")) or None,
            "license_urls": list(dict.fromkeys([*list(base.get("license_urls") or []), *_license_urls(article_meta)])),
            "references": list(base.get("references") or []),
        }
    )
    return metadata


def _render_paragraph_texts(parent: ET.Element | None) -> list[str]:
    texts: list[str] = []
    for child in iter_children(parent):
        local_name = xml_local_name(child.tag)
        if local_name == "title":
            continue
        if local_name == "p":
            text = render_inline_text(child, skip_local_names=JATS_BLOCK_LOCAL_NAMES)
            if text:
                texts.append(text)
            continue
        if local_name in {"sec", "notes", "ack", "app"}:
            nested = _render_paragraph_texts(child)
            texts.extend(nested)
    return texts


def _heading_text(section: ET.Element) -> str:
    title = normalize_text(child_text(section, "title"))
    label = normalize_text(child_text(section, "label"))
    if title and label:
        return normalize_text(f"{label} {title}")
    return title or label


def _caption_text(container: ET.Element | None) -> str:
    caption = first_child(container, "caption")
    if caption is None:
        return ""
    paragraphs = _render_paragraph_texts(caption)
    if paragraphs:
        return normalize_text("\n\n".join(paragraphs))
    return normalize_text(render_inline_text(caption))


def _graphic_url(figure: ET.Element, source_url: str) -> str:
    for node in iter_descendants(figure, "graphic"):
        url = _urljoin(source_url, _href(node))
        if url:
            return url
    return ""


def _figure_entry(figure: ET.Element, source_url: str) -> dict[str, Any] | None:
    url = _graphic_url(figure, source_url)
    label = normalize_text(child_text(figure, "label")) or "Figure"
    figure_id = _element_id(figure)
    caption = _caption_text(figure)
    key = figure_id or url or label
    if not key:
        return None
    entry: dict[str, Any] = {
        "kind": "figure",
        "key": key,
        "anchor_key": key,
        "heading": label,
        "caption": caption,
        "section": "body",
        "render_state": "inline",
    }
    if url:
        entry.update({"link": url, "original_url": url})
    return entry


def _has_table_spans(table: ET.Element | None) -> bool:
    if table is None:
        return False
    span_attrs = {"namest", "nameend", "morerows", "rowspan", "colspan"}
    return any(any(node.get(attr) for attr in span_attrs) for node in table.iter() if isinstance(node.tag, str))


def _table_node(table_wrap: ET.Element) -> ET.Element | None:
    if xml_local_name(table_wrap.tag) == "table":
        return table_wrap
    return first_descendant(table_wrap, "table")


def _table_rows(table: ET.Element | None) -> list[list[str]]:
    if table is None:
        return []
    rows: list[list[str]] = []
    for row in table.iter():
        if not isinstance(row.tag, str) or xml_local_name(row.tag) not in {"row", "tr"}:
            continue
        cells: list[str] = []
        for cell in iter_children(row):
            if xml_local_name(cell.tag) not in {"entry", "td", "th"}:
                continue
            cells.append(normalize_table_cell_text(render_inline_text(cell)))
        if cells:
            rows.append(cells)
    if len(rows) <= 1:
        return rows
    max_width = max(len(row) for row in rows)
    return [row + [""] * (max_width - len(row)) for row in rows]


def _table_footnotes(table_wrap: ET.Element) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for local_name in ("table-wrap-foot", "fn"):
        for node in iter_descendants(table_wrap, local_name):
            text = normalize_text("\n\n".join(_render_paragraph_texts(node)) or render_inline_text(node))
            if text and text not in seen:
                notes.append(text)
                seen.add(text)
    return notes


def _table_entry(table_wrap: ET.Element) -> tuple[dict[str, Any] | None, bool]:
    label = normalize_text(child_text(table_wrap, "label")) or "Table"
    caption = _caption_text(table_wrap)
    table = _table_node(table_wrap)
    rows = _table_rows(table)
    key = _element_id(table_wrap) or _element_id(table) or label
    lossy = _has_table_spans(table)
    if rows:
        entry: dict[str, Any] = {
            "kind": "table",
            "table_render_kind": "structured",
            "key": key,
            "anchor_key": key,
            "heading": label,
            "caption": caption,
            "rows": rows,
            "footnotes": _table_footnotes(table_wrap),
            "section": "body",
            "render_state": "inline",
        }
        if lossy:
            message = (
                "Merged table spans were flattened into rectangular Markdown cells; "
                "rowspan/colspan layout fidelity was reduced."
            )
            entry["lossy_message"] = message
            entry["conversion_notes"] = [message]
        return entry, lossy
    if caption:
        return {
            "kind": "table",
            "table_render_kind": "fallback",
            "key": key,
            "anchor_key": key,
            "heading": label,
            "caption": caption,
            "footnotes": _table_footnotes(table_wrap),
            "section": "body",
            "render_state": "inline",
            "fallback_message": "Table content could not be converted to Markdown; caption text was retained.",
            "conversion_notes": ["Table content could not be converted to Markdown; caption text was retained."],
        }, True
    return None, False


def _supplementary_entries(root: ET.Element, source_url: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if xml_local_name(node.tag) not in {"inline-supplementary-material", "supplementary-material"}:
            continue
        url = _urljoin(source_url, _href(node))
        if not url:
            continue
        text = normalize_text(render_inline_text(node))
        title = (
            normalize_text(str(node.get(XLINK_TITLE) or node.get("content-type") or ""))
            or text
            or "Supplementary material"
        )
        key = url or _element_id(node) or title
        if not key or key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "kind": "supplementary",
            "key": key,
            "anchor_key": key,
            "heading": title,
            "caption": text if text and text != title else "",
            "section": "supplementary",
        }
        if url:
            entry.update({"link": url, "original_url": url})
        entries.append(entry)
    return entries


def _render_list(node: ET.Element, *, ordered: bool) -> list[str]:
    lines: list[str] = []
    index = 1
    for item in iter_children(node, "list-item"):
        text = normalize_text(" ".join(_render_paragraph_texts(item)) or render_inline_text(item))
        if not text:
            continue
        marker = f"{index}." if ordered else "-"
        lines.append(f"{marker} {text}")
        index += 1
    if lines:
        lines.append("")
    return lines


def _render_blocks(
    parent: ET.Element | None,
    *,
    heading_level: int,
    source_url: str,
    assets: list[dict[str, Any]],
    table_entries: list[dict[str, Any]],
    formula_renders: list[FormulaRenderResult],
) -> list[str]:
    if parent is None:
        return []

    lines: list[str] = []
    for child in iter_children(parent):
        local_name = xml_local_name(child.tag)
        if local_name in {"title", "label"}:
            continue
        if local_name == "sec":
            child_lines = _render_blocks(
                child,
                heading_level=heading_level + 1,
                source_url=source_url,
                assets=assets,
                table_entries=table_entries,
                formula_renders=formula_renders,
            )
            heading = _heading_text(child)
            if heading and child_lines:
                lines.extend([f"{'#' * heading_level} {heading}", ""])
            lines.extend(child_lines)
            continue
        if local_name == "p":
            text = render_inline_text(child, skip_local_names=JATS_BLOCK_LOCAL_NAMES)
            if text:
                lines.extend([text, ""])
            for nested in iter_children(child):
                nested_name = xml_local_name(nested.tag)
                if nested_name == "fig":
                    entry = _figure_entry(nested, source_url)
                    if entry is not None:
                        assets.append(entry)
                        if entry.get("link"):
                            lines.extend(render_figure_block(entry))
                    continue
                if nested_name in {"table-wrap", "table"}:
                    entry, _lossy = _table_entry(nested)
                    if entry is not None:
                        table_entries.append(entry)
                        assets.append(entry)
                        lines.extend(render_table_block(entry))
                    continue
                if nested_name == "disp-formula":
                    result = render_display_formula_result(nested)
                    if result.lines:
                        formula_renders.append(result)
                        lines.extend(result.lines)
                    continue
                if nested_name == "list":
                    list_type = normalize_text(str(nested.get("list-type") or "")).lower()
                    lines.extend(_render_list(nested, ordered=list_type in {"order", "ordered", "decimal"}))
            continue
        if local_name == "fig":
            entry = _figure_entry(child, source_url)
            if entry is not None:
                assets.append(entry)
                if entry.get("link"):
                    lines.extend(render_figure_block(entry))
                elif entry.get("caption"):
                    lines.extend([f"**{entry['heading']}** {entry['caption']}", ""])
            continue
        if local_name in {"table-wrap", "table"}:
            entry, _lossy = _table_entry(child)
            if entry is not None:
                table_entries.append(entry)
                assets.append(entry)
                lines.extend(render_table_block(entry))
            continue
        if local_name == "disp-formula":
            result = render_display_formula_result(child)
            if result.lines:
                formula_renders.append(result)
                lines.extend(result.lines)
            continue
        if local_name == "list":
            list_type = normalize_text(str(child.get("list-type") or "")).lower()
            lines.extend(_render_list(child, ordered=list_type in {"order", "ordered", "decimal"}))
            continue
        if local_name in {"notes", "ack", "app"}:
            heading = normalize_text(child_text(child, "title")) or _note_heading(child)
            child_lines = _render_blocks(
                child,
                heading_level=heading_level + 1,
                source_url=source_url,
                assets=assets,
                table_entries=table_entries,
                formula_renders=formula_renders,
            )
            if heading and child_lines:
                lines.extend([f"{'#' * heading_level} {heading}", ""])
            lines.extend(child_lines)
            continue
        lines.extend(
            _render_blocks(
                child,
                heading_level=heading_level,
                source_url=source_url,
                assets=assets,
                table_entries=table_entries,
                formula_renders=formula_renders,
            )
        )
    return lines


def _note_heading(node: ET.Element) -> str:
    notes_type = normalize_text(str(node.get("notes-type") or "")).lower()
    known = {
        "dataavailability": "Data availability",
        "codeavailability": "Code availability",
        "authorcontribution": "Author contributions",
        "competinginterests": "Competing interests",
        "financialsupport": "Financial support",
        "reviewstatement": "Review statement",
    }
    return known.get(notes_type, "")


def _back_matter_lines(
    root: ET.Element,
    *,
    source_url: str,
    assets: list[dict[str, Any]],
    table_entries: list[dict[str, Any]],
    formula_renders: list[FormulaRenderResult],
) -> list[str]:
    back = first_child(root, "back")
    if back is None:
        return []
    lines: list[str] = []
    for child in iter_children(back):
        local_name = xml_local_name(child.tag)
        if local_name in {"notes", "ack"}:
            child_lines = _render_blocks(
                child,
                heading_level=3,
                source_url=source_url,
                assets=assets,
                table_entries=table_entries,
                formula_renders=formula_renders,
            )
            heading = normalize_text(child_text(child, "title")) or _note_heading(child)
            if heading and child_lines:
                lines.extend([f"## {heading}", ""])
            lines.extend(child_lines)
        elif local_name == "app-group":
            supplement_lines = _render_supplementary_materials(child, source_url)
            if supplement_lines:
                lines.extend(supplement_lines)
    return lines


def _render_supplementary_materials(node: ET.Element, source_url: str) -> list[str]:
    bullets: list[str] = []
    for entry in _supplementary_entries(node, source_url):
        link = normalize_text(str(entry.get("link") or entry.get("url") or ""))
        heading = normalize_text(str(entry.get("heading") or "Supplementary material"))
        caption = normalize_text(str(entry.get("caption") or ""))
        if link:
            bullet = f"- [{heading}]({link})"
        else:
            bullet = f"- {heading}"
        if caption and caption != heading:
            bullet = f"{bullet}: {caption}"
        bullets.append(bullet)
    return ["## Supplementary Materials", "", *bullets, ""] if bullets else []


def _reference_year(ref: ET.Element) -> str:
    for node in iter_descendants(ref, "year"):
        year = normalize_text("".join(node.itertext()))
        if year:
            return year
    match = re.search(r"\b(19|20)\d{2}\b", normalize_text(" ".join(ref.itertext())))
    return match.group(0) if match else ""


def _reference_title(ref: ET.Element) -> str:
    for local_name in ("article-title", "chapter-title", "source"):
        title = _text_from_first_descendant(ref, local_name)
        if title:
            return title
    return ""


def _reference_doi(ref: ET.Element) -> str:
    for node in iter_descendants(ref, "pub-id"):
        if normalize_text(str(node.get("pub-id-type") or "")).lower() == "doi":
            doi = normalize_doi("".join(node.itertext()))
            if doi:
                return doi
    for node in iter_descendants(ref, "ext-link"):
        for value in (_href(node), "".join(node.itertext())):
            doi = extract_doi(value)
            if doi:
                return normalize_doi(doi)
    doi = extract_doi(" ".join(ref.itertext()))
    return normalize_doi(doi) if doi else ""


def extract_jats_references(root: ET.Element) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for index, ref in enumerate(iter_descendants(root, "ref"), start=1):
        label = normalize_text(child_text(ref, "label"))
        citation = first_child(ref, "mixed-citation")
        if citation is None:
            citation = first_child(ref, "element-citation")
        body = normalize_text(render_inline_text(citation) if citation is not None else " ".join(ref.itertext()))
        if label:
            label_text = label.strip("[](). ")
            body = normalize_text(re.sub(rf"^\[?\s*{re.escape(label_text)}\s*\]?\.?\s*", "", body))
        if not body:
            continue
        raw_label = label or str(index)
        raw = f"{raw_label}. {body}" if raw_label.isdigit() else f"[{raw_label}] {body}"
        references.append(
            {
                "raw": raw,
                "doi": _reference_doi(ref) or None,
                "title": _reference_title(ref) or None,
                "year": _reference_year(ref) or None,
            }
        )
    return references


def parse_jats_xml(
    xml_body: bytes,
    *,
    source_url: str,
    base_metadata: Mapping[str, Any] | None = None,
    xml_root: ET.Element | None = None,
) -> JatsExtraction | None:
    try:
        root = xml_root if xml_root is not None else ET.fromstring(xml_body)
    except ET.ParseError:
        return None
    if not isinstance(root.tag, str) or xml_local_name(root.tag) != "article":
        return None

    metadata = extract_jats_metadata(root, base_metadata=base_metadata, source_url=source_url)
    article_meta = _article_meta(root)
    abstract_node = first_child(article_meta, "abstract")
    abstract_text = normalize_text("\n\n".join(_render_paragraph_texts(abstract_node)))
    abstract_sections = (
        [{"heading": "Abstract", "text": abstract_text, "kind": "abstract", "order": 0}]
        if abstract_text
        else []
    )

    assets: list[dict[str, Any]] = []
    table_entries: list[dict[str, Any]] = []
    formula_renders: list[FormulaRenderResult] = []
    body_lines = _render_blocks(
        first_child(root, "body"),
        heading_level=2,
        source_url=source_url,
        assets=assets,
        table_entries=table_entries,
        formula_renders=formula_renders,
    )
    back_lines = _back_matter_lines(
        root,
        source_url=source_url,
        assets=assets,
        table_entries=table_entries,
        formula_renders=formula_renders,
    )
    supplement_entries = _supplementary_entries(root, source_url)
    for entry in supplement_entries:
        if not any(
            normalize_text(str(item.get("key") or "")) == normalize_text(str(entry.get("key") or ""))
            for item in assets
        ):
            assets.append(entry)
    markdown_text = normalize_lines([*body_lines, *back_lines])
    references = extract_jats_references(root)
    if references:
        metadata["references"] = references

    conversion_notes = collect_conversion_notes(
        table_entries=table_entries,
        formula_notes=[str(result.note) for result in formula_renders if normalize_text(str(result.note or ""))],
    )
    semantic_losses = SemanticLosses(
        table_fallback_count=sum(
            1 for entry in table_entries if normalize_text(str(entry.get("table_render_kind") or "")) == "fallback"
        ),
        table_layout_degraded_count=sum(
            1 for entry in table_entries if normalize_text(str(entry.get("lossy_message") or ""))
        ),
        formula_fallback_count=sum(
            1 for result in formula_renders if getattr(result, "fallback_kind", None) == "fallback"
        ),
        formula_missing_count=sum(
            1 for result in formula_renders if getattr(result, "fallback_kind", None) == "missing"
        ),
    )
    return JatsExtraction(
        metadata=metadata,
        abstract_sections=abstract_sections,
        markdown_text=markdown_text,
        assets=assets,
        references=references,
        semantic_losses=semantic_losses,
        conversion_notes=conversion_notes,
    )


def build_jats_markdown_document(
    extraction: JatsExtraction,
    *,
    xml_path: Path | None = None,
    provider_label: str = "jats",
) -> str:
    lines = [f"# {normalize_text(str(extraction.metadata.get('title') or 'Untitled Article'))}", ""]
    doi = normalize_text(str(extraction.metadata.get("doi") or ""))
    if doi:
        lines.append(f"- DOI: `{doi}`")
    lines.append(f"- Provider: `{provider_label}`")
    journal = normalize_text(str(extraction.metadata.get("journal_title") or extraction.metadata.get("journal") or ""))
    if journal:
        lines.append(f"- Journal: {journal}")
    published = normalize_text(str(extraction.metadata.get("published") or ""))
    if published:
        lines.append(f"- Published: {published}")
    if xml_path is not None:
        lines.append(f"- XML: {xml_path.name}")
    lines.append("")
    if extraction.abstract_sections:
        lines.extend(["## Abstract", "", str(extraction.abstract_sections[0]["text"]), ""])
    if extraction.markdown_text:
        lines.extend(extraction.markdown_text.splitlines())
        lines.append("")
    if extraction.conversion_notes:
        lines.extend(["## Conversion Notes", "", *extraction.conversion_notes, ""])
    return normalize_lines(lines)


__all__ = [
    "JatsExtraction",
    "build_jats_markdown_document",
    "extract_jats_authors",
    "extract_jats_metadata",
    "extract_jats_references",
    "parse_jats_xml",
]
