"""Science provider client."""

from __future__ import annotations

from . import _science_html, browser_workflow


SCIENCE_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "science",
    fallback_author_extractor=_science_html.extract_authors,
)


class ScienceClient(browser_workflow.BrowserWorkflowClient):
    name = SCIENCE_BROWSER_PROFILE.name
    profile = SCIENCE_BROWSER_PROFILE
