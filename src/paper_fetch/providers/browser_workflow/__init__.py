"""Shared browser-workflow runtime helpers for Wiley/Science/PNAS/AMS.

Migration aliases may resolve old and browser-neutral names to the same target;
``__getattr__`` caches each public name independently in module globals.
"""

from __future__ import annotations

from importlib import import_module
import time as time
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "BrowserWorkflowBootstrapResult": (".profile", "BrowserWorkflowBootstrapResult"),
    "BrowserWorkflowClient": (".client", "BrowserWorkflowClient"),
    "FlareSolverrFailure": ("paper_fetch.providers._flaresolverr", "FlareSolverrFailure"),
    "PdfFallbackFailure": ("paper_fetch.providers._pdf_fallback", "PdfFallbackFailure"),
    "ProviderBrowserProfile": (".profile", "ProviderBrowserProfile"),
    "make_atypon_browser_profile": (".profile", "make_atypon_browser_profile"),
    "HtmlExtractionFailure": ("paper_fetch.extraction.html.signals", "HtmlExtractionFailure"),
    "_IMAGE_DOCUMENT_FETCH_TIMEOUT_MS": (".fetchers", "_IMAGE_DOCUMENT_FETCH_TIMEOUT_MS"),
    "_BasePlaywrightDocumentFetcher": (".fetchers", "_BasePlaywrightDocumentFetcher"),
    "_MemoizedFigurePageFetcher": (".fetchers", "_MemoizedFigurePageFetcher"),
    "_MemoizedImageDocumentFetcher": (".fetchers", "_MemoizedImageDocumentFetcher"),
    "_SharedPlaywrightFileDocumentFetcher": (".fetchers", "_SharedPlaywrightFileDocumentFetcher"),
    "_SharedPlaywrightImageDocumentFetcher": (".fetchers", "_SharedPlaywrightImageDocumentFetcher"),
    "_ThreadLocalSharedPlaywrightFileDocumentFetcher": (
        ".fetchers",
        "_ThreadLocalSharedPlaywrightFileDocumentFetcher",
    ),
    "_ThreadLocalSharedPlaywrightImageDocumentFetcher": (
        ".fetchers",
        "_ThreadLocalSharedPlaywrightImageDocumentFetcher",
    ),
    "_assets_matching_download_failures": (".assets", "_assets_matching_download_failures"),
    "_browser_workflow_html_payload": (".html_extraction", "_browser_workflow_html_payload"),
    "_browser_workflow_image_download_candidates": (".assets", "_browser_workflow_image_download_candidates"),
    "_build_shared_playwright_file_fetcher": (".fetchers", "_build_shared_playwright_file_fetcher"),
    "_build_shared_playwright_image_fetcher": (".fetchers", "_build_shared_playwright_image_fetcher"),
    "_cached_browser_workflow_assets": (".assets", "_cached_browser_workflow_assets"),
    "_cached_browser_workflow_markdown": (".html_extraction", "_cached_browser_workflow_markdown"),
    "_choose_playwright_seed_url": (".fetchers", "_choose_playwright_seed_url"),
    "_compact_failure_diagnostic": (".fetchers", "_compact_failure_diagnostic"),
    "_fetch_browser_html_payload": (".bootstrap", "_fetch_browser_html_payload"),
    "_fetch_browser_html_payload_with_fast_path": (".bootstrap", "_fetch_browser_html_payload_with_fast_path"),
    "_fetch_flaresolverr_html_payload": (".bootstrap", "_fetch_flaresolverr_html_payload"),
    "_fetch_flaresolverr_html_payload_with_fast_path": (
        ".bootstrap",
        "_fetch_flaresolverr_html_payload_with_fast_path",
    ),
    "_flaresolverr_image_document_payload": (".fetchers", "_flaresolverr_image_document_payload"),
    "_flaresolverr_image_payload_failure_reason": (".fetchers", "_flaresolverr_image_payload_failure_reason"),
    "_merge_download_attempt_results": (".assets", "_merge_download_attempt_results"),
    "_normalized_response_headers": (".fetchers", "_normalized_response_headers"),
    "bootstrap_browser_workflow": (".bootstrap", "bootstrap_browser_workflow"),
    "browser_workflow_article_from_payload": (".article", "browser_workflow_article_from_payload"),
    "build_browser_workflow_html_candidates": (".shared", "build_browser_workflow_html_candidates"),
    "build_browser_workflow_pdf_candidates": (".shared", "build_browser_workflow_pdf_candidates"),
    "download_assets": ("paper_fetch.extraction.html.assets", "download_assets"),
    "ensure_runtime_ready": ("paper_fetch.providers._cloakbrowser", "ensure_runtime_ready"),
    "extract_pdf_url_from_crossref": (".shared", "extract_pdf_url_from_crossref"),
    "extract_atypon_browser_workflow_markdown": (".html_extraction", "extract_atypon_browser_workflow_markdown"),
    "fetch_html_with_direct_playwright": (".html_extraction", "fetch_html_with_direct_playwright"),
    "fetch_html_with_fast_browser": (".html_extraction", "fetch_html_with_direct_playwright"),
    "fetch_html_with_browser": ("paper_fetch.providers.browser_runtime", "fetch_html_with_browser"),
    "fetch_html_with_flaresolverr": ("paper_fetch.providers._cloakbrowser", "fetch_html_with_cloakbrowser"),
    "fetch_image_document_with_playwright": (".fetchers", "fetch_image_document_with_playwright"),
    "fetch_pdf_with_browser": ("paper_fetch.providers._pdf_fallback", "fetch_pdf_with_playwright"),
    "fetch_pdf_with_playwright": ("paper_fetch.providers._pdf_fallback", "fetch_pdf_with_playwright"),
    "fetch_seeded_browser_pdf_payload": (".pdf_fallback", "fetch_seeded_browser_pdf_payload"),
    "load_runtime_config": ("paper_fetch.providers._cloakbrowser", "load_runtime_config"),
    "merge_browser_context_seeds": ("paper_fetch.providers._flaresolverr", "merge_browser_context_seeds"),
    "merge_provider_owned_authors": (".article", "merge_provider_owned_authors"),
    "probe_runtime_status": ("paper_fetch.providers._cloakbrowser", "probe_runtime_status"),
    "rewrite_inline_figure_links": (".html_extraction", "rewrite_inline_figure_links"),
    "warm_browser_context": ("paper_fetch.providers.browser_runtime", "warm_browser_context"),
    "warm_browser_context_with_flaresolverr": (
        "paper_fetch.providers._cloakbrowser",
        "warm_browser_context_with_cloakbrowser",
    ),
}

__all__ = [*_EXPORTS, "time"]


def __getattr__(name: str) -> Any:
    if name == "time":
        return time
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *__all__])
