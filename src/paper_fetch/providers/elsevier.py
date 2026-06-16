"""Elsevier / ScienceDirect provider client — browser workflow (nodriver).

REST API logic has been extracted to ``elsevier_api.py`` for the future
OA shortcut pathway (step ②).
"""

from __future__ import annotations

from ..extraction.html.availability_policy import AvailabilityPolicy
from ..extraction.html.provider_rules import ProviderHtmlRules
from ..provider_catalog import ProviderSpec
from . import browser_workflow
from ._registry import ProviderBundle, register_provider_bundle
from ..quality.html_signals import ELSEVIER_AVAILABILITY_OVERRIDES


register_provider_bundle(
    ProviderBundle(
        catalog=ProviderSpec(
            name="elsevier",
            display_name="Elsevier",
            official=True,
            domains=("sciencedirect.com", "elsevier.com"),
            doi_prefixes=("10.1016/",),
            publisher_aliases=(
                "elsevier",
                "elsevier bv",
                "elsevier ltd",
                "elsevier masson sas",
            ),
            asset_default="body",
            probe_capability="routing_signal",
            provider_managed_abstract_only=False,
            client_factory_path="paper_fetch.providers.elsevier:ElsevierClient",
            status_order=1,
            base_domains=("sciencedirect.com",),
            requires_browser_runtime=True,
            api_hosts=("scopus.com", "www.scopus.com"),
            sensitive_headers=("x-els-apikey",),
        ),
        html_rules=ProviderHtmlRules(
            name="elsevier",
            availability=AvailabilityPolicy(
                name="elsevier",
                overrides=ELSEVIER_AVAILABILITY_OVERRIDES,
            ),
        ),
        sources=("elsevier",),
    )
)


def _elsevier_fallback_authors(html: str) -> list[str]:
    """Minimal author extraction fallback (browser workflow requirement)."""
    return []


ELSEVIER_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "elsevier",
    fallback_author_extractor=_elsevier_fallback_authors,
)


class ElsevierClient(browser_workflow.BrowserWorkflowClient):
    name = ELSEVIER_BROWSER_PROFILE.name
    profile = ELSEVIER_BROWSER_PROFILE
