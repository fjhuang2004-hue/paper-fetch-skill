"""Provider-neutral HTML availability profiles and access signals."""

from __future__ import annotations

from typing import Any

from ..extraction.html.provider_rules import (
    DEFAULT_SITE_RULE,
    availability_rules_for_provider,
    merged_site_rule,
    provider_html_rules,
)
from ..utils import normalize_text
from .html_signals import (
    AAAS_DATALAYER_PATTERN as AAAS_DATALAYER_PATTERN,
    HTML_STRONG_FULLTEXT_MARKERS as HTML_STRONG_FULLTEXT_MARKERS,
    HTML_STRUCTURE_MARKERS as HTML_STRUCTURE_MARKERS,
    PNAS_DATALAYER_PATTERN as PNAS_DATALAYER_PATTERN,
    WILEY_DATALAYER_PATTERN as WILEY_DATALAYER_PATTERN,
    dedupe_signals as dedupe_signals,
    default_positive_signals as default_positive_signals,
    ieee_blocking_fallback_signals as ieee_blocking_fallback_signals,
    ieee_positive_signals as ieee_positive_signals,
    load_aaas_datalayer as load_aaas_datalayer,
    load_pnas_datalayer as load_pnas_datalayer,
    load_wiley_datalayer as load_wiley_datalayer,
    looks_like_abstract_redirect as looks_like_abstract_redirect,
    pnas_blocking_fallback_signals as pnas_blocking_fallback_signals,
    science_blocking_fallback_signals as science_blocking_fallback_signals,
    science_positive_signals as science_positive_signals,
    wiley_blocking_fallback_signals as wiley_blocking_fallback_signals,
)


def _rules_for_publisher(publisher: str | None):
    return provider_html_rules(normalize_text(publisher or "").lower())


def site_rule_for_publisher(publisher: str | None) -> dict[str, Any]:
    return merged_site_rule(_rules_for_publisher(publisher))


def noise_profile_for_publisher(publisher: str | None) -> str:
    return _rules_for_publisher(publisher).noise_profile


def provider_positive_signals(
    publisher: str | None,
    html_text: str,
) -> tuple[list[str], list[str], list[str]]:
    policy = availability_rules_for_provider(normalize_text(publisher or "").lower())
    return (
        policy.positive_signals(html_text)
        if policy.positive_signals
        else ([], [], [])
    )


def provider_blocking_fallback_signals(
    publisher: str | None,
    html_text: str,
) -> list[str]:
    policy = availability_rules_for_provider(normalize_text(publisher or "").lower())
    return (
        list(policy.blocking_fallback_signals(html_text))
        if policy.blocking_fallback_signals
        else []
    )


def provider_availability_overrides(
    publisher: str | None,
    *args: Any,
    **kwargs: Any,
) -> tuple[list[str], list[str], list[str]]:
    policy = availability_rules_for_provider(normalize_text(publisher or "").lower())
    return (
        policy.availability_overrides(*args, **kwargs)
        if policy.availability_overrides
        else ([], [], [])
    )
