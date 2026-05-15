"""Browser-neutral runtime type aliases."""

from __future__ import annotations

from typing import TypedDict

from .._cloakbrowser import CloakBrowserFailure, CloakBrowserRuntimeConfig
from .._flaresolverr import FetchedPublisherHtml

BrowserRuntimeConfig = CloakBrowserRuntimeConfig
BrowserRuntimeFailure = CloakBrowserFailure
BrowserFetchedHtml = FetchedPublisherHtml


class BrowserImagePayload(TypedDict):
    bodyB64: str
    contentType: str
    url: str
    status: int
    width: int
    height: int
