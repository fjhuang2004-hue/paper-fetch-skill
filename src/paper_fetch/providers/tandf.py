"""Taylor & Francis provider client — Atypon browser workflow with CloakBrowser HTML path and PDF fallback."""

from __future__ import annotations

from typing import Any, Mapping

from ..extraction.html.availability_policy import AvailabilityPolicy
from ..extraction.html.provider_rules import (
    ProviderHtmlRules,
    DomHooks,
)
from ..provider_catalog import (
    ATYPON_DEFAULT_PDF_PATH_TEMPLATES,
    ProviderSpec,
)
from ..reason_codes import NO_RESULT, NOT_CONFIGURED
from ..runtime import RuntimeContext
from ..utils import normalize_text
from . import _tandf_html, browser_workflow
from .browser_workflow.shared import BrowserWorkflowDeps, default_browser_workflow_deps
from ._pdf_fallback import PdfFallbackFailure
from ._registry import ProviderBundle, register_provider_bundle
from .base import (
    ProviderContent,
    ProviderFailure,
    ProviderStatusResult,
    RawFulltextPayload,
    build_provider_status_check,
    summarize_capability_status,
    OK,
)

register_provider_bundle(
    ProviderBundle(
        catalog=ProviderSpec(
            name="tandf",
            display_name="Taylor & Francis",
            official=True,
            domains=("tandfonline.com", "www.tandfonline.com"),
            doi_prefixes=("10.1080/",),
            publisher_aliases=(
                "taylor & francis",
                "taylor and francis",
                "tandf",
            ),
            asset_default="body",
            probe_capability="routing_signal",
            provider_managed_abstract_only=True,
            client_factory_path="paper_fetch.providers.tandf:TandfClient",
            status_order=18,
            base_domains=("tandfonline.com",),
            html_path_templates=("/doi/full/{doi}", "/doi/{doi}"),
            pdf_path_templates=ATYPON_DEFAULT_PDF_PATH_TEMPLATES,
            crossref_pdf_position=1,
            requires_playwright=True,
        ),
        html_rules=ProviderHtmlRules(
            name="tandf",
            availability=AvailabilityPolicy(
                name="tandf",
            ),
            dom_hooks=DomHooks(
                body_container=_tandf_html.tandf_body_container,
            ),
        ),
        sources=("tandf_browser",),
    )
)

TANDF_BROWSER_PROFILE = browser_workflow.make_atypon_browser_profile(
    "tandf",
    article_source_name="tandf_browser",
    fallback_author_extractor=_tandf_html.extract_authors,
)


class TandfClient(browser_workflow.BrowserWorkflowClient):
    name = TANDF_BROWSER_PROFILE.name
    profile = TANDF_BROWSER_PROFILE

    def __init__(
        self,
        transport,
        env: Mapping[str, str],
        deps: BrowserWorkflowDeps = default_browser_workflow_deps(),
    ) -> None:
        super().__init__(transport, env, deps=deps)

    def probe_status(self) -> ProviderStatusResult:
        browser_status = self.deps.probe_runtime_status(self.env, provider=self.name)
        browser_checks = list(browser_status.checks)
        browser_ready = bool(browser_checks) and all(
            check.status == OK for check in browser_checks
        )
        return summarize_capability_status(
            self.name,
            official_provider=self.official_provider,
            checks=[
                *browser_checks,
                build_provider_status_check(
                    "browser_runtime",
                    OK if browser_ready else NOT_CONFIGURED,
                    (
                        f"{self.name} CloakBrowser runtime is ready."
                        if browser_ready
                        else f"{self.name} requires a CloakBrowser runtime."
                    ),
                ),
            ],
        )

    def fetch_raw_fulltext(
        self,
        doi: str,
        metadata: Mapping[str, Any],
        *,
        context: RuntimeContext | None = None,
    ) -> RawFulltextPayload:
        """使用 CDP 连接 Chrome 获取 HTML，然后走提取管线。"""
        from .cdp_browser import CdpBrowser, detect_paywall, NeedLogin
        from ..publisher_identity import normalize_doi

        normalized_doi = normalize_doi(doi) or doi
        url = f"https://www.tandfonline.com/doi/full/{normalized_doi}"

        # 1. 通过 CDP 获取 HTML
        try:
            cdp = CdpBrowser.connect()
        except RuntimeError as exc:
            raise ProviderFailure(
                NOT_CONFIGURED,
                str(exc),
            ) from exc

        try:
            html = cdp.fetch_html(url, wait_for=".hlFld-Fulltext")
        finally:
            cdp.close()

        # 2. 检测 paywall
        if detect_paywall(html):
            raise ProviderFailure(
                NO_RESULT,
                f"{self.name}: 检测到 paywall，可能未登录 HZAU。"
                f"请用 CARSI 登录到 {url} 后重试。",
            )

        # 3. 交给提取管线
        from ..extraction.html import decode_html
        return RawFulltextPayload(
            provider=self.name,
            source_url=url,
            content_type="text/html",
            body=html.encode("utf-8") if isinstance(html, str) else html,
            content=ProviderContent(
                route_kind="html",
                source_url=url,
                content_type="text/html",
                body=html.encode("utf-8") if isinstance(html, str) else html,
            ),
        )


__all__ = ["TandfClient"]
