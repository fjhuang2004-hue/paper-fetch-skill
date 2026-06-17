"""RSC (Royal Society of Chemistry) provider client — browser workflow (nodriver).

RSC platform: pubs.rsc.org (custom ASP.NET, not Atypon/Silverchair).
DOI prefix: 10.1039/.  Login: Shibboleth/CARSI via ``/en/account/logon``.
Full-text HTML: ``/en/content/articlehtml/{year}/{journal}/{doi}``.
"""

from __future__ import annotations

from ..extraction.html.availability_policy import AvailabilityPolicy
from ..extraction.html.provider_rules import ProviderHtmlRules
from ..provider_catalog import ProviderSpec
from . import browser_workflow
from ._registry import ProviderBundle, register_provider_bundle

register_provider_bundle(
    ProviderBundle(
        catalog=ProviderSpec(
            name="rsc",
            display_name="Royal Society of Chemistry",
            official=True,
            domains=("pubs.rsc.org", "www.rsc.org"),
            doi_prefixes=("10.1039/",),
            publisher_aliases=(
                "royal society of chemistry",
                "rsc",
                "royal society of chemistry (rsc)",
                "the royal society of chemistry",
            ),
            asset_default="body",
            probe_capability="routing_signal",
            provider_managed_abstract_only=False,
            client_factory_path="paper_fetch.providers.rsc:RscClient",
            status_order=13,
            base_domains=("pubs.rsc.org",),
            requires_browser_runtime=True,
        ),
        html_rules=ProviderHtmlRules(
            name="rsc",
            availability=AvailabilityPolicy(
                name="rsc",
            ),
        ),
        sources=("rsc",),
    )
)


def _rsc_fallback_authors(html: str) -> list[str]:
    from ._rsc_html import extract_authors
    return extract_authors(html)


RSC_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "rsc",
    fallback_author_extractor=_rsc_fallback_authors,
)


class RscClient(browser_workflow.BrowserWorkflowClient):
    name = RSC_BROWSER_PROFILE.name
    profile = RSC_BROWSER_PROFILE
