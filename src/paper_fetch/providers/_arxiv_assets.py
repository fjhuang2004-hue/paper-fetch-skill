"""arXiv HTML asset extraction and download retry helpers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
import re
import urllib.parse

from ..common_patterns import EXTENDED_DATA_FIGURE_LABEL
from ..config import resolve_asset_download_concurrency
from ..extraction.html import assets as html_assets
from ..utils import normalize_text
from ._arxiv_html import Tag
from ._arxiv_references import _is_arxiv_inline_figure_container
from ._html_asset_engine import merge_assets_by_identity
from ._html_section_markdown import INLINE_FIGURE_ALT_ATTR, INLINE_FIGURE_SRC_ATTR
from ._retry_categories import (
    DEFAULT_RETRYABLE_ASSET_ERROR_CATEGORIES,
    NETWORK_RETRYABLE_REASON_TOKENS,
)

ARXIV_ASSET_DOWNLOAD_CONCURRENCY_LIMIT = 2
ARXIV_IMAGE_ACCEPT = "image/avif,image/webp,image/*,*/*;q=0.8"
_ARXIV_FIGURE_CAPTION_LABEL_PATTERN = re.compile(
    rf"^(?P<label>(?:Figure|Fig\.?|{re.escape(EXTENDED_DATA_FIGURE_LABEL)}\.?)\s+\d+[A-Za-z]?)[.:]?\s*(?P<caption>.*)$",
    flags=re.IGNORECASE,
)
_ARXIV_FIGURE_ID_PATTERN = re.compile(
    r"(?:^|[.])F(?P<number>\d+[A-Za-z]?(?:\.\d+[A-Za-z]?)?)(?=$|[.])",
    flags=re.IGNORECASE,
)
_ARXIV_RETRYABLE_ASSET_ERROR_CATEGORIES = DEFAULT_RETRYABLE_ASSET_ERROR_CATEGORIES

def _arxiv_asset_download_concurrency(env: Mapping[str, str] | None) -> int:
    return min(
        resolve_asset_download_concurrency(env), ARXIV_ASSET_DOWNLOAD_CONCURRENCY_LIMIT
    )


def _asset_candidate_urls(asset: Mapping[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            normalize_text(str(asset.get(field) or ""))
            for field in (
                "url",
                "full_size_url",
                "preview_url",
                "download_url",
                "original_url",
                "link",
            )
        )
        if normalized
    }


def _is_retryable_arxiv_asset_failure(failure: Mapping[str, Any]) -> bool:
    if failure.get("status") is not None:
        return False
    error_category = normalize_text(str(failure.get("error_category") or "")).lower()
    if error_category:
        return error_category in _ARXIV_RETRYABLE_ASSET_ERROR_CATEGORIES
    reason = normalize_text(str(failure.get("reason") or "")).lower()
    if not reason or "unsupported asset url scheme" in reason:
        return False
    return any(token in reason for token in NETWORK_RETRYABLE_REASON_TOKENS)


def _asset_matches_failure(
    asset: Mapping[str, Any], failure: Mapping[str, Any]
) -> bool:
    failure_url = normalize_text(
        str(failure.get("source_url") or failure.get("url") or "")
    )
    if failure_url and failure_url in _asset_candidate_urls(asset):
        return True
    failure_heading = normalize_text(str(failure.get("heading") or ""))
    asset_heading = normalize_text(str(asset.get("heading") or ""))
    failure_caption = normalize_text(str(failure.get("caption") or ""))
    asset_caption = normalize_text(str(asset.get("caption") or ""))
    return bool(
        failure_heading
        and failure_heading == asset_heading
        and failure_caption == asset_caption
    )


def _assets_for_arxiv_network_retry(
    assets: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    retry_assets: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str, str]] = set()
    retry_failures = [
        failure for failure in failures if _is_retryable_arxiv_asset_failure(failure)
    ]
    for failure in retry_failures:
        for asset in assets:
            if not _asset_matches_failure(asset, failure):
                continue
            identity = (
                tuple(sorted(_asset_candidate_urls(asset))),
                normalize_text(str(asset.get("heading") or "")),
                normalize_text(str(asset.get("caption") or "")),
            )
            if identity not in seen:
                seen.add(identity)
                retry_assets.append(dict(asset))
            break
    return retry_assets


def _merge_arxiv_asset_download_results(
    initial_result: Mapping[str, list[dict[str, Any]]],
    retry_result: Mapping[str, list[dict[str, Any]]],
    *,
    retried_assets: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    initial_assets = [dict(item) for item in (initial_result.get("assets") or [])]
    retry_assets = [dict(item) for item in (retry_result.get("assets") or [])]
    retry_failures = [dict(item) for item in (retry_result.get("asset_failures") or [])]
    retained_initial_failures: list[dict[str, Any]] = []
    for failure in initial_result.get("asset_failures") or []:
        if _is_retryable_arxiv_asset_failure(failure) and any(
            _asset_matches_failure(asset, failure) for asset in retried_assets
        ):
            continue
        retained_initial_failures.append(dict(failure))
    return {
        "assets": [*initial_assets, *retry_assets],
        "asset_failures": [*retained_initial_failures, *retry_failures],
    }

def _asset_has_download_candidate(asset: Mapping[str, Any]) -> bool:
    return bool(
        normalize_text(
            str(
                asset.get("url")
                or asset.get("full_size_url")
                or asset.get("preview_url")
                or asset.get("download_url")
                or asset.get("original_url")
                or asset.get("link")
                or ""
            )
        )
    )


def _extract_arxiv_html_assets(
    article_html: str, source_url: str
) -> list[dict[str, Any]]:
    assets = [
        _postprocess_arxiv_html_asset(item)
        for item in html_assets.extract_figure_assets(article_html, source_url)
        if normalize_text(str(item.get("kind") or "")).lower() == "figure"
        and _asset_has_download_candidate(item)
    ]
    return [dict(item) for item in merge_assets_by_identity(assets)]


def _arxiv_figure_label_from_text(text: str) -> str:
    normalized = normalize_text(str(text or "").replace("\n", " "))
    match = _ARXIV_FIGURE_CAPTION_LABEL_PATTERN.match(normalized)
    if match is None:
        return ""
    raw_label = normalize_text(match.group("label"))
    number_match = re.search(r"(\d+[A-Za-z]?)$", raw_label)
    if number_match is None:
        return raw_label.rstrip(".:")
    if raw_label.lower().startswith(EXTENDED_DATA_FIGURE_LABEL.lower()):
        return f"{EXTENDED_DATA_FIGURE_LABEL}. {number_match.group(1)}"
    return f"Figure {number_match.group(1)}"


def _arxiv_figure_label_from_dom_id(dom_id: Any) -> str:
    normalized = normalize_text(str(dom_id or ""))
    match = _ARXIV_FIGURE_ID_PATTERN.search(normalized)
    if match is None:
        return ""
    return f"Figure {match.group('number')}"


def _clean_arxiv_asset_caption(text: Any) -> str:
    return html_assets.clean_noisy_image_alt_text(str(text or "").replace("\n", " "))


def _postprocess_arxiv_html_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(asset)
    caption = _clean_arxiv_asset_caption(result.get("caption"))
    heading = _clean_arxiv_asset_caption(result.get("heading")) or "Figure"
    short_heading = _arxiv_figure_label_from_text(
        caption
    ) or _arxiv_figure_label_from_text(heading)
    if not short_heading:
        short_heading = _arxiv_figure_label_from_dom_id(
            result.get("dom_id")
        ) or _arxiv_figure_label_from_dom_id(result.get("image_id"))
    result["heading"] = short_heading or heading
    result["caption"] = caption
    return result

def _arxiv_parent_figures(node: Any, article: Any) -> list[Any]:
    figures: list[Any] = []
    current = getattr(node, "parent", None)
    while Tag is not None and isinstance(current, Tag) and current is not article:
        if current.name == "figure":
            figures.append(current)
        current = getattr(current, "parent", None)
    return figures


def _arxiv_inline_figure_for_image(image: Any, article: Any) -> Any:
    if Tag is None or not isinstance(image, Tag):
        return None
    figures = _arxiv_parent_figures(image, article)
    if not figures:
        return None
    if any(not _is_arxiv_inline_figure_container(figure) for figure in figures):
        return None
    return figures[0]


def _arxiv_srcset_url_candidates(raw_value: Any) -> list[str]:
    raw = normalize_text(str(raw_value or ""))
    if not raw:
        return []
    candidates: list[str] = []
    for item in raw.split(","):
        candidate = normalize_text(item).split(" ", 1)[0]
        if candidate:
            candidates.append(candidate)
    return candidates


def _arxiv_url_reference_candidates(raw_value: Any, source_url: str = "") -> set[str]:
    raw = normalize_text(str(raw_value or "")).strip("<>").replace("\\", "/")
    if not raw:
        return set()
    values = [raw]
    if source_url:
        values.append(urllib.parse.urljoin(source_url, raw))
    candidates: set[str] = set()
    for value in values:
        normalized = normalize_text(value).strip("<>").replace("\\", "/")
        if not normalized:
            continue
        parsed = urllib.parse.urlsplit(normalized)
        path = parsed.path or normalized
        for candidate in (
            normalized,
            urllib.parse.unquote(normalized),
            path,
            urllib.parse.unquote(path),
        ):
            cleaned = normalize_text(candidate).replace("\\", "/").strip()
            if not cleaned:
                continue
            candidates.add(cleaned)
            candidates.add(cleaned.lstrip("/"))
            basename = cleaned.rstrip("/").rsplit("/", 1)[-1]
            if basename:
                candidates.add(basename)
    return candidates


def _arxiv_url_candidate_sets_match(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    for left_item in left:
        for right_item in right:
            if left_item.endswith(f"/{right_item}") or right_item.endswith(
                f"/{left_item}"
            ):
                return True
    return False


def _arxiv_image_url_candidates(image: Any, source_url: str) -> set[str]:
    if Tag is None or not isinstance(image, Tag):
        return set()
    candidates: set[str] = set()
    for attr in ("src", "data-src", "data-lazy-src"):
        candidates |= _arxiv_url_reference_candidates(image.get(attr), source_url)
    for attr in ("srcset", "data-srcset"):
        for srcset_url in _arxiv_srcset_url_candidates(image.get(attr)):
            candidates |= _arxiv_url_reference_candidates(srcset_url, source_url)

    picture = image.find_parent("picture")
    if isinstance(picture, Tag):
        for source in picture.find_all("source"):
            if not isinstance(source, Tag):
                continue
            for attr in ("src", "data-src"):
                candidates |= _arxiv_url_reference_candidates(
                    source.get(attr), source_url
                )
            for attr in ("srcset", "data-srcset"):
                for srcset_url in _arxiv_srcset_url_candidates(source.get(attr)):
                    candidates |= _arxiv_url_reference_candidates(
                        srcset_url, source_url
                    )

    anchor = image.find_parent("a", href=True)
    if isinstance(anchor, Tag):
        candidates |= _arxiv_url_reference_candidates(anchor.get("href"), source_url)
    return candidates


def _arxiv_inline_asset_url(asset: Mapping[str, Any]) -> str:
    for field in (
        "url",
        "full_size_url",
        "preview_url",
        "download_url",
        "original_url",
        "link",
    ):
        candidate = normalize_text(str(asset.get(field) or ""))
        if candidate:
            return candidate
    return ""


def _arxiv_inline_asset_alt(asset: Mapping[str, Any]) -> str:
    return (
        normalize_text(str(asset.get("heading") or ""))
        or _arxiv_figure_label_from_dom_id(asset.get("image_id"))
        or _arxiv_figure_label_from_dom_id(asset.get("dom_id"))
        or "Figure"
    )


def _arxiv_asset_order(asset: Mapping[str, Any]) -> int | None:
    raw_value = normalize_text(str(asset.get("asset_order") or ""))
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return value if value >= 0 else None


def _arxiv_inline_images_for_figure(figure: Any, article: Any) -> list[Any]:
    if Tag is None or not isinstance(figure, Tag):
        return []
    images: list[Any] = []
    for image in figure.find_all("img"):
        if not isinstance(image, Tag):
            continue
        if _arxiv_inline_figure_for_image(image, article) is None:
            continue
        if figure not in _arxiv_parent_figures(image, article):
            continue
        images.append(image)
    return images


def _annotate_arxiv_inline_figure_images(
    article: Any,
    extracted_assets: Sequence[Mapping[str, Any]],
    source_url: str,
) -> dict[str, int]:
    if Tag is None or not isinstance(article, Tag):
        return {
            "inline_figure_image_count": 0,
            "inline_figure_asset_match_count": 0,
            "inline_figure_asset_miss_count": len(extracted_assets),
        }

    figure_by_id: dict[str, Any] = {}
    for figure in article.find_all("figure"):
        if not _is_arxiv_inline_figure_container(figure):
            continue
        figure_id = normalize_text(str(figure.get("id") or ""))
        if figure_id and figure_id not in figure_by_id:
            figure_by_id[figure_id] = figure

    eligible_images: list[Any] = []
    image_by_id: dict[str, Any] = {}
    image_url_candidates: dict[int, set[str]] = {}
    for image in article.find_all("img"):
        if (
            not isinstance(image, Tag)
            or _arxiv_inline_figure_for_image(image, article) is None
        ):
            continue
        eligible_images.append(image)
        image_id = normalize_text(str(image.get("id") or ""))
        if image_id and image_id not in image_by_id:
            image_by_id[image_id] = image
        image_url_candidates[id(image)] = _arxiv_image_url_candidates(image, source_url)

    consumed_image_ids: set[int] = set()
    match_count = 0
    miss_count = 0

    for asset in extracted_assets:
        inline_url = _arxiv_inline_asset_url(asset)
        if not inline_url:
            miss_count += 1
            continue

        matched_image = None
        image_id = normalize_text(str(asset.get("image_id") or ""))
        if image_id:
            candidate = image_by_id.get(image_id)
            if candidate is not None and id(candidate) not in consumed_image_ids:
                matched_image = candidate

        if matched_image is None:
            dom_id = normalize_text(str(asset.get("dom_id") or ""))
            order = _arxiv_asset_order(asset)
            figure = figure_by_id.get(dom_id) if dom_id else None
            figure_images = (
                _arxiv_inline_images_for_figure(figure, article)
                if figure is not None
                else []
            )
            if order is not None and order < len(figure_images):
                candidate = figure_images[order]
                if id(candidate) not in consumed_image_ids:
                    matched_image = candidate

        if matched_image is None:
            asset_candidates = set()
            for candidate_url in _asset_candidate_urls(asset):
                asset_candidates |= _arxiv_url_reference_candidates(
                    candidate_url, source_url
                )
            for image in eligible_images:
                if id(image) in consumed_image_ids:
                    continue
                if _arxiv_url_candidate_sets_match(
                    asset_candidates,
                    image_url_candidates.get(id(image), set()),
                ):
                    matched_image = image
                    break

        if matched_image is None:
            miss_count += 1
            continue

        matched_image[INLINE_FIGURE_SRC_ATTR] = inline_url
        matched_image[INLINE_FIGURE_ALT_ATTR] = _arxiv_inline_asset_alt(asset)
        consumed_image_ids.add(id(matched_image))
        match_count += 1

    return {
        "inline_figure_image_count": match_count,
        "inline_figure_asset_match_count": match_count,
        "inline_figure_asset_miss_count": miss_count,
    }
