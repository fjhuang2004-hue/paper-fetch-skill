"""PNAS provider client."""

from __future__ import annotations

from ..provider_catalog import (
    provider_base_domains,
    provider_crossref_pdf_position,
    provider_domains,
    provider_html_path_templates,
    provider_pdf_path_templates,
)
from ..utils import provider_display_name
from . import _pnas_html, browser_workflow


PNAS_BROWSER_PROFILE = browser_workflow.ProviderBrowserProfile(
    name="pnas",
    article_source_name=None,
    label=provider_display_name("pnas"),
    hosts=provider_domains("pnas"),
    base_hosts=provider_base_domains("pnas"),
    html_path_templates=provider_html_path_templates("pnas"),
    pdf_path_templates=provider_pdf_path_templates("pnas"),
    crossref_pdf_position=provider_crossref_pdf_position("pnas"),
    markdown_publisher="pnas",
    fallback_author_extractor=_pnas_html.extract_authors,
    shared_playwright_image_fetcher=True,
    direct_playwright_html_preflight=True,
)


class PnasClient(browser_workflow.BrowserWorkflowClient):
    name = PNAS_BROWSER_PROFILE.name
    profile = PNAS_BROWSER_PROFILE
