"""American Meteorological Society provider client."""

from __future__ import annotations

from ..utils import normalize_text
from . import _ams_html, browser_workflow
from .base import RawFulltextPayload
from ..reason_codes import PDF_FALLBACK


AMS_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "ams",
    fallback_author_extractor=_ams_html.extract_authors,
)


class AmsClient(browser_workflow.BrowserWorkflowClient):
    name = AMS_BROWSER_PROFILE.name
    profile = AMS_BROWSER_PROFILE

    def article_source_for_payload(self, raw_payload: RawFulltextPayload) -> str:
        content = raw_payload.content
        route = normalize_text(content.route_kind if content is not None else "").lower()
        if route == PDF_FALLBACK:
            return "ams_pdf"
        return "ams_html"

    def to_article_model(self, *args, **kwargs):
        article = super().to_article_model(*args, **kwargs)
        return _ams_html.normalize_article_model(article)
