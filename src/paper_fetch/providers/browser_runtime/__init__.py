"""Browser-neutral runtime contract for provider workflows."""

from .api import (
    ensure_runtime_ready,
    fetch_html_with_browser,
    load_runtime_config,
    probe_runtime_status,
    warm_browser_context,
)
from .types import (
    BrowserFetchedHtml,
    BrowserImagePayload,
    BrowserRuntimeConfig,
    BrowserRuntimeFailure,
)

__all__ = [
    "BrowserFetchedHtml",
    "BrowserImagePayload",
    "BrowserRuntimeConfig",
    "BrowserRuntimeFailure",
    "ensure_runtime_ready",
    "fetch_html_with_browser",
    "load_runtime_config",
    "probe_runtime_status",
    "warm_browser_context",
]
