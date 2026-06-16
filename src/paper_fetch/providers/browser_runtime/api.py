"""Browser-neutral runtime API backed by nodriver (CDP-based Chrome)."""

from __future__ import annotations

from typing import Any, Mapping

from .. import _nodriver_fetch
from ..base import ProviderStatusResult
from .types import BrowserFetchedHtml, BrowserRuntimeConfig

DEFAULT_BROWSER_RUNTIME_MAX_TIMEOUT_MS = _nodriver_fetch.DEFAULT_BROWSER_RUNTIME_MAX_TIMEOUT_MS
DEFAULT_BROWSER_RUNTIME_WAIT_SECONDS = _nodriver_fetch.DEFAULT_BROWSER_RUNTIME_WAIT_SECONDS
DEFAULT_BROWSER_RUNTIME_WARM_WAIT_SECONDS = _nodriver_fetch.DEFAULT_BROWSER_RUNTIME_WARM_WAIT_SECONDS


def load_runtime_config(env: Mapping[str, str], *, provider: str, doi: str) -> BrowserRuntimeConfig:
    return _nodriver_fetch.load_runtime_config(env, provider=provider, doi=doi)


def ensure_runtime_ready(config: BrowserRuntimeConfig) -> None:
    _nodriver_fetch.ensure_runtime_ready(config)


def probe_runtime_status(
    env: Mapping[str, str],
    *,
    provider: str,
    doi: str = "probe://browser/status",
) -> ProviderStatusResult:
    return _nodriver_fetch.probe_runtime_status(env, provider=provider, doi=doi)


def fetch_html_with_browser(
    candidate_urls: list[str],
    *,
    publisher: str,
    config: BrowserRuntimeConfig,
    **kwargs: Any,
) -> BrowserFetchedHtml:
    return _nodriver_fetch.fetch_html_with_nodriver(
        candidate_urls,
        publisher=publisher,
        config=config,
        **kwargs,
    )


fetch_html_with_browser.paper_fetch_html_fetcher_name = "nodriver"  # type: ignore[attr-defined]


def warm_browser_context(
    candidate_urls: list[str],
    *,
    publisher: str,
    config: BrowserRuntimeConfig,
    browser_context_seed: Mapping[str, Any] | None = None,
    runtime_context: Any | None = None,
) -> dict[str, Any]:
    return _nodriver_fetch.warm_browser_context_with_nodriver(
        candidate_urls,
        publisher=publisher,
        config=config,
        browser_context_seed=browser_context_seed,
        runtime_context=runtime_context,
    )
