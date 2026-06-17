"""ASM (American Society for Microbiology) provider client — browser workflow (nodriver).

ASM platform: journals.asm.org (Atypon Literatum with pb frontend).
DOI prefix: 10.1128/.  No institutional login handler — HZAU does not
subscribe.  OA articles are fetched via CF bypass + browser HTML extraction;
paywalled articles fall back to metadata-only via the quality assessor.

Full-text HTML: landing page at ``/doi/{doi}`` renders full text for OA
articles inside ``<article>`` → ``#bodymatter``.
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
            name="asm",
            display_name="American Society for Microbiology",
            official=True,
            domains=("journals.asm.org",),
            doi_prefixes=("10.1128/",),
            publisher_aliases=(
                "american society for microbiology",
                "asm",
                "asm press",
                "american society for microbiology (asm)",
            ),
            asset_default="body",
            probe_capability="routing_signal",
            provider_managed_abstract_only=False,
            client_factory_path="paper_fetch.providers.asm:AsmClient",
            status_order=14,
            base_domains=("journals.asm.org",),
            requires_browser_runtime=True,
        ),
        html_rules=ProviderHtmlRules(
            name="asm",
            availability=AvailabilityPolicy(
                name="asm",
            ),
        ),
        sources=("asm",),
    )
)


def _asm_fallback_authors(html: str) -> list[str]:
    """Extract author names from ASM article HTML.

    Looks for ``<meta name="dc.Creator">`` tags in the document head,
    which ASM always emits for both OA and paywalled pages.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    authors: list[str] = []
    for meta in soup.select('meta[name="dc.Creator"]'):
        content = (meta.get("content") or "").strip()
        if content:
            authors.append(content)
    return authors


ASM_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "asm",
    fallback_author_extractor=_asm_fallback_authors,
)


class AsmClient(browser_workflow.BrowserWorkflowClient):
    name = ASM_BROWSER_PROFILE.name
    profile = ASM_BROWSER_PROFILE
