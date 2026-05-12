"""Wiley provider client with browser HTML and official TDM PDF fallbacks."""

from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from ..http import DEFAULT_FULLTEXT_TIMEOUT_SECONDS, RequestFailure
from ..provider_catalog import (
    provider_api_url_template,
    provider_base_domains,
    provider_crossref_pdf_position,
    provider_domains,
    provider_html_path_templates,
    provider_pdf_path_templates,
)
from ..runtime import RuntimeContext
from ..tracing import fulltext_marker
from ..utils import normalize_text, provider_display_name
from . import _wiley_html, browser_workflow
from ._pdf_fallback import PdfFallbackFailure, PdfFallbackStrategy, fetch_pdf_over_http
from ._pdf_common import PdfFetchResult, pdf_fetch_result_from_response
from ._waterfall import (
    ProviderWaterfallStep,
    ProviderWaterfallState,
    run_provider_waterfall,
)
from .base import (
    ProviderContent,
    ProviderFailure,
    ProviderStatusResult,
    RawFulltextPayload,
    build_provider_status_check,
    summarize_capability_status,
)

WILEY_TDM_CLIENT_TOKEN_ENV_VAR = "WILEY_TDM_CLIENT_TOKEN"
WILEY_TDM_API_TEMPLATE_NAME = "tdm_pdf"

WILEY_BROWSER_PROFILE = browser_workflow.ProviderBrowserProfile(
    name="wiley",
    article_source_name="wiley_browser",
    label=provider_display_name("wiley"),
    hosts=provider_domains("wiley"),
    base_hosts=provider_base_domains("wiley"),
    html_path_templates=provider_html_path_templates("wiley"),
    pdf_path_templates=provider_pdf_path_templates("wiley"),
    crossref_pdf_position=provider_crossref_pdf_position("wiley"),
    markdown_publisher="wiley",
    fallback_author_extractor=_wiley_html.extract_authors,
    shared_playwright_image_fetcher=True,
)


def _fetch_wiley_tdm_pdf_result(
    transport,
    *,
    api_url: str,
    headers: Mapping[str, str],
    artifact_dir=None,
    timeout: int = DEFAULT_FULLTEXT_TIMEOUT_SECONDS,
) -> PdfFetchResult:
    request_headers = {"Accept": "application/pdf,*/*;q=0.8", **dict(headers)}
    try:
        response = transport.request(
            "GET",
            api_url,
            headers=request_headers,
            timeout=timeout,
            retry_on_transient=True,
        )
    except RequestFailure as exc:
        raise PdfFallbackFailure(
            "pdf_download_failed",
            f"Failed to download Wiley API PDF fallback candidate: {exc}",
            details={"source_url": api_url},
        ) from exc

    response_headers = {
        str(key).lower(): str(value)
        for key, value in (response.get("headers") or {}).items()
    }
    final_url = str(response.get("url") or api_url)
    location = normalize_text(response_headers.get("location"))
    if int(response.get("status_code") or 0) in {301, 302, 303, 307, 308} and location:
        redirected_url = urllib.parse.urljoin(api_url, location)
        return PdfFallbackStrategy(
            transport=transport,
            headers=request_headers,
            timeout=timeout,
            artifact_dir=artifact_dir,
            fetcher=fetch_pdf_over_http,
        ).fetch([redirected_url])

    return pdf_fetch_result_from_response(
        response,
        artifact_dir=artifact_dir,
        source_url=api_url,
        final_url=final_url,
        not_pdf_message="Wiley API PDF fallback did not return a PDF file.",
    )


class WileyClient(browser_workflow.BrowserWorkflowClient):
    name = WILEY_BROWSER_PROFILE.name
    profile = WILEY_BROWSER_PROFILE

    def __init__(self, transport, env: Mapping[str, str]) -> None:
        super().__init__(transport, env)
        self.tdm_client_token = str(
            self.env.get(WILEY_TDM_CLIENT_TOKEN_ENV_VAR, "")
        ).strip()

    def _tdm_api_url(self, doi: str) -> str:
        template = provider_api_url_template(self.name, WILEY_TDM_API_TEMPLATE_NAME)
        if template is None:
            raise ProviderFailure(
                "not_configured",
                "Wiley TDM API URL template is not declared in provider catalog.",
            )
        return template.format(doi=urllib.parse.quote(doi, safe=""))

    def _tdm_api_headers(self) -> dict[str, str]:
        return {
            "Wiley-TDM-Client-Token": self.tdm_client_token,
            "User-Agent": self.user_agent,
        }

    def probe_status(self) -> ProviderStatusResult:
        browser_status = browser_workflow.probe_runtime_status(
            self.env, provider=self.name
        )
        token_configured = bool(self.tdm_client_token)
        browser_ready = bool(browser_status.checks) and all(
            check.status == "ok" for check in browser_status.checks
        )
        return summarize_capability_status(
            self.name,
            official_provider=self.official_provider,
            checks=[
                *browser_status.checks,
                build_provider_status_check(
                    "tdm_api_token",
                    "ok" if token_configured or browser_ready else "not_configured",
                    (
                        "Wiley TDM API client token is configured."
                        if token_configured
                        else (
                            "Wiley TDM API client token is optional when the browser workflow runtime is ready."
                            if browser_ready
                            else (
                                f"{WILEY_TDM_CLIENT_TOKEN_ENV_VAR} enables the official Wiley PDF lane when browser PDF fallback "
                                "is unavailable."
                            )
                        )
                    ),
                    missing_env=[]
                    if token_configured or browser_ready
                    else [WILEY_TDM_CLIENT_TOKEN_ENV_VAR],
                    details={"env_var": WILEY_TDM_CLIENT_TOKEN_ENV_VAR},
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
        context = self._runtime_context(context)
        bootstrap = browser_workflow.bootstrap_browser_workflow(
            self,
            doi,
            metadata,
            allow_runtime_failure=True,
            context=context,
        )
        if bootstrap.html_payload is not None:
            return bootstrap.html_payload

        initial_warnings = [*bootstrap.warnings]
        if bootstrap.runtime is not None:
            initial_warnings.append(
                f"{self.name} HTML route was not usable "
                f"({bootstrap.html_failure_reason or 'html_failed'}); attempting Wiley publisher PDF/ePDF fallback."
            )
        else:
            initial_warnings.append(
                f"{self.name} HTML route was not usable "
                f"({bootstrap.html_failure_reason or 'html_failed'}); attempting Wiley TDM API PDF fallback."
            )
        if bootstrap.runtime is None and bootstrap.runtime_failure is not None:
            initial_warnings.append(
                f"Wiley browser PDF/ePDF fallback was not attempted because {bootstrap.runtime_failure.message}"
            )

        def run_tdm_api(_state: ProviderWaterfallState) -> RawFulltextPayload:
            if not self.tdm_client_token:
                raise ProviderFailure(
                    "not_configured",
                    f"Wiley TDM API PDF fallback is not configured because {WILEY_TDM_CLIENT_TOKEN_ENV_VAR} is missing.",
                    missing_env=[WILEY_TDM_CLIENT_TOKEN_ENV_VAR],
                )

            api_url = self._tdm_api_url(bootstrap.normalized_doi)
            try:
                pdf_result = _fetch_wiley_tdm_pdf_result(
                    self.transport,
                    api_url=api_url,
                    headers=self._tdm_api_headers(),
                    artifact_dir=(bootstrap.runtime.artifact_dir / "pdf_api_fallback")
                    if bootstrap.runtime is not None
                    else None,
                )
            except PdfFallbackFailure as exc:
                raise ProviderFailure("no_result", exc.message) from exc

            return RawFulltextPayload(
                provider=self.name,
                source_url=pdf_result.final_url,
                content_type="application/pdf",
                body=pdf_result.pdf_bytes,
                content=ProviderContent(
                    route_kind="pdf_fallback",
                    source_url=pdf_result.final_url,
                    content_type="application/pdf",
                    body=pdf_result.pdf_bytes,
                    markdown_text=pdf_result.markdown_text,
                    html_failure_reason=bootstrap.html_failure_reason,
                    html_failure_message=bootstrap.html_failure_message,
                    suggested_filename=pdf_result.suggested_filename,
                ),
                needs_local_copy=True,
            )

        def run_browser_pdf(_state: ProviderWaterfallState) -> RawFulltextPayload:
            if bootstrap.runtime is None:
                raise ProviderFailure(
                    "not_configured",
                    bootstrap.runtime_failure.message
                    if bootstrap.runtime_failure is not None
                    else "Wiley browser runtime is not configured.",
                    missing_env=bootstrap.runtime_failure.missing_env
                    if bootstrap.runtime_failure is not None
                    else [],
                )
            try:
                return browser_workflow.fetch_seeded_browser_pdf_payload(
                    provider=self.name,
                    runtime=bootstrap.runtime,
                    pdf_candidates=bootstrap.pdf_candidates,
                    html_candidates=bootstrap.html_candidates,
                    landing_page_url=bootstrap.landing_page_url,
                    user_agent=self.user_agent,
                    browser_context_seed=bootstrap.browser_context_seed,
                    html_failure_reason=bootstrap.html_failure_reason,
                    html_failure_message=bootstrap.html_failure_message,
                    warnings=[],
                    success_source_trail=[],
                    success_warning=(
                        "Full text was extracted from the Wiley publisher PDF/ePDF fallback after the HTML path was not usable."
                    ),
                    artifact_subdir="browser_pdf_fallback",
                    context=context,
                )
            except PdfFallbackFailure as exc:
                raise ProviderFailure("no_result", exc.message) from exc

        def browser_failure_warning(
            failure: ProviderFailure, _state: ProviderWaterfallState
        ) -> str:
            if self.tdm_client_token:
                return (
                    f"Wiley publisher PDF/ePDF fallback was not usable ({failure.message}); "
                    "attempting Wiley TDM API PDF fallback."
                )
            return (
                f"Wiley publisher PDF/ePDF fallback was not usable ({failure.message})."
            )

        def tdm_failure_warning(
            failure: ProviderFailure, _state: ProviderWaterfallState
        ) -> str:
            if failure.code == "not_configured":
                return failure.message
            return f"Wiley TDM API PDF fallback was not usable ({failure.message})."

        def final_failure(state: ProviderWaterfallState) -> ProviderFailure:
            api_failure = next(
                (failure for label, failure in state.failures if label == "pdf_api"),
                None,
            )
            browser_failure = next(
                (
                    failure
                    for label, failure in state.failures
                    if label == "browser_pdf"
                ),
                None,
            )
            failure_parts = [
                f"HTML failure: {bootstrap.html_failure_message or 'wiley HTML route failed.'}"
            ]
            if browser_failure is not None:
                failure_parts.append(
                    f"Wiley browser PDF failure: {browser_failure.message}"
                )
            elif bootstrap.runtime is None and bootstrap.runtime_failure is not None:
                failure_parts.append(
                    f"Wiley browser PDF failure: {bootstrap.runtime_failure.message}"
                )
            if api_failure is not None:
                failure_parts.append(f"Wiley API PDF failure: {api_failure.message}")

            missing_env: list[str] = []
            if bootstrap.runtime is None and bootstrap.runtime_failure is not None:
                missing_env.extend(bootstrap.runtime_failure.missing_env)
            if (
                bootstrap.runtime is None
                and not self.tdm_client_token
                and WILEY_TDM_CLIENT_TOKEN_ENV_VAR not in missing_env
            ):
                missing_env.append(WILEY_TDM_CLIENT_TOKEN_ENV_VAR)

            return ProviderFailure(
                "not_configured"
                if bootstrap.runtime is None and not self.tdm_client_token
                else "no_result",
                f"{self.name} full text could not be retrieved. "
                + " ".join(failure_parts),
                missing_env=missing_env,
                warnings=state.warnings,
                source_trail=[
                    fulltext_marker(self.name, "fail", route="html"),
                    *(
                        [fulltext_marker(self.name, "fail", route="pdf_browser")]
                        if bootstrap.runtime is not None
                        else []
                    ),
                    fulltext_marker(self.name, "fail", route="pdf_api"),
                ],
            )

        steps = []
        if bootstrap.runtime is not None:
            steps.append(
                ProviderWaterfallStep(
                    label="browser_pdf",
                    run=run_browser_pdf,
                    failure_marker=fulltext_marker(
                        self.name, "fail", route="pdf_browser"
                    ),
                    success_markers=(
                        fulltext_marker(self.name, "ok", route="pdf_browser"),
                        fulltext_marker(self.name, "ok", route="pdf_fallback"),
                    ),
                    failure_warning=browser_failure_warning,
                )
            )
        steps.append(
            ProviderWaterfallStep(
                label="pdf_api",
                run=run_tdm_api,
                failure_marker=fulltext_marker(self.name, "fail", route="pdf_api"),
                success_markers=(
                    fulltext_marker(self.name, "ok", route="pdf_api"),
                    fulltext_marker(self.name, "ok", route="pdf_fallback"),
                ),
                continue_codes=("no_result", "not_configured"),
                failure_warning=tdm_failure_warning,
                success_warning="Full text was extracted from the Wiley TDM API PDF fallback after the HTML path was not usable.",
            )
        )

        return run_provider_waterfall(
            steps,
            initial_warnings=initial_warnings,
            initial_source_trail=[fulltext_marker(self.name, "fail", route="html")],
            final_failure_factory=final_failure,
        )
