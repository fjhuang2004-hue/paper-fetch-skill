"""HTML availability signal helpers and provider-owned signal callbacks."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ..utils import normalize_text

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    BeautifulSoup = None

HTML_STRONG_FULLTEXT_MARKERS = (
    'property="articleBody"',
    "property='articleBody'",
    'itemprop="articleBody"',
    "itemprop='articleBody'",
)
HTML_STRUCTURE_MARKERS = (
    'data-article-access="full"',
    "data-article-access='full'",
    'data-article-access-type="full"',
    "data-article-access-type='full'",
    'id="bodymatter"',
    "id='bodymatter'",
)
AAAS_DATALAYER_PATTERN = re.compile(r"AAASdataLayer=(\{.*?\});(?:if\(|</script>)", flags=re.DOTALL)
PNAS_DATALAYER_PATTERN = re.compile(r"PNASdataLayer\s*=(\{.*?\});", flags=re.DOTALL)
WILEY_DATALAYER_PATTERN = re.compile(r"window\.adobeDataLayer\.push\((\{.*?\})\);", flags=re.DOTALL)


def dedupe_signals(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def default_positive_signals(html_text: str) -> tuple[list[str], list[str], list[str]]:
    strong: list[str] = []
    soft: list[str] = []
    lowered = html_text.lower()
    if any(marker in lowered for marker in HTML_STRONG_FULLTEXT_MARKERS):
        strong.append("article_body_marker")
    if any(marker in lowered for marker in HTML_STRUCTURE_MARKERS):
        soft.append("article_body_structure_marker")
    if "<article" in lowered:
        soft.append("article_tag_present")
    return dedupe_signals(strong), dedupe_signals(soft), []


def looks_like_abstract_redirect(requested_url: str | None, final_url: str | None) -> bool:
    if not requested_url or not final_url:
        return False
    requested = requested_url.lower()
    final = final_url.lower()
    return "/doi/full/" in requested and "/doi/abs/" in final and requested != final


def load_aaas_datalayer(html_text: str) -> Mapping[str, Any] | None:
    match = AAAS_DATALAYER_PATTERN.search(html_text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def load_pnas_datalayer(html_text: str) -> Mapping[str, Any] | None:
    match = PNAS_DATALAYER_PATTERN.search(html_text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def load_wiley_datalayer(html_text: str) -> Mapping[str, Any] | None:
    for match in WILEY_DATALAYER_PATTERN.finditer(html_text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        if isinstance(payload.get("content"), Mapping) or isinstance(payload.get("page"), Mapping):
            return payload
    return None


def science_blocking_fallback_signals(html_text: str) -> list[str]:
    payload = load_aaas_datalayer(html_text)
    if payload is None:
        return []
    page = payload.get("page")
    page_info = page.get("pageInfo", {}) if isinstance(page, Mapping) else {}
    user = payload.get("user", {}) if isinstance(payload.get("user"), Mapping) else {}
    signals: list[str] = []

    page_type = normalize_text(page_info.get("pageType")).lower()
    if page_type == "journal-article-denial":
        signals.append("aaas_page_type_denial")
    if page_type == "journal-article-abstract":
        signals.append("aaas_page_type_abstract")

    view_type = normalize_text(page_info.get("viewType")).lower()
    if view_type == "abs":
        signals.append("aaas_view_abs")

    user_entitled = normalize_text(user.get("entitled")).lower()
    user_access = normalize_text(user.get("access")).lower()
    if user_entitled == "false" and user_access != "yes":
        signals.append("aaas_entitlement_denied")

    return dedupe_signals(signals)


def science_positive_signals(html_text: str) -> tuple[list[str], list[str], list[str]]:
    strong, soft, abstract_only = default_positive_signals(html_text)
    payload = load_aaas_datalayer(html_text)
    if payload is None:
        return strong, soft, abstract_only
    page_info = payload.get("page", {}).get("pageInfo", {}) if isinstance(payload.get("page"), Mapping) else {}
    user = payload.get("user", {}) if isinstance(payload.get("user"), Mapping) else {}
    if str(page_info.get("pageType") or "").strip().lower() == "journal-article-full-text":
        soft.append("aaas_page_type_full_text")
    if "abstract" in str(page_info.get("pageType") or "").strip().lower():
        abstract_only.append("aaas_page_type_abstract")
    if str(page_info.get("viewType") or "").strip().lower() == "full":
        soft.append("aaas_view_full")
    if "abstract" in str(page_info.get("viewType") or "").strip().lower():
        abstract_only.append("aaas_view_abstract")
    if str(user.get("entitled") or "").strip().lower() == "true":
        strong.append("aaas_user_entitled")
    if str(user.get("access") or "").strip().lower() == "yes":
        strong.append("aaas_user_access_yes")
    if str(page_info.get("articleType") or "").strip():
        soft.append("aaas_article_type_present")
    return dedupe_signals(strong), dedupe_signals(soft), dedupe_signals(abstract_only)


def pnas_blocking_fallback_signals(html_text: str) -> list[str]:
    payload = load_pnas_datalayer(html_text)
    if payload is None:
        return []
    page = payload.get("page", {}) if isinstance(payload.get("page"), Mapping) else {}
    attributes = page.get("attributes", {}) if isinstance(page.get("attributes"), Mapping) else {}
    user = payload.get("user", {}) if isinstance(payload.get("user"), Mapping) else {}
    access_type = normalize_text(attributes.get("accessType")).lower()
    free_access = normalize_text(attributes.get("freeAccess")).lower()
    user_access = normalize_text(user.get("access")).lower()
    if access_type == "paywall" and free_access == "no" and user_access == "no":
        return ["pnas_paywall_no_access"]
    return []


def pnas_positive_signals(html_text: str) -> tuple[list[str], list[str], list[str]]:
    return default_positive_signals(html_text)


def wiley_blocking_fallback_signals(html_text: str) -> list[str]:
    payload = load_wiley_datalayer(html_text)
    if payload is None:
        return []
    content = payload.get("content", {}) if isinstance(payload.get("content"), Mapping) else {}
    item = content.get("item", {}) if isinstance(content.get("item"), Mapping) else {}
    page = payload.get("page", {}) if isinstance(payload.get("page"), Mapping) else {}
    signals: list[str] = []

    if normalize_text(item.get("access")).lower() == "no":
        signals.append("wiley_access_no")
    if normalize_text(item.get("format-viewed") or item.get("format_viewed")).lower() == "abstract":
        signals.append("wiley_format_viewed_abstract")
    if normalize_text(page.get("tertiary-section") or page.get("tertiary_section")).lower() == "abs":
        signals.append("wiley_page_tertiary_abs")

    return dedupe_signals(signals)


def wiley_positive_signals(html_text: str) -> tuple[list[str], list[str], list[str]]:
    return default_positive_signals(html_text)


def ieee_blocking_fallback_signals(html_text: str) -> list[str]:
    from ..extraction.html.provider_rules import provider_html_rules

    lowered = normalize_text(html_text).lower()
    signals: list[str] = []
    if any(token in lowered for token in provider_html_rules("ieee").access_block_text_tokens):
        signals.append("ieee_access_or_challenge_page")
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html_text, "html.parser")
        article = soup.select_one("#article")
        if article is not None:
            text = normalize_text(article.get_text(" ", strip=True))
            has_body_nodes = bool(article.select("p, h2, h3, div.section, div.section_2, figure, table, tex-math"))
            if not text and not has_body_nodes:
                signals.append("ieee_empty_article_shell")
    return dedupe_signals(signals)


def ieee_positive_signals(html_text: str) -> tuple[list[str], list[str], list[str]]:
    strong, soft, abstract_only = default_positive_signals(html_text)
    lowered = html_text.lower()
    if 'id="article"' in lowered or "id='article'" in lowered:
        soft.append("ieee_article_container")
    if "div class=\"section" in lowered or "div class='section" in lowered:
        strong.append("ieee_section_nodes")
    if "<tex-math" in lowered or "tex-math" in lowered:
        soft.append("ieee_formula_marker")
    if "<figure" in lowered or "class=\"figure" in lowered or "class='figure" in lowered:
        soft.append("ieee_figure_marker")
    if "<table" in lowered:
        soft.append("ieee_table_marker")
    return dedupe_signals(strong), dedupe_signals(soft), dedupe_signals(abstract_only)

