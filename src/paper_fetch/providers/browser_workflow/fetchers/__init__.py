"""Internal browser fetchers for browser workflow assets."""

from __future__ import annotations

import time as time

from . import context as _context
from .context import (
    _BaseBrowserDocumentFetcher,
    _choose_playwright_seed_url,
    _normalized_response_headers,
)
from .diagnostics import (
    BROWSER_CONTEXT_ERROR,
    PLAYWRIGHT_CONTEXT_ERROR,
    _browser_image_payload_failure_reason,
    _compact_failure_diagnostic,
    _flaresolverr_image_payload_failure_reason,
)
from .file import (
    _SharedBrowserFileDocumentFetcher,
    _SharedPlaywrightFileDocumentFetcher,
    _ThreadLocalSharedBrowserFileDocumentFetcher,
    _ThreadLocalSharedPlaywrightFileDocumentFetcher,
    _build_shared_browser_file_fetcher,
    _build_shared_playwright_file_fetcher,
)
from .image import (
    _IMAGE_DOCUMENT_FETCH_TIMEOUT_MS,
    _SharedBrowserImageDocumentFetcher,
    _SharedPlaywrightImageDocumentFetcher,
    _ThreadLocalSharedBrowserImageDocumentFetcher,
    _ThreadLocalSharedPlaywrightImageDocumentFetcher,
    _browser_image_document_payload,
    _build_shared_browser_image_fetcher,
    _build_shared_playwright_image_fetcher,
    _flaresolverr_image_document_payload,
    fetch_image_document_with_playwright,
)
from .memo import _MemoizedFigurePageFetcher, _MemoizedImageDocumentFetcher

_LEGACY_BASE_PLAYWRIGHT_DOCUMENT_FETCHER = "_Base" "PlaywrightDocumentFetcher"
globals()[_LEGACY_BASE_PLAYWRIGHT_DOCUMENT_FETCHER] = getattr(
    _context,
    _LEGACY_BASE_PLAYWRIGHT_DOCUMENT_FETCHER,
)

__all__ = [
    "_IMAGE_DOCUMENT_FETCH_TIMEOUT_MS",
    "_MemoizedFigurePageFetcher",
    "_MemoizedImageDocumentFetcher",
    "_BaseBrowserDocumentFetcher",
    _LEGACY_BASE_PLAYWRIGHT_DOCUMENT_FETCHER,
    "_SharedBrowserFileDocumentFetcher",
    "_SharedBrowserImageDocumentFetcher",
    "_SharedPlaywrightFileDocumentFetcher",
    "_SharedPlaywrightImageDocumentFetcher",
    "_ThreadLocalSharedBrowserFileDocumentFetcher",
    "_ThreadLocalSharedBrowserImageDocumentFetcher",
    "_ThreadLocalSharedPlaywrightFileDocumentFetcher",
    "_ThreadLocalSharedPlaywrightImageDocumentFetcher",
    "_build_shared_browser_file_fetcher",
    "_build_shared_browser_image_fetcher",
    "_build_shared_playwright_file_fetcher",
    "_build_shared_playwright_image_fetcher",
    "_browser_image_document_payload",
    "_browser_image_payload_failure_reason",
    "_choose_playwright_seed_url",
    "_compact_failure_diagnostic",
    "_flaresolverr_image_document_payload",
    "_flaresolverr_image_payload_failure_reason",
    "_normalized_response_headers",
    "BROWSER_CONTEXT_ERROR",
    "PLAYWRIGHT_CONTEXT_ERROR",
    "fetch_image_document_with_playwright",
]
