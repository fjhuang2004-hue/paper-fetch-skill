"""PNAS provider client."""

from __future__ import annotations

from . import _pnas_html, browser_workflow


PNAS_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "pnas",
    fallback_author_extractor=_pnas_html.extract_authors,
    direct_playwright_html_preflight=True,
)


class PnasClient(browser_workflow.BrowserWorkflowClient):
    name = PNAS_BROWSER_PROFILE.name
    profile = PNAS_BROWSER_PROFILE
