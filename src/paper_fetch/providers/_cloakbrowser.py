"""CloakBrowser helpers for browser-workflow provider access."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from importlib import util as importlib_util
import logging
from pathlib import Path
from typing import Any, Mapping

from bs4 import BeautifulSoup

from ..config import (
    CLOAKBROWSER_HEADLESS_ENV_VAR,
    CLOAKBROWSER_TIMEOUT_MS_ENV_VAR,
    build_user_agent,
    parse_positive_int_env,
    resolve_user_data_dir,
)
from ..extraction.html.signals import detect_html_block, summarize_html
from ..quality.html_availability import choose_parser, extract_page_title
from ..quality.html_signals import looks_like_abstract_redirect
from ..quality.reason_codes import REDIRECTED_TO_ABSTRACT
from ..reason_codes import ERROR, NOT_CONFIGURED, OK, READY
from ..utils import normalize_text, provider_display_name, sanitize_filename
from ._flaresolverr import (
    FetchedPublisherHtml,
    FlareSolverrFailure,
    merge_browser_context_seeds,
    normalize_browser_cookies_for_playwright,
    parse_optional_int,
)
from .base import (
    ProviderFailure,
    ProviderStatusResult,
    build_provider_status_check,
    provider_status_check_from_failure,
)
from .browser_workflow.fetchers import _normalized_response_headers
from .browser_workflow.shared import BROWSER_HTML_BLOCKED_RESOURCE_TYPES

logger = logging.getLogger("paper_fetch.providers.cloakbrowser")

DEFAULT_BROWSER_RUNTIME_MAX_TIMEOUT_MS = 120000
DEFAULT_BROWSER_RUNTIME_WAIT_SECONDS = 8
DEFAULT_BROWSER_RUNTIME_WARM_WAIT_SECONDS = 1
DEFAULT_CLOAKBROWSER_TIMEOUT_MS = DEFAULT_BROWSER_RUNTIME_MAX_TIMEOUT_MS
CLOAKBROWSER_STATUS_PROBE_ID = "probe://cloakbrowser/status"
_BROWSER_WORKFLOW_PROVIDERS = ("wiley", "science", "pnas", "ams")


@dataclass(frozen=True)
class CloakBrowserRuntimeConfig:
    provider: str
    doi: str
    artifact_dir: Path
    headless: bool
    user_agent: str
    timeout_ms: int = DEFAULT_CLOAKBROWSER_TIMEOUT_MS


class CloakBrowserFailure(FlareSolverrFailure):
    """Browser workflow failure raised by the CloakBrowser backend."""


def _browser_workflow_label(provider: str) -> str:
    normalized = normalize_text(provider).lower()
    if normalized in _BROWSER_WORKFLOW_PROVIDERS:
        return f"{provider_display_name(normalized)} browser workflow"
    return f"{normalized or provider} browser workflow"


def _dependency_available() -> bool:
    try:
        return importlib_util.find_spec("cloakbrowser") is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _dependency_details() -> dict[str, Any]:
    details: dict[str, Any] = {"probe": "importlib.find_spec"}
    if _dependency_available():
        try:
            details["version"] = importlib_metadata.version("cloakbrowser")
        except importlib_metadata.PackageNotFoundError:
            details["version"] = None
    return details


def _import_cloakbrowser() -> Any:
    try:
        import cloakbrowser
    except Exception as exc:
        raise ProviderFailure(
            NOT_CONFIGURED,
            f"CloakBrowser Python package is not importable: {exc}",
        ) from exc
    return cloakbrowser


def _env_flag_false(value: str | None) -> bool:
    return normalize_text(value).lower() in {"0", "false", "no", "off"}


def load_runtime_config(env: Mapping[str, str], *, provider: str, doi: str) -> CloakBrowserRuntimeConfig:
    headless = not _env_flag_false(env.get(CLOAKBROWSER_HEADLESS_ENV_VAR))
    artifact_dir = resolve_user_data_dir(env) / "publisher-browser-artifacts" / provider / sanitize_filename(doi)
    return CloakBrowserRuntimeConfig(
        provider=provider,
        doi=doi,
        artifact_dir=artifact_dir,
        headless=headless,
        user_agent=build_user_agent(env),
        timeout_ms=parse_positive_int_env(
            env,
            CLOAKBROWSER_TIMEOUT_MS_ENV_VAR,
            default=DEFAULT_CLOAKBROWSER_TIMEOUT_MS,
        ),
    )


def ensure_runtime_ready(config: CloakBrowserRuntimeConfig) -> None:
    try:
        _import_cloakbrowser()
    except ProviderFailure as exc:
        workflow_label = _browser_workflow_label(config.provider)
        raise ProviderFailure(
            NOT_CONFIGURED,
            f"{workflow_label} requires the cloakbrowser Python package. {exc.message}",
        ) from exc


def _runtime_probe_details(env: Mapping[str, str], config: CloakBrowserRuntimeConfig | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {
        "headless": (
            config.headless
            if config is not None
            else not _env_flag_false(env.get(CLOAKBROWSER_HEADLESS_ENV_VAR))
        ),
        "timeout_ms": config.timeout_ms if config is not None else parse_positive_int_env(
            env,
            CLOAKBROWSER_TIMEOUT_MS_ENV_VAR,
            default=DEFAULT_CLOAKBROWSER_TIMEOUT_MS,
        ),
    }
    return details


def probe_runtime_status(
    env: Mapping[str, str],
    *,
    provider: str,
    doi: str = CLOAKBROWSER_STATUS_PROBE_ID,
) -> ProviderStatusResult:
    checks = []
    config: CloakBrowserRuntimeConfig | None = None
    runtime_details = _runtime_probe_details(env)
    dependency_available = _dependency_available()
    try:
        config = load_runtime_config(env, provider=provider, doi=doi)
        runtime_details = _runtime_probe_details(env, config)
        checks.append(
            build_provider_status_check(
                "runtime_env",
                OK if dependency_available else NOT_CONFIGURED,
                (
                    f"{provider} CloakBrowser runtime environment is configured."
                    if dependency_available
                    else f"{provider} CloakBrowser runtime requires the cloakbrowser Python package."
                ),
                details=runtime_details,
            )
        )
    except ProviderFailure as exc:
        checks.append(provider_status_check_from_failure("runtime_env", exc, details=runtime_details))
    except Exception as exc:
        checks.append(build_provider_status_check("runtime_env", ERROR, str(exc), details=runtime_details))

    dependency_details = _dependency_details()
    if dependency_available:
        checks.append(
            build_provider_status_check(
                "cloakbrowser_dependency",
                OK,
                "CloakBrowser Python package is importable; browser launch is not probed.",
                details=dependency_details,
            )
        )
    else:
        checks.append(
            build_provider_status_check(
                "cloakbrowser_dependency",
                NOT_CONFIGURED,
                "CloakBrowser Python package is not installed.",
                details=dependency_details,
            )
        )

    missing_env: list[str] = []
    for check in checks:
        for name in check.missing_env:
            if name not in missing_env:
                missing_env.append(name)

    if any(check.status == ERROR for check in checks):
        status = ERROR
    elif all(check.status == OK for check in checks):
        status = READY
    else:
        status = NOT_CONFIGURED

    return ProviderStatusResult(
        provider=provider,
        status=status,
        available=status == READY,
        official_provider=True,
        missing_env=missing_env,
        notes=[],
        checks=list(checks),
    )


def _response_headers(response: Any) -> dict[str, str]:
    if response is None:
        return {}
    try:
        return _normalized_response_headers(response.all_headers())
    except Exception:
        return _normalized_response_headers(getattr(response, "headers", {}) or {})


def _response_status(response: Any) -> int | None:
    if response is None:
        return None
    try:
        return parse_optional_int(getattr(response, "status", None))
    except Exception:
        return None


def _context_seed(context: Any, *, final_url: str, user_agent: str) -> dict[str, Any]:
    try:
        cookies = context.cookies()
    except Exception:
        cookies = []
    return {
        "browser_cookies": normalize_browser_cookies_for_playwright(
            list(cookies or []),
            fallback_url=final_url,
        ),
        "browser_user_agent": normalize_text(user_agent) or None,
        "browser_final_url": final_url,
    }


def _safe_close(value: Any) -> None:
    if value is None:
        return
    try:
        value.close()
    except Exception:
        pass


def fetch_html_with_cloakbrowser(
    candidate_urls: list[str],
    *,
    publisher: str,
    config: CloakBrowserRuntimeConfig,
    wait_seconds: int = DEFAULT_BROWSER_RUNTIME_WAIT_SECONDS,
    warm_wait_seconds: int = DEFAULT_BROWSER_RUNTIME_WARM_WAIT_SECONDS,
    max_timeout_ms: int | None = None,
    return_image_payload: bool = False,
    return_screenshot: bool = False,
    disable_media: bool = False,
) -> FetchedPublisherHtml:
    del warm_wait_seconds
    if not candidate_urls:
        raise CloakBrowserFailure("empty_html_attempts", "No publisher HTML candidates were attempted.")
    if return_image_payload:
        raise CloakBrowserFailure(
            "cloakbrowser_image_payload_unsupported",
            "CloakBrowser imagePayload recovery is not implemented in the minimal browser workflow backend.",
        )

    try:
        cloakbrowser = _import_cloakbrowser()
    except ProviderFailure as exc:
        raise CloakBrowserFailure(NOT_CONFIGURED, exc.message) from exc

    last_failure: CloakBrowserFailure | None = None
    latest_browser_context_seed: Mapping[str, Any] | None = None
    timeout_ms = max_timeout_ms or config.timeout_ms
    artifact_dir = config.artifact_dir / "cloakbrowser"
    user_agent = normalize_text(config.user_agent)

    browser = None
    browser_context = None
    page = None
    try:
        try:
            browser = cloakbrowser.launch(headless=config.headless, locale="en-US")
            browser_context = browser.new_context(
                user_agent=user_agent,
                locale="en-US",
                viewport={"width": 1440, "height": 1600},
            )
            page = browser_context.new_page()
        except Exception as exc:
            raise CloakBrowserFailure(
                "cloakbrowser_launch_failed",
                normalize_text(str(exc)) or "CloakBrowser failed to launch.",
            ) from exc

        def route_handler(route: Any) -> None:
            try:
                resource_type = normalize_text(str(route.request.resource_type or "")).lower()
                if disable_media and resource_type in BROWSER_HTML_BLOCKED_RESOURCE_TYPES:
                    route.abort()
                    return
                route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        if disable_media:
            try:
                page.route("**/*", route_handler)
            except Exception:
                pass

        for url in candidate_urls:
            normalized_url = normalize_text(url)
            if not normalized_url:
                continue
            try:
                logger.debug(
                    "cloakbrowser_request provider=%s action=request wait_seconds=%s url=%s",
                    publisher,
                    wait_seconds,
                    normalized_url,
                )
                response = page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_seconds > 0:
                    page.wait_for_timeout(max(0, int(wait_seconds)) * 1000)
                final_url = normalize_text(str(getattr(page, "url", "") or "")) or normalized_url
                html = str(page.content() or "")
                title = normalize_text(str(page.title() or "")) or extract_page_title(
                    BeautifulSoup(html, choose_parser())
                )
                status = _response_status(response)
                headers = _response_headers(response)
                summary = summarize_html(html)
                browser_context_seed = _context_seed(browser_context, final_url=final_url, user_agent=user_agent)
                if browser_context_seed.get("browser_cookies") or browser_context_seed.get("browser_user_agent"):
                    latest_browser_context_seed = browser_context_seed
            except Exception as exc:
                if isinstance(exc, CloakBrowserFailure):
                    last_failure = exc
                else:
                    last_failure = CloakBrowserFailure(
                        "cloakbrowser_request_failed",
                        normalize_text(str(exc)) or "CloakBrowser page request failed.",
                    )
                continue

            if looks_like_abstract_redirect(normalized_url, final_url):
                last_failure = CloakBrowserFailure(
                    REDIRECTED_TO_ABSTRACT,
                    "Publisher redirected the full-text URL to an abstract page.",
                    browser_context_seed=browser_context_seed,
                )
                continue

            detected = detect_html_block(title or "", summary, status)
            if detected is not None:
                last_failure = CloakBrowserFailure(
                    detected.reason,
                    detected.message,
                    browser_context_seed=browser_context_seed,
                )
                continue
            if not normalize_text(html):
                last_failure = CloakBrowserFailure(
                    "empty_html_response",
                    "CloakBrowser returned empty publisher HTML.",
                    browser_context_seed=browser_context_seed,
                )
                continue

            screenshot_b64 = None
            if return_screenshot:
                try:
                    screenshot_payload = page.screenshot(type="png", timeout=timeout_ms)
                    if isinstance(screenshot_payload, bytes):
                        screenshot_b64 = base64.b64encode(screenshot_payload).decode("ascii")
                    elif isinstance(screenshot_payload, str):
                        screenshot_b64 = screenshot_payload
                except Exception:
                    screenshot_b64 = None
            return FetchedPublisherHtml(
                source_url=normalized_url,
                final_url=final_url,
                html=html,
                response_status=status,
                response_headers=headers,
                title=title,
                summary=summary,
                browser_context_seed=browser_context_seed,
                screenshot_b64=screenshot_b64,
            )
    finally:
        _safe_close(page)
        _safe_close(browser_context)
        _safe_close(browser)

    if last_failure is None and latest_browser_context_seed is not None:
        last_failure = CloakBrowserFailure(
            "empty_html_attempts",
            "No publisher HTML candidates were attempted.",
            browser_context_seed=latest_browser_context_seed,
        )
    if last_failure is None:
        last_failure = CloakBrowserFailure("empty_html_attempts", "No publisher HTML candidates were attempted.")
    if artifact_dir:
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    raise last_failure


fetch_html_with_cloakbrowser.paper_fetch_html_fetcher_name = "cloakbrowser"  # type: ignore[attr-defined]


def fetch_html_with_cloakbrowser_fast(*args: Any, **kwargs: Any) -> FetchedPublisherHtml:
    return fetch_html_with_cloakbrowser(*args, **kwargs)


fetch_html_with_cloakbrowser_fast.paper_fetch_html_fetcher_name = "cloakbrowser_fast"  # type: ignore[attr-defined]


def warm_browser_context_with_cloakbrowser(
    candidate_urls: list[str],
    *,
    publisher: str,
    config: CloakBrowserRuntimeConfig,
    browser_context_seed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged_seed = merge_browser_context_seeds(browser_context_seed)
    if not candidate_urls:
        return merged_seed

    try:
        result = fetch_html_with_cloakbrowser(candidate_urls, publisher=publisher, config=config)
    except CloakBrowserFailure as exc:
        return merge_browser_context_seeds(merged_seed, exc.browser_context_seed)
    return merge_browser_context_seeds(merged_seed, result.browser_context_seed)
