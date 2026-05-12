"""Science provider client."""

from __future__ import annotations

from ..provider_catalog import (
    provider_base_domains,
    provider_crossref_pdf_position,
    provider_domains,
    provider_html_path_templates,
    provider_pdf_path_templates,
)
from ..utils import provider_display_name
from . import _science_html, browser_workflow


SCIENCE_BROWSER_PROFILE = browser_workflow.ProviderBrowserProfile(
    name="science",
    article_source_name=None,
    label=provider_display_name("science"),
    hosts=provider_domains("science"),
    base_hosts=provider_base_domains("science"),
    html_path_templates=provider_html_path_templates("science"),
    pdf_path_templates=provider_pdf_path_templates("science"),
    crossref_pdf_position=provider_crossref_pdf_position("science"),
    markdown_publisher="science",
    fallback_author_extractor=_science_html.extract_authors,
    shared_playwright_image_fetcher=True,
)


class ScienceClient(browser_workflow.BrowserWorkflowClient):
    name = SCIENCE_BROWSER_PROFILE.name
    profile = SCIENCE_BROWSER_PROFILE
