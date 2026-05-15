"""Browser workflow asset download planning and retry helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable, Mapping

from ...extraction.html.assets import (
    FIGURE_KIND,
    SUPPLEMENTARY_KIND,
    extract_scoped_html_assets,
)
from ...models import AssetProfile
from ...utils import empty_asset_results, normalize_text
from .._flaresolverr import (
    FlareSolverrFailure,
    merge_browser_context_seeds,
)
from .assets import (
    _merge_download_attempt_results,
)
from .fetchers import (
    _compact_failure_diagnostic,
    _flaresolverr_image_document_payload,
    _flaresolverr_image_payload_failure_reason,
)
from .shared import BrowserWorkflowDeps


@dataclass(frozen=True)
class BrowserAssetDownloadPlan:
    article_id: str
    output_dir: Path
    asset_profile: AssetProfile
    body_assets: list[dict[str, Any]]
    supplementary_assets: list[dict[str, Any]]


@dataclass(frozen=True)
class BrowserAssetRecoveryContext:
    runtime: Any
    provider: str
    user_agent: str
    browser_context_seed: Mapping[str, Any]
    browser_cookies: list[dict[str, Any]]
    active_seed_urls: list[str]


@dataclass
class BrowserAssetDownloadResult:
    body_results: list[dict[str, Any]]
    supplementary_results: list[dict[str, Any]]
    failures: list[dict[str, Any]]


def plan_browser_asset_download(
    *,
    article_id,
    output_dir,
    html_text,
    source_url,
    profile,
    deps: BrowserWorkflowDeps,
) -> BrowserAssetDownloadPlan:
    asset_profile = _asset_profile_from_plan_profile(profile)
    article_assets = _article_assets_from_plan_profile(
        profile,
        html_text=html_text,
        source_url=source_url,
        asset_profile=asset_profile,
        deps=deps,
    )
    body_assets, supplementary_assets = deps.split_body_and_supplementary_assets(
        article_assets
    )
    return BrowserAssetDownloadPlan(
        article_id=normalize_text(str(article_id or "")),
        output_dir=Path(output_dir),
        asset_profile=asset_profile,
        body_assets=[dict(asset) for asset in body_assets],
        supplementary_assets=[dict(asset) for asset in supplementary_assets],
    )


def run_browser_asset_download_attempt(
    plan: BrowserAssetDownloadPlan,
    recovery: BrowserAssetRecoveryContext,
    *,
    image_fetcher_factory,
    file_fetcher_factory,
    opener_requester,
    deps: BrowserWorkflowDeps,
) -> BrowserAssetDownloadResult:
    return _run_browser_asset_download_attempt(
        plan,
        recovery,
        current_seed=recovery.browser_context_seed,
        attempt_body_assets=plan.body_assets,
        attempt_supplementary_assets=plan.supplementary_assets,
        image_fetcher_factory=image_fetcher_factory,
        file_fetcher_factory=file_fetcher_factory,
        opener_requester=opener_requester,
        deps=deps,
    )


def retry_failed_browser_assets(
    plan: BrowserAssetDownloadPlan,
    previous: BrowserAssetDownloadResult,
    recovery: BrowserAssetRecoveryContext,
    *,
    image_fetcher_factory,
    file_fetcher_factory,
    opener_requester,
    deps: BrowserWorkflowDeps,
) -> BrowserAssetDownloadResult:
    failed_body_assets = deps._assets_matching_download_failures(
        plan.body_assets,
        previous.failures,
        retry_scope="body",
    )
    failed_supplementary_assets = deps._assets_matching_download_failures(
        plan.supplementary_assets,
        previous.failures,
        retry_scope="supplementary",
    )
    if not failed_body_assets and not failed_supplementary_assets:
        return previous

    refreshed_seed = deps.refresh_browser_context_seed(
        _seed_urls_for(recovery, recovery.browser_context_seed),
        publisher=recovery.provider,
        config=recovery.runtime,
        browser_context_seed=recovery.browser_context_seed,
    )
    retry_result = _run_browser_asset_download_attempt(
        plan,
        recovery,
        current_seed=refreshed_seed,
        attempt_body_assets=failed_body_assets,
        attempt_supplementary_assets=failed_supplementary_assets,
        image_fetcher_factory=image_fetcher_factory,
        file_fetcher_factory=file_fetcher_factory,
        opener_requester=opener_requester,
        deps=deps,
    )
    merged = _merge_download_attempt_results(
        _result_mapping(previous),
        _result_mapping(retry_result),
    )
    return _download_result_from_mapping(merged, deps=deps)


def _asset_profile_from_plan_profile(profile: Any) -> AssetProfile:
    value: Any
    if isinstance(profile, Mapping):
        value = profile.get("asset_profile", profile.get("profile", "all"))
    else:
        value = getattr(profile, "asset_profile", None)
        if value is None:
            value = profile
    if value not in {"none", "body", "all"}:
        return "all"
    return value


def _article_assets_from_plan_profile(
    profile: Any,
    *,
    html_text: str,
    source_url: str,
    asset_profile: AssetProfile,
    deps: BrowserWorkflowDeps,
) -> list[dict[str, Any]]:
    if isinstance(profile, Mapping):
        if "assets" in profile:
            return [dict(asset) for asset in list(profile.get("assets") or [])]
        client = profile.get("client")
        context = profile.get("context")
    else:
        assets = getattr(profile, "assets", None)
        if assets is not None:
            return [dict(asset) for asset in list(assets or [])]
        client = getattr(profile, "client", None)
        context = getattr(profile, "context", None)

    if client is not None and context is not None:
        return deps._cached_browser_workflow_assets(
            client,
            html_text,
            source_url,
            asset_profile=asset_profile,
            context=context,
        )
    return extract_scoped_html_assets(
        html_text,
        source_url,
        asset_profile=asset_profile,
    )


def _run_browser_asset_download_attempt(
    plan: BrowserAssetDownloadPlan,
    recovery: BrowserAssetRecoveryContext,
    *,
    current_seed: Mapping[str, Any],
    attempt_body_assets: list[dict[str, Any]],
    attempt_supplementary_assets: list[dict[str, Any]],
    image_fetcher_factory,
    file_fetcher_factory,
    opener_requester,
    deps: BrowserWorkflowDeps,
) -> BrowserAssetDownloadResult:
    attempt_seed = merge_browser_context_seeds(
        {"browser_cookies": recovery.browser_cookies},
        current_seed,
    )
    attempt_seed_lock = threading.Lock()
    attempt_settings = _attempt_settings(opener_requester)

    def raw_figure_page_fetcher(figure_page_url: str) -> tuple[str, str] | None:
        try:
            html_result = deps.fetch_html_with_browser(
                [figure_page_url],
                publisher=recovery.provider,
                config=recovery.runtime,
            )
        except FlareSolverrFailure:
            return None
        with attempt_seed_lock:
            attempt_seed.update(
                merge_browser_context_seeds(
                    attempt_seed, html_result.browser_context_seed
                )
            )
        return html_result.html, html_result.final_url

    figure_page_fetcher_factory = attempt_settings.get("figure_page_fetcher_factory")
    figure_page_fetcher = (
        figure_page_fetcher_factory(raw_figure_page_fetcher)
        if callable(figure_page_fetcher_factory)
        else raw_figure_page_fetcher
    )
    def seed_urls_getter() -> list[str]:
        return _seed_urls_for(recovery, attempt_seed)

    image_document_fetcher = _build_attempt_image_fetcher(
        recovery,
        attempt_seed=attempt_seed,
        attempt_seed_lock=attempt_seed_lock,
        attempt_body_assets=attempt_body_assets,
        seed_urls_getter=seed_urls_getter,
        image_fetcher_factory=image_fetcher_factory,
        deps=deps,
    )
    file_document_fetcher = _build_attempt_file_fetcher(
        recovery,
        attempt_seed=attempt_seed,
        attempt_seed_lock=attempt_seed_lock,
        attempt_supplementary_assets=attempt_supplementary_assets,
        seed_urls_getter=seed_urls_getter,
        file_fetcher_factory=file_fetcher_factory,
        deps=deps,
    )
    try:
        body_result = (
            deps.download_assets(
                FIGURE_KIND,
                attempt_settings.get("transport"),
                article_id=plan.article_id,
                assets=attempt_body_assets,
                output_dir=plan.output_dir,
                user_agent=recovery.user_agent,
                asset_profile=plan.asset_profile,
                figure_page_fetcher=figure_page_fetcher,
                candidate_builder=deps._browser_workflow_image_download_candidates,
                image_document_fetcher=image_document_fetcher,
                asset_download_concurrency=attempt_settings.get(
                    "asset_download_concurrency"
                ),
            )
            if attempt_body_assets
            else empty_asset_results()
        )
        supplementary_kwargs: dict[str, Any] = {}
        if callable(attempt_settings.get("cookie_opener_builder")):
            supplementary_kwargs["cookie_opener_builder"] = attempt_settings[
                "cookie_opener_builder"
            ]
        if callable(attempt_settings.get("opener_requester")):
            supplementary_kwargs["opener_requester"] = attempt_settings[
                "opener_requester"
            ]
        supplementary_result = (
            deps.download_assets(
                SUPPLEMENTARY_KIND,
                attempt_settings.get("transport"),
                article_id=plan.article_id,
                assets=attempt_supplementary_assets,
                output_dir=plan.output_dir,
                user_agent=recovery.user_agent,
                asset_profile=plan.asset_profile,
                browser_context_seed=attempt_seed,
                seed_urls=_seed_urls_for(recovery, attempt_seed),
                file_document_fetcher=file_document_fetcher,
                asset_download_concurrency=attempt_settings.get(
                    "asset_download_concurrency"
                ),
                **supplementary_kwargs,
            )
            if attempt_supplementary_assets
            else empty_asset_results()
        )
        return BrowserAssetDownloadResult(
            body_results=[dict(asset) for asset in list(body_result.get("assets") or [])],
            supplementary_results=[
                dict(asset) for asset in list(supplementary_result.get("assets") or [])
            ],
            failures=[
                *[dict(failure) for failure in list(body_result.get("asset_failures") or [])],
                *[
                    dict(failure)
                    for failure in list(
                        supplementary_result.get("asset_failures") or []
                    )
                ],
            ],
        )
    finally:
        for fetcher in (image_document_fetcher, file_document_fetcher):
            close_fetcher = getattr(fetcher, "close", None)
            if callable(close_fetcher):
                close_fetcher()


def _build_attempt_image_fetcher(
    recovery: BrowserAssetRecoveryContext,
    *,
    attempt_seed: dict[str, Any],
    attempt_seed_lock: threading.Lock,
    attempt_body_assets: list[dict[str, Any]],
    seed_urls_getter: Callable[[], list[str]],
    image_fetcher_factory,
    deps: BrowserWorkflowDeps,
) -> Callable[[str, Mapping[str, Any]], dict[str, Any] | None] | None:
    if not attempt_body_assets or not callable(image_fetcher_factory):
        return None
    return image_fetcher_factory(
        attempt_body_assets=attempt_body_assets,
        browser_context_seed_getter=lambda: attempt_seed,
        seed_urls_getter=seed_urls_getter,
        browser_user_agent=attempt_seed.get("browser_user_agent")
        or recovery.user_agent,
        headless=getattr(recovery.runtime, "headless", True),
        challenge_recovery=_asset_challenge_recovery_for(
            recovery,
            attempt_seed=attempt_seed,
            attempt_seed_lock=attempt_seed_lock,
            deps=deps,
        ),
    )


def _build_attempt_file_fetcher(
    recovery: BrowserAssetRecoveryContext,
    *,
    attempt_seed: dict[str, Any],
    attempt_seed_lock: threading.Lock,
    attempt_supplementary_assets: list[dict[str, Any]],
    seed_urls_getter: Callable[[], list[str]],
    file_fetcher_factory,
    deps: BrowserWorkflowDeps,
) -> Callable[[str, Mapping[str, Any]], dict[str, Any] | None] | None:
    if not attempt_supplementary_assets or not callable(file_fetcher_factory):
        return None
    return file_fetcher_factory(
        attempt_supplementary_assets=attempt_supplementary_assets,
        browser_context_seed_getter=lambda: attempt_seed,
        seed_urls_getter=seed_urls_getter,
        browser_user_agent=attempt_seed.get("browser_user_agent")
        or recovery.user_agent,
        headless=getattr(recovery.runtime, "headless", True),
        challenge_recovery=_supplementary_challenge_recovery_for(
            recovery,
            attempt_seed=attempt_seed,
            attempt_seed_lock=attempt_seed_lock,
            deps=deps,
        ),
    )


def _asset_challenge_recovery_for(
    recovery: BrowserAssetRecoveryContext,
    *,
    attempt_seed: dict[str, Any],
    attempt_seed_lock: threading.Lock,
    deps: BrowserWorkflowDeps,
) -> Callable[[str, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any] | None]:
    def recover(
        image_url: str, asset: Mapping[str, Any], failure: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        attempts: list[dict[str, Any]] = []
        for recovery_url in _asset_recovery_urls(image_url, asset):
            try:
                html_result = deps.fetch_html_with_browser(
                    [recovery_url],
                    publisher=recovery.provider,
                    config=recovery.runtime,
                    return_image_payload=True,
                )
            except FlareSolverrFailure as exc:
                if exc.browser_context_seed:
                    with attempt_seed_lock:
                        attempt_seed.update(
                            merge_browser_context_seeds(
                                attempt_seed, exc.browser_context_seed
                            )
                        )
                attempts.append(
                    _compact_failure_diagnostic(
                        {
                            "url": recovery_url,
                            "status": "failed",
                            "reason": "challenge_recovery_failed",
                            "message": exc.message,
                        }
                    )
                )
                continue
            with attempt_seed_lock:
                attempt_seed.update(
                    merge_browser_context_seeds(
                        attempt_seed, html_result.browser_context_seed
                    )
                )
            image_payload = _flaresolverr_image_document_payload(html_result)
            recovery_reason = (
                ""
                if image_payload is not None
                else _flaresolverr_image_payload_failure_reason(html_result)
            )
            return _compact_failure_diagnostic(
                {
                    "status": "ok" if image_payload is not None else "failed",
                    "url": recovery_url,
                    "final_url": html_result.final_url,
                    "response_status": html_result.response_status,
                    "content_type": html_result.response_headers.get("content-type"),
                    "title_snippet": (html_result.title or "")[:160],
                    "attempts": attempts,
                    "reason": recovery_reason,
                    "image_payload": image_payload,
                }
            )
        return _compact_failure_diagnostic(
            {
                "status": "failed",
                "reason": normalize_text(str(failure.get("reason") or ""))
                or "challenge_recovery_failed",
                "attempts": attempts,
            }
        )

    return recover


def _supplementary_challenge_recovery_for(
    recovery: BrowserAssetRecoveryContext,
    *,
    attempt_seed: dict[str, Any],
    attempt_seed_lock: threading.Lock,
    deps: BrowserWorkflowDeps,
) -> Callable[[str, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any] | None]:
    def recover(
        file_url: str, asset: Mapping[str, Any], failure: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        attempts: list[dict[str, Any]] = []
        for recovery_url in _supplementary_recovery_urls(recovery, file_url, asset):
            try:
                html_result = deps.fetch_html_with_browser(
                    [recovery_url],
                    publisher=recovery.provider,
                    config=recovery.runtime,
                )
            except FlareSolverrFailure as exc:
                if exc.browser_context_seed:
                    with attempt_seed_lock:
                        attempt_seed.update(
                            merge_browser_context_seeds(
                                attempt_seed, exc.browser_context_seed
                            )
                        )
                attempts.append(
                    _compact_failure_diagnostic(
                        {
                            "url": recovery_url,
                            "status": "failed",
                            "reason": "challenge_recovery_failed",
                            "message": exc.message,
                        }
                    )
                )
                continue
            with attempt_seed_lock:
                attempt_seed.update(
                    merge_browser_context_seeds(
                        attempt_seed, html_result.browser_context_seed
                    )
                )
            return _compact_failure_diagnostic(
                {
                    "status": "ok",
                    "url": recovery_url,
                    "final_url": html_result.final_url,
                    "response_status": html_result.response_status,
                    "content_type": html_result.response_headers.get("content-type"),
                    "title_snippet": (html_result.title or "")[:160],
                    "attempts": attempts,
                }
            )
        return _compact_failure_diagnostic(
            {
                "status": "failed",
                "reason": normalize_text(str(failure.get("reason") or ""))
                or "challenge_recovery_failed",
                "attempts": attempts,
            }
        )

    return recover


def _asset_recovery_urls(image_url: str, asset: Mapping[str, Any]) -> list[str]:
    return _dedupe_urls(
        [
            image_url,
            normalize_text(str(asset.get("figure_page_url") or "")),
        ]
    )


def _supplementary_recovery_urls(
    recovery: BrowserAssetRecoveryContext,
    file_url: str,
    asset: Mapping[str, Any],
) -> list[str]:
    return _dedupe_urls(
        [
            file_url,
            *recovery.active_seed_urls,
            normalize_text(str(asset.get("source_url") or "")),
            normalize_text(str(asset.get("download_url") or "")),
        ]
    )


def _seed_urls_for(
    recovery: BrowserAssetRecoveryContext,
    current_seed: Mapping[str, Any],
) -> list[str]:
    return _dedupe_urls(
        [
            *recovery.active_seed_urls,
            normalize_text(str(current_seed.get("browser_final_url") or "")),
        ]
    )


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in urls:
        normalized = normalize_text(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _attempt_settings(opener_requester: Any) -> dict[str, Any]:
    if isinstance(opener_requester, Mapping):
        return dict(opener_requester)
    settings: dict[str, Any] = {}
    if callable(opener_requester):
        settings["opener_requester"] = opener_requester
    for name in (
        "transport",
        "asset_download_concurrency",
        "figure_page_fetcher_factory",
        "cookie_opener_builder",
    ):
        value = getattr(opener_requester, name, None)
        if value is not None:
            settings[name] = value
    return settings


def _result_mapping(result: BrowserAssetDownloadResult) -> dict[str, list[dict[str, Any]]]:
    return {
        "assets": [
            *[dict(asset) for asset in result.body_results],
            *[dict(asset) for asset in result.supplementary_results],
        ],
        "asset_failures": [dict(failure) for failure in result.failures],
    }


def _download_result_from_mapping(
    result: Mapping[str, Any],
    *,
    deps: BrowserWorkflowDeps,
) -> BrowserAssetDownloadResult:
    body_results, supplementary_results = deps.split_body_and_supplementary_assets(
        [dict(asset) for asset in list(result.get("assets") or [])]
    )
    return BrowserAssetDownloadResult(
        body_results=body_results,
        supplementary_results=supplementary_results,
        failures=[dict(failure) for failure in list(result.get("asset_failures") or [])],
    )
