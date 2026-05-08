"""Provider-owned HTML extraction, cleanup, and availability rule registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ...quality.html_signals import (
    default_positive_signals,
    ieee_blocking_fallback_signals,
    ieee_positive_signals,
    pnas_blocking_fallback_signals,
    pnas_positive_signals,
    science_blocking_fallback_signals,
    science_positive_signals,
    wiley_blocking_fallback_signals,
    wiley_positive_signals,
)


DEFAULT_NOISE_PROFILE = "generic"

DEFAULT_SITE_RULE: dict[str, Any] = {
    "candidate_selectors": [
        "article",
        "main article",
        "[role='main'] article",
        "[itemprop='articleBody']",
        "[property='articleBody']",
        "[itemprop='mainEntity']",
        ".article",
        ".article__body",
        ".article__content",
        ".article-body",
        ".main-content",
        "#main-content",
        "main",
        "[role='main']",
        "body",
    ],
    "remove_selectors": [
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        ".social-share",
        ".article-tools",
        ".article-metrics",
        ".metrics-widget",
        ".recommended-articles",
        ".related-content",
        ".breadcrumbs",
        ".toc",
        ".tab__nav",
        ".accessDenialWidget",
        ".cookie-banner",
        ".cookie-consent",
    ],
    "drop_keywords": {
        "metrics",
        "metric",
        "share",
        "social",
        "recommend",
        "related",
        "toolbar",
        "breadcrumb",
        "download",
        "cookie",
        "promo",
        "banner",
        "citation-tool",
        "nav",
        "access-widget",
        "rightslink",
    },
    "drop_text": {
        "Check for updates",
        "View Metrics",
        "Share",
        "Cite",
    },
}

SCIENCE_SITE_RULE_OVERRIDES: dict[str, Any] = {
    "candidate_selectors": [
        ".article__fulltext",
        ".article-view",
    ],
    "remove_selectors": [
        "header .social-share",
        ".jump-to-nav",
        ".article-access-info",
        ".references-tab",
        ".permissions",
        ".issue-item__citation",
        ".article-header__access",
        "#article_collateral_menu",
        "#core-collateral-fulltext-options",
        "#core-collateral-metrics",
        "#core-collateral-share",
        "#core-collateral-media",
        "#core-collateral-figures",
        "#core-collateral-tables",
    ],
    "drop_keywords": {"advert", "tab-nav", "jump-to"},
    "drop_text": {"Permissions"},
}

PNAS_MARKDOWN_PROMO_TOKENS = (
    "sign up for pnas alerts",
    "get alerts for new articles, or get an alert when an article is cited",
)
PNAS_SITE_RULE_OVERRIDES: dict[str, Any] = {
    "candidate_selectors": [
        ".article__fulltext",
        ".core-container",
        ".article-content",
    ],
    "remove_selectors": [
        ".article__access",
        ".article__footer",
        ".article__reference-links",
        ".core-collateral",
        ".card",
        ".signup-alert-ad",
    ],
    "drop_keywords": {"tab-nav"},
}

SPRINGER_NATURE_MARKDOWN_PROMO_TOKENS = (
    "sign up for alerts",
    "download citation",
    "reprints and permissions",
    "similar content being viewed by others",
)

WILEY_SITE_RULE_OVERRIDES: dict[str, Any] = {
    "candidate_selectors": [
        ".article-section__content",
        ".issue-item__body",
        ".epub-section",
        ".doi-access",
    ],
    "remove_selectors": [
        ".citation-tools",
        ".epub-reference",
        ".article-section__tableofcontents",
        ".publicationHistory",
    ],
    "drop_text": {"Recommended articles"},
}

IEEE_ACCESS_BLOCK_TEXT_TOKENS = (
    "unable to complete your request",
    "your request has been blocked",
    "verify you are human",
    "captcha",
    "access denied",
    "institutional sign in",
    "purchase access",
)
IEEE_EXTRACTION_CLEANUP_SELECTORS = (
    "accessType",
    "accesstype",
    "script",
    "style",
    "noscript",
    "iframe",
    "button",
    "input",
    "select",
    "textarea",
    ".zoom-container",
    ".document-actions",
    ".article-toolbar",
    ".stats-document-abstract-view",
    "button[data-docId]",
    "a[data-docId][href^='javascript:']",
    "[href^='javascript:']",
)
IEEE_AVAILABILITY_DROP_KEYWORDS = (
    "access-type",
    "article-toolbar",
    "document-actions",
    "download",
    "metrics",
    "recommend",
    "references-modal",
    "rightslink",
    "show-all",
    "zoom",
)
IEEE_AVAILABILITY_DROP_TEXT = (
    "Show All",
    "View References",
    "Download PDF",
)
IEEE_MARKDOWN_PROMO_TOKENS = (
    "download pdf",
    "export citation",
    "show all",
    "view references",
    "view all authors",
)
IEEE_SITE_RULE_OVERRIDES: dict[str, Any] = {
    "candidate_selectors": [
        "#article",
        "#BodyWrapper",
        ".ArticlePage",
    ],
    "remove_selectors": list(IEEE_EXTRACTION_CLEANUP_SELECTORS),
    "drop_keywords": set(IEEE_AVAILABILITY_DROP_KEYWORDS),
    "drop_text": set(IEEE_AVAILABILITY_DROP_TEXT),
}


@dataclass(frozen=True)
class ProviderHtmlRules:
    name: str
    aliases: tuple[str, ...] = ()
    noise_profile: str = DEFAULT_NOISE_PROFILE
    markdown_promo_tokens: tuple[str, ...] = ()
    extraction_cleanup_selectors: tuple[str, ...] = ()
    extraction_drop_keywords: tuple[str, ...] = ()
    availability_site_rule_overrides: Mapping[str, Any] = field(default_factory=dict)
    access_block_text_tokens: tuple[str, ...] = ()
    positive_signals: Callable[[str], tuple[list[str], list[str], list[str]]] = default_positive_signals
    blocking_fallback_signals: Callable[[str], list[str]] = lambda _html: []


GENERIC_HTML_RULES = ProviderHtmlRules(name=DEFAULT_NOISE_PROFILE)

PROVIDER_HTML_RULES: Mapping[str, ProviderHtmlRules] = MappingProxyType(
    {
        "science": ProviderHtmlRules(
            name="science",
            aliases=("aaas",),
            availability_site_rule_overrides=SCIENCE_SITE_RULE_OVERRIDES,
            positive_signals=science_positive_signals,
            blocking_fallback_signals=science_blocking_fallback_signals,
        ),
        "pnas": ProviderHtmlRules(
            name="pnas",
            noise_profile="pnas",
            markdown_promo_tokens=PNAS_MARKDOWN_PROMO_TOKENS,
            extraction_drop_keywords=("signup-alert-ad", "tab-nav"),
            availability_site_rule_overrides=PNAS_SITE_RULE_OVERRIDES,
            positive_signals=pnas_positive_signals,
            blocking_fallback_signals=pnas_blocking_fallback_signals,
        ),
        "springer_nature": ProviderHtmlRules(
            name="springer_nature",
            aliases=("springer", "nature"),
            noise_profile="springer_nature",
            markdown_promo_tokens=SPRINGER_NATURE_MARKDOWN_PROMO_TOKENS,
        ),
        "wiley": ProviderHtmlRules(
            name="wiley",
            extraction_drop_keywords=("citation-tools", "publicationhistory"),
            availability_site_rule_overrides=WILEY_SITE_RULE_OVERRIDES,
            positive_signals=wiley_positive_signals,
            blocking_fallback_signals=wiley_blocking_fallback_signals,
        ),
        "ieee": ProviderHtmlRules(
            name="ieee",
            noise_profile="ieee",
            markdown_promo_tokens=IEEE_MARKDOWN_PROMO_TOKENS,
            extraction_cleanup_selectors=IEEE_EXTRACTION_CLEANUP_SELECTORS,
            extraction_drop_keywords=IEEE_AVAILABILITY_DROP_KEYWORDS,
            availability_site_rule_overrides=IEEE_SITE_RULE_OVERRIDES,
            access_block_text_tokens=IEEE_ACCESS_BLOCK_TEXT_TOKENS,
            positive_signals=ieee_positive_signals,
            blocking_fallback_signals=ieee_blocking_fallback_signals,
        ),
    }
)


def _normalize_rule_key(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", "_").split())


def _build_rule_lookup() -> dict[str, ProviderHtmlRules]:
    lookup: dict[str, ProviderHtmlRules] = {DEFAULT_NOISE_PROFILE: GENERIC_HTML_RULES}
    for rules in PROVIDER_HTML_RULES.values():
        for key in (rules.name, rules.noise_profile, *rules.aliases):
            normalized = _normalize_rule_key(key)
            if normalized:
                lookup[normalized] = rules
    return lookup


_RULE_LOOKUP = MappingProxyType(_build_rule_lookup())
REGISTERED_NOISE_PROFILES = frozenset(
    {DEFAULT_NOISE_PROFILE, *(rules.noise_profile for rules in PROVIDER_HTML_RULES.values())}
)


def provider_html_rules(name: str | None) -> ProviderHtmlRules:
    return _RULE_LOOKUP.get(_normalize_rule_key(name), GENERIC_HTML_RULES)


def normalize_noise_profile(noise_profile: str | None) -> str:
    return provider_html_rules(noise_profile).noise_profile


def markdown_promo_tokens_for_profile(noise_profile: str | None) -> tuple[str, ...]:
    return provider_html_rules(noise_profile).markdown_promo_tokens


def extraction_cleanup_selectors_for_profile(noise_profile: str | None) -> tuple[str, ...]:
    return provider_html_rules(noise_profile).extraction_cleanup_selectors


def extraction_drop_keywords_for_profile(noise_profile: str | None) -> tuple[str, ...]:
    return provider_html_rules(noise_profile).extraction_drop_keywords


__all__ = [
    "DEFAULT_NOISE_PROFILE",
    "DEFAULT_SITE_RULE",
    "GENERIC_HTML_RULES",
    "IEEE_ACCESS_BLOCK_TEXT_TOKENS",
    "IEEE_AVAILABILITY_DROP_KEYWORDS",
    "IEEE_AVAILABILITY_DROP_TEXT",
    "IEEE_EXTRACTION_CLEANUP_SELECTORS",
    "IEEE_MARKDOWN_PROMO_TOKENS",
    "IEEE_SITE_RULE_OVERRIDES",
    "PNAS_MARKDOWN_PROMO_TOKENS",
    "PNAS_SITE_RULE_OVERRIDES",
    "PROVIDER_HTML_RULES",
    "ProviderHtmlRules",
    "REGISTERED_NOISE_PROFILES",
    "SCIENCE_SITE_RULE_OVERRIDES",
    "SPRINGER_NATURE_MARKDOWN_PROMO_TOKENS",
    "WILEY_SITE_RULE_OVERRIDES",
    "extraction_cleanup_selectors_for_profile",
    "extraction_drop_keywords_for_profile",
    "markdown_promo_tokens_for_profile",
    "normalize_noise_profile",
    "provider_html_rules",
]
