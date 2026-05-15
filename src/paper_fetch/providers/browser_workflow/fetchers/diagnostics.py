"""Failure diagnostics for browser workflow fetchers."""

from __future__ import annotations

from typing import Any, Mapping

from ....extraction.html.signals import (
    CLOUDFLARE_CHALLENGE_TITLE_TOKENS as _CLOUDFLARE_CHALLENGE_TITLE_TOKENS,
)
from ....quality.reason_codes import CLOUDFLARE_CHALLENGE
from ....utils import normalize_text
from ..._flaresolverr import FetchedPublisherHtml

BROWSER_CONTEXT_ERROR = "browser_context_error"
PLAYWRIGHT_CONTEXT_ERROR = "playwright_context_error"


def _looks_like_cloudflare_challenge_title(title: str | None) -> bool:
    normalized = normalize_text(title).lower()
    return bool(
        normalized
        and any(token in normalized for token in _CLOUDFLARE_CHALLENGE_TITLE_TOKENS)
    )


def _looks_like_cloudflare_challenge_failure(failure: Mapping[str, Any] | None) -> bool:
    if not isinstance(failure, Mapping):
        return False
    reason = normalize_text(str(failure.get("reason") or "")).lower()
    title = normalize_text(
        str(failure.get("title_snippet") or failure.get("title") or "")
    ).lower()
    body = normalize_text(str(failure.get("body_snippet") or "")).lower()
    return (
        reason in {CLOUDFLARE_CHALLENGE, "login_or_access_html"}
        or _looks_like_cloudflare_challenge_title(title)
        or any(token in body for token in _CLOUDFLARE_CHALLENGE_TITLE_TOKENS)
    )


def _compact_failure_diagnostic(values: Mapping[str, Any]) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized = normalize_text(value)
            if normalized:
                diagnostic[key] = normalized
            continue
        if isinstance(value, (bool, int, float)):
            diagnostic[key] = value
            continue
        if isinstance(value, list) and value:
            diagnostic[key] = value
            continue
        if isinstance(value, Mapping) and value:
            diagnostic[key] = dict(value)
    return diagnostic


def _copy_failure_diagnostic(values: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(values)
    recovery_attempts = values.get("recovery_attempts")
    if isinstance(recovery_attempts, list):
        copied["recovery_attempts"] = [
            dict(item) if isinstance(item, Mapping) else item
            for item in recovery_attempts
        ]
    return copied


def _diagnostic_with_reason_aliases(
    values: Mapping[str, Any], aliases: tuple[str, ...]
) -> dict[str, Any]:
    diagnostic = _compact_failure_diagnostic(values)
    for alias in aliases:
        normalized_alias = normalize_text(alias)
        if (
            normalized_alias
            and normalized_alias != normalize_text(str(diagnostic.get("reason") or ""))
        ):
            diagnostic.setdefault(normalized_alias, normalized_alias)
    return diagnostic


def _context_failure_diagnostic(exc: Exception) -> dict[str, Any]:
    message = normalize_text(str(exc))
    return _diagnostic_with_reason_aliases(
        {
            "reason": BROWSER_CONTEXT_ERROR,
            "error_type": exc.__class__.__name__,
            "error_message": message[:240] if message else exc.__class__.__name__,
        },
        (PLAYWRIGHT_CONTEXT_ERROR,),
    )


def _is_timeout_error(value: str | None) -> bool:
    normalized = normalize_text(value).lower()
    return bool(normalized and ("timeout" in normalized or "aborterror" in normalized))


def _image_fetch_failure_reason(
    *, error: str | None = None, timed_out: bool = False
) -> str:
    if timed_out or _is_timeout_error(error):
        return "image_fetch_timeout"
    return "image_fetch_error"


def _browser_image_payload_failure_reason(result: FetchedPublisherHtml) -> str:
    if not isinstance(result.image_payload, Mapping):
        return "browser_image_payload_missing"
    payload_reason = normalize_text(str(result.image_payload.get("reason") or ""))
    if payload_reason:
        return payload_reason
    return "browser_image_payload_invalid"


_flaresolverr_image_payload_failure_reason = _browser_image_payload_failure_reason
