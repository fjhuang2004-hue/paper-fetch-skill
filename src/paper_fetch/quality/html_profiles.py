"""Provider-neutral HTML availability profiles and access signals."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..extraction.html.provider_rules import (
    DEFAULT_SITE_RULE,
    PROVIDER_HTML_RULES,
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
    no_availability_overrides as no_availability_overrides,
    pnas_blocking_fallback_signals as pnas_blocking_fallback_signals,
    pnas_positive_signals as pnas_positive_signals,
    science_blocking_fallback_signals as science_blocking_fallback_signals,
    science_positive_signals as science_positive_signals,
    wiley_blocking_fallback_signals as wiley_blocking_fallback_signals,
    wiley_positive_signals as wiley_positive_signals,
)

SCIENCE_NOISE_PROFILE = provider_html_rules("science").noise_profile
PNAS_NOISE_PROFILE = provider_html_rules("pnas").noise_profile
WILEY_NOISE_PROFILE = provider_html_rules("wiley").noise_profile
IEEE_NOISE_PROFILE = provider_html_rules("ieee").noise_profile


@dataclass(frozen=True)
class HtmlAvailabilityProfile:
    noise_profile: str = "generic"
    site_rule_overrides: Mapping[str, Any] = field(default_factory=dict)
    positive_signals: Callable[[str], tuple[list[str], list[str], list[str]]] = default_positive_signals
    blocking_fallback_signals: Callable[[str], list[str]] = lambda _html_text: []
    availability_overrides: Callable[..., tuple[list[str], list[str], list[str]]] = no_availability_overrides


GENERIC_AVAILABILITY_PROFILE = HtmlAvailabilityProfile()


def _availability_profile_from_rules(provider_name: str) -> HtmlAvailabilityProfile:
    rules = provider_html_rules(provider_name)
    return HtmlAvailabilityProfile(
        noise_profile=rules.noise_profile,
        site_rule_overrides=copy.deepcopy(dict(rules.availability_site_rule_overrides)),
        positive_signals=rules.positive_signals,
        blocking_fallback_signals=rules.blocking_fallback_signals,
        availability_overrides=rules.availability_overrides,
    )


PUBLISHER_AVAILABILITY_PROFILES: dict[str, HtmlAvailabilityProfile] = {
    name: _availability_profile_from_rules(name)
    for name in PROVIDER_HTML_RULES
}


def availability_profile_for_publisher(publisher: str | None) -> HtmlAvailabilityProfile:
    rules = provider_html_rules(normalize_text(publisher or "").lower())
    if rules.name == "generic":
        return GENERIC_AVAILABILITY_PROFILE
    return PUBLISHER_AVAILABILITY_PROFILES.get(rules.name, GENERIC_AVAILABILITY_PROFILE)


def site_rule_for_publisher(publisher: str | None) -> dict[str, Any]:
    profile = availability_profile_for_publisher(publisher)
    merged = copy.deepcopy(DEFAULT_SITE_RULE)
    for key, value in profile.site_rule_overrides.items():
        default_value = merged.get(key)
        if isinstance(default_value, list):
            merged[key] = [*default_value, *[item for item in value if item not in default_value]]
            continue
        if isinstance(default_value, set):
            merged[key] = set(default_value) | set(value)
            continue
        merged[key] = copy.deepcopy(value)
    return merged


def noise_profile_for_publisher(publisher: str | None) -> str:
    return availability_profile_for_publisher(publisher).noise_profile


def provider_positive_signals(
    publisher: str | None,
    html_text: str,
) -> tuple[list[str], list[str], list[str]]:
    return availability_profile_for_publisher(publisher).positive_signals(html_text)


def provider_blocking_fallback_signals(
    publisher: str | None,
    html_text: str,
) -> list[str]:
    return list(availability_profile_for_publisher(publisher).blocking_fallback_signals(html_text))


def provider_availability_overrides(
    publisher: str | None,
    *args: Any,
    **kwargs: Any,
) -> tuple[list[str], list[str], list[str]]:
    return availability_profile_for_publisher(publisher).availability_overrides(*args, **kwargs)
