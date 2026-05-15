"""Shared URL and signal helpers for browser-workflow providers."""

from __future__ import annotations

import urllib.parse

from dataclasses import dataclass
from typing import Any, Callable

from ...quality import html_profiles as _html_profiles
from ...utils import normalize_text
from .._pdf_candidates import extract_pdf_url_from_crossref as extract_pdf_url_from_crossref

HTML_STRONG_FULLTEXT_MARKERS = _html_profiles.HTML_STRONG_FULLTEXT_MARKERS
HTML_STRUCTURE_MARKERS = _html_profiles.HTML_STRUCTURE_MARKERS
dedupe_signals = _html_profiles.dedupe_signals
default_positive_signals = _html_profiles.default_positive_signals
looks_like_abstract_redirect = _html_profiles.looks_like_abstract_redirect
BROWSER_HTML_BLOCKED_RESOURCE_TYPES = {"image", "font", "stylesheet", "media"}


_BROWSER_WORKFLOW_DEP_FIELDS = (
    "load_runtime_config",
    "ensure_runtime_ready",
    "probe_runtime_status",
    "fetch_html_with_browser",
    "warm_browser_context",
    "fetch_seeded_browser_pdf_payload",
    "fetch_pdf_with_browser",
    "download_assets",
    "split_body_and_supplementary_assets",
    "bootstrap_browser_workflow",
    "_build_shared_browser_file_fetcher",
    "_build_shared_browser_image_fetcher",
    "extract_atypon_browser_workflow_markdown",
    "pdf_browser_context_seed",
    "refresh_browser_context_seed",
    "fetch_html_with_fast_browser",
    "_cached_browser_workflow_markdown",
    "_cached_browser_workflow_assets",
    "_assets_matching_download_failures",
    "_browser_workflow_image_download_candidates",
)

_LEGACY_DEP_ALIASES = {
    "fetch_html_with_flaresolverr": "fetch_html_with_browser",  # legacy alias
    "warm_browser_context_with_flaresolverr": "warm_browser_context",  # legacy alias
    "fetch_pdf_with_playwright": "fetch_pdf_with_browser",
    "fetch_html_with_direct_playwright": "fetch_html_with_fast_browser",
    "_build_shared_playwright_file_fetcher": "_build_shared_browser_file_fetcher",
    "_build_shared_playwright_image_fetcher": "_build_shared_browser_image_fetcher",
}


def _mark_legacy_html_fetcher(fetcher: Any) -> None:
    marker = getattr(fetcher, "paper_fetch_html_fetcher_name", None)
    if isinstance(marker, str):
        return
    try:
        setattr(fetcher, "paper_fetch_html_fetcher_name", "flaresolverr")
    except Exception:
        pass


@dataclass(frozen=True, init=False)
class BrowserWorkflowDeps:
    load_runtime_config: Callable[..., Any]
    ensure_runtime_ready: Callable[..., Any]
    probe_runtime_status: Callable[..., Any]
    fetch_html_with_browser: Callable[..., Any]
    warm_browser_context: Callable[..., Any]
    fetch_seeded_browser_pdf_payload: Callable[..., Any]
    fetch_pdf_with_browser: Callable[..., Any]
    download_assets: Callable[..., Any]
    split_body_and_supplementary_assets: Callable[..., Any]
    bootstrap_browser_workflow: Callable[..., Any]
    _build_shared_browser_file_fetcher: Callable[..., Any]
    _build_shared_browser_image_fetcher: Callable[..., Any]
    extract_atypon_browser_workflow_markdown: Callable[..., Any]
    pdf_browser_context_seed: Callable[..., Any]
    refresh_browser_context_seed: Callable[..., Any]
    fetch_html_with_fast_browser: Callable[..., Any]
    _cached_browser_workflow_markdown: Callable[..., Any]
    _cached_browser_workflow_assets: Callable[..., Any]
    _assets_matching_download_failures: Callable[..., Any]
    _browser_workflow_image_download_candidates: Callable[..., Any]

    def __init__(self, **values: Any) -> None:
        values = dict(values)
        for alias, target in _LEGACY_DEP_ALIASES.items():
            if alias not in values:
                continue
            alias_value = values.pop(alias)
            if alias == "fetch_html_with_flaresolverr":  # legacy alias
                _mark_legacy_html_fetcher(alias_value)
            values[target] = alias_value

        unknown = sorted(set(values) - set(_BROWSER_WORKFLOW_DEP_FIELDS))
        if unknown:
            unknown_display = ", ".join(unknown)
            raise TypeError(f"Unexpected BrowserWorkflowDeps field(s): {unknown_display}")

        missing = [name for name in _BROWSER_WORKFLOW_DEP_FIELDS if name not in values]
        if missing:
            missing_display = ", ".join(missing)
            raise TypeError(f"Missing BrowserWorkflowDeps field(s): {missing_display}")

        for name in _BROWSER_WORKFLOW_DEP_FIELDS:
            object.__setattr__(self, name, values[name])

    @property
    def fetch_html_with_flaresolverr(self) -> Callable[..., Any]:  # legacy alias
        return self.fetch_html_with_browser

    @property
    def warm_browser_context_with_flaresolverr(self) -> Callable[..., Any]:  # legacy alias
        return self.warm_browser_context

    @property
    def fetch_pdf_with_playwright(self) -> Callable[..., Any]:
        return self.fetch_pdf_with_browser

    @property
    def fetch_html_with_direct_playwright(self) -> Callable[..., Any]:
        return self.fetch_html_with_fast_browser

    @property
    def _build_shared_playwright_file_fetcher(self) -> Callable[..., Any]:
        return self._build_shared_browser_file_fetcher

    @property
    def _build_shared_playwright_image_fetcher(self) -> Callable[..., Any]:
        return self._build_shared_browser_image_fetcher


def default_browser_workflow_deps() -> BrowserWorkflowDeps:
    """返回生产默认依赖。"""
    from ...extraction.html.assets import (
        download_assets,
        split_body_and_supplementary_assets,
    )
    from ..browser_runtime import (
        ensure_runtime_ready,
        fetch_html_with_browser,
        load_runtime_config,
        probe_runtime_status,
        warm_browser_context,
    )
    from .._pdf_fallback import fetch_pdf_with_playwright
    from ..atypon_browser_workflow import extract_atypon_browser_workflow_markdown
    from .assets import (
        _assets_matching_download_failures,
        _browser_workflow_image_download_candidates,
        _cached_browser_workflow_assets,
    )
    from .bootstrap import bootstrap_browser_workflow
    from .fetchers import (
        _build_shared_playwright_file_fetcher,
        _build_shared_playwright_image_fetcher,
    )
    from .html_extraction import (
        _cached_browser_workflow_markdown,
        fetch_html_with_direct_playwright,
    )
    from .pdf_fallback import fetch_seeded_browser_pdf_payload

    return BrowserWorkflowDeps(
        load_runtime_config=load_runtime_config,
        ensure_runtime_ready=ensure_runtime_ready,
        probe_runtime_status=probe_runtime_status,
        fetch_html_with_browser=fetch_html_with_browser,
        warm_browser_context=warm_browser_context,
        fetch_seeded_browser_pdf_payload=fetch_seeded_browser_pdf_payload,
        fetch_pdf_with_browser=fetch_pdf_with_playwright,
        download_assets=download_assets,
        split_body_and_supplementary_assets=split_body_and_supplementary_assets,
        bootstrap_browser_workflow=bootstrap_browser_workflow,
        _build_shared_browser_file_fetcher=_build_shared_playwright_file_fetcher,
        _build_shared_browser_image_fetcher=_build_shared_playwright_image_fetcher,
        extract_atypon_browser_workflow_markdown=extract_atypon_browser_workflow_markdown,
        pdf_browser_context_seed=warm_browser_context,
        refresh_browser_context_seed=warm_browser_context,
        fetch_html_with_fast_browser=fetch_html_with_direct_playwright,
        _cached_browser_workflow_markdown=_cached_browser_workflow_markdown,
        _cached_browser_workflow_assets=_cached_browser_workflow_assets,
        _assets_matching_download_failures=_assets_matching_download_failures,
        _browser_workflow_image_download_candidates=_browser_workflow_image_download_candidates,
    )


def default_browser_workflow_deps_with_legacy_aliases() -> BrowserWorkflowDeps:
    """返回带旧属性别名的默认依赖，仅供迁移期旧测试使用。"""
    return default_browser_workflow_deps()


def preferred_html_candidate_from_landing_page(
    doi: str,
    landing_page_url: str | None,
    *,
    hosts: tuple[str, ...],
) -> str | None:
    candidate = normalize_text(landing_page_url)
    if not candidate:
        return None
    parsed = urllib.parse.urlparse(candidate)
    hostname = normalize_text(parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not any(
        hostname == token or hostname.endswith(f".{token}")
        for token in hosts
    ):
        return None
    unquoted_candidate = normalize_text(urllib.parse.unquote(candidate)).lower()
    normalized_doi = normalize_text(doi).lower()
    doi_suffix = normalized_doi.split("/", 1)[1] if "/" in normalized_doi else ""
    if normalized_doi not in unquoted_candidate and (
        not doi_suffix or doi_suffix not in unquoted_candidate
    ):
        return None
    return candidate


def build_base_urls(
    *,
    hosts: tuple[str, ...],
    base_hosts: tuple[str, ...],
    landing_page_url: str | None = None,
) -> list[str]:
    preferred = normalize_text(landing_page_url)
    base_urls: list[str] = []
    if preferred:
        parsed = urllib.parse.urlparse(preferred)
        hostname = normalize_text(parsed.hostname or "").lower()
        if parsed.scheme in {"http", "https"} and hostname:
            if any(hostname == token or hostname.endswith(f".{token}") for token in hosts):
                base_urls.append(f"{parsed.scheme}://{hostname}")
    for host in base_hosts or hosts:
        candidate = f"https://{host}"
        if candidate not in base_urls:
            base_urls.append(candidate)
    return base_urls


def _append_unique(candidates: list[str], candidate: str | None) -> None:
    normalized = normalize_text(candidate)
    if normalized and normalized not in candidates:
        candidates.append(normalized)


def build_browser_workflow_html_candidates(
    doi: str,
    landing_page_url: str | None,
    *,
    hosts: tuple[str, ...],
    base_hosts: tuple[str, ...],
    path_templates: tuple[str, ...],
) -> list[str]:
    candidates: list[str] = []
    preferred_candidate = preferred_html_candidate_from_landing_page(
        doi,
        landing_page_url,
        hosts=hosts,
    )
    _append_unique(candidates, preferred_candidate)
    for base in build_base_urls(hosts=hosts, base_hosts=base_hosts, landing_page_url=landing_page_url):
        for template in path_templates:
            _append_unique(candidates, f"{base}{template.format(doi=doi)}")
    return candidates


def build_browser_workflow_pdf_candidates(
    doi: str,
    crossref_pdf_url: str | None,
    *,
    hosts: tuple[str, ...],
    base_hosts: tuple[str, ...],
    path_templates: tuple[str, ...],
    crossref_pdf_position: int,
    base_seed_url: str | None = None,
) -> list[str]:
    generated_candidates: list[str] = []
    for base in build_base_urls(hosts=hosts, base_hosts=base_hosts, landing_page_url=base_seed_url):
        for template in path_templates:
            _append_unique(generated_candidates, f"{base}{template.format(doi=doi)}")

    crossref_candidate = normalize_text(crossref_pdf_url)
    if not crossref_candidate:
        return generated_candidates

    candidates: list[str] = []
    inserted = False
    insert_at = max(crossref_pdf_position, 0)
    for index, candidate in enumerate(generated_candidates):
        if index == insert_at:
            _append_unique(candidates, crossref_candidate)
            inserted = True
        _append_unique(candidates, candidate)
    if not inserted:
        _append_unique(candidates, crossref_candidate)
    return candidates
