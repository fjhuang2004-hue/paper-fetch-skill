#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SRC_PATH = REPO_ROOT / "src"
if SRC_PATH.is_dir() and str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from _structured_errors import ToolError, emit_error, error_payload  # noqa: E402
from paper_fetch.extraction.html.signals import CHALLENGE_PATTERNS, contains_access_gate_text, summarize_html  # noqa: E402
from paper_fetch.http import (  # noqa: E402
    HttpTransport,
    RequestFailure,
    build_network_error_detail,
    classify_network_error,
)  # noqa: E402
from paper_fetch.publisher_identity import infer_provider_from_doi, normalize_doi  # noqa: E402


GOLDEN_PURPOSES = {
    "structure",
    "table",
    "formula",
    "figure",
    "supplementary",
    "references",
    "pdf_fallback",
}
BLOCK_PURPOSES = {"abstract_only", "access_gate", "empty_shell"}
PURPOSES = sorted(GOLDEN_PURPOSES | BLOCK_PURPOSES)
PURPOSE_ALIASES = {
    "abstract-only": "abstract_only",
    "access-gate": "access_gate",
    "empty-shell": "empty_shell",
    "pdf-fallback": "pdf_fallback",
}
CLI_PURPOSES = sorted(set(PURPOSES) | set(PURPOSE_ALIASES))
RETRY_VIA_ERROR_CODES = {"HTTP_FORBIDDEN", "HTTP_RATE_LIMITED", "CHALLENGE_DETECTED"}


class CaptureArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit_error(
            error_payload(
                "UNSUITABLE_DOI_SAMPLE",
                message,
                provider=None,
                manifest=None,
                task_id="capture-fixtures-parse-args",
                retryable=False,
                details={"reason": message},
            )
        )
        raise SystemExit(2)


class ManifestContext:
    def __init__(
        self,
        *,
        path: Path,
        data: dict[str, Any],
        provider: str | None,
        routing: dict[str, Any],
        sample: dict[str, Any] | None,
    ) -> None:
        self.path = path
        self.data = data
        self.provider = provider
        self.routing = routing
        self.sample = sample


class CaptureFixtureError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        route: str | None = None,
        previous_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.route = route
        self.previous_code = previous_code

    def to_payload(
        self,
        *,
        provider: str | None = None,
        manifest: str | None = None,
        purpose: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        if provider:
            extras["provider"] = provider
        if manifest:
            extras["manifest"] = manifest
        if purpose:
            details["purpose"] = purpose
            extras["purpose"] = purpose
        if self.status_code is not None:
            details["status_code"] = self.status_code
            extras["status_code"] = self.status_code
        if self.route:
            details["route"] = self.route
            extras["route"] = self.route
        if self.previous_code:
            details["previous_code"] = self.previous_code
            extras["previous_code"] = self.previous_code
        return error_payload(
            self.code,
            self.message,
            provider=provider,
            manifest=manifest,
            task_id=task_id,
            retryable=self.retryable,
            details=details,
            extras=extras,
        )


def _repo_root() -> Path:
    return REPO_ROOT


def doi_slug(doi: str) -> str:
    return normalize_doi(doi).replace("/", "_")


def normalize_purpose(value: str) -> str:
    return PURPOSE_ALIASES.get(value, value)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"samples": {}}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest root must be an object: {path}")
    samples = manifest.setdefault("samples", {})
    if not isinstance(samples, dict):
        raise ValueError(f"manifest samples must be an object: {path}")
    return manifest


def _load_provider_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ToolError(
            "MANIFEST_NOT_FOUND",
            "Provider manifest was not found.",
            retryable=False,
            manifest=path.as_posix(),
            task_id="capture-fixtures-validate-manifest",
            details={"path": path.as_posix()},
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ToolError(
            "MANIFEST_SCHEMA_INVALID",
            "Manifest YAML is invalid.",
            retryable=False,
            manifest=path.as_posix(),
            task_id="capture-fixtures-validate-manifest",
            details={"reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise ToolError(
            "MANIFEST_SCHEMA_INVALID",
            "Manifest root must be an object.",
            retryable=False,
            manifest=path.as_posix(),
            task_id="capture-fixtures-validate-manifest",
            details={"path": path.as_posix()},
        )
    return data


def _manifest_context(path_value: str | None, purpose: str | None) -> ManifestContext | None:
    if not path_value:
        return None
    path = Path(path_value)
    data = _load_provider_manifest(path)
    fixtures = data.get("fixtures") if isinstance(data.get("fixtures"), dict) else {}
    doi_samples = fixtures.get("doi_samples") if isinstance(fixtures.get("doi_samples"), dict) else {}
    sample = doi_samples.get(purpose) if purpose else None
    if purpose and sample is not None and not isinstance(sample, dict):
        raise CaptureFixtureError(
            "UNSUITABLE_DOI_SAMPLE",
            f"fixtures.doi_samples.{purpose} must be an object",
            retryable=False,
        )
    routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
    provider = data.get("name")
    return ManifestContext(
        path=path,
        data=data,
        provider=str(provider) if provider else None,
        routing=dict(routing),
        sample=sample if isinstance(sample, dict) else None,
    )


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _content_type(response: dict[str, Any]) -> str:
    headers = response.get("headers") if isinstance(response.get("headers"), dict) else {}
    return str(headers.get("content-type") or headers.get("Content-Type") or "text/html")


def _body_bytes(response: dict[str, Any]) -> bytes:
    body = response.get("body", b"")
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    raise TypeError("HttpTransport response body must be bytes or str")


def _extension_for(content_type: str, purpose: str) -> str:
    normalized = content_type.lower()
    if purpose == "pdf_fallback" or "application/pdf" in normalized:
        return "pdf"
    if "xml" in normalized:
        return "xml"
    return "html"


def _fixture_family(purpose: str) -> str:
    return "block" if purpose in BLOCK_PURPOSES else "golden"


def _fixture_path(root: Path, slug: str, purpose: str, content_type: str) -> Path:
    family = _fixture_family(purpose)
    if family == "block":
        return root / "tests" / "fixtures" / "block" / slug / "original.html"
    filename = f"original.{_extension_for(content_type, purpose)}"
    return root / "tests" / "fixtures" / "golden_criteria" / slug / filename


def _manifest_entry(
    *,
    doi: str,
    provider: str,
    source_url: str,
    fetched_at: str,
    purpose: str,
    fixture_path: Path,
    root: Path,
    content_type: str,
) -> dict[str, Any]:
    family = _fixture_family(purpose)
    route_kind = "pdf_fallback" if purpose == "pdf_fallback" else ("block" if family == "block" else _extension_for(content_type, purpose))
    asset_name = fixture_path.name
    return {
        "doi": doi,
        "publisher": provider,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "purpose": purpose,
        "expected_outcome": "pending",
        "fixture_family": family,
        "content_type": content_type,
        "route_kind": route_kind,
        "origin_kind": "real_replay",
        "usage_kind": "content",
        "assets": {
            asset_name: fixture_path.relative_to(root).as_posix(),
        },
    }


def _capture_http(doi: str) -> dict[str, Any]:
    url = f"https://doi.org/{doi}"
    return HttpTransport().request(
        "GET",
        url,
        headers={"Accept": "text/html,application/xhtml+xml,application/xml,application/pdf;q=0.9,*/*;q=0.8"},
        retry_on_transient=True,
    )


def _is_pdf_response(content_type: str, body: bytes) -> bool:
    return "application/pdf" in content_type.lower() or body.lstrip().startswith(b"%PDF")


def _is_html_response(content_type: str, body: bytes) -> bool:
    return (
        "html" in content_type.lower()
        or body.lstrip().lower().startswith(b"<!doctype html")
        or b"<html" in body[:512].lower()
    )


def _decode_body(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _contains_challenge(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in CHALLENGE_PATTERNS) or "captcha" in lowered


def _looks_empty_article_shell(content_type: str, body: bytes) -> bool:
    if not _is_html_response(content_type, body):
        return False
    if not body.strip():
        return True
    html = _decode_body(body)
    text = summarize_html(html, limit=200).strip()
    return not text and "<html" in html.lower()


def _validate_capture_response(
    *,
    response: dict[str, Any],
    purpose: str,
    route: str,
) -> tuple[str, bytes, str]:
    content_type = _content_type(response)
    body = _body_bytes(response)
    final_url = str(response.get("url") or "")
    status_code = int(response.get("status_code") or 200)
    body_text = _decode_body(body) if _is_html_response(content_type, body) else ""

    if status_code == 403:
        raise CaptureFixtureError(
            "HTTP_FORBIDDEN",
            "publisher returned HTTP 403 while capturing fixture",
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if status_code == 429:
        raise CaptureFixtureError(
            "HTTP_RATE_LIMITED",
            "publisher returned HTTP 429 while capturing fixture",
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if status_code >= 500:
        raise CaptureFixtureError(
            "NETWORK_TRANSIENT",
            f"publisher returned transient HTTP {status_code} while capturing fixture",
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if _is_html_response(content_type, body) and _contains_challenge(body_text):
        raise CaptureFixtureError(
            "CHALLENGE_DETECTED",
            "publisher returned a challenge or CAPTCHA page while capturing fixture",
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if purpose == "pdf_fallback" and not _is_pdf_response(content_type, body):
        raise CaptureFixtureError(
            "NON_PDF_FALLBACK_CONTENT",
            "pdf_fallback sample did not return PDF content",
            retryable=False,
            status_code=status_code,
            route=route,
        )
    if purpose != "access_gate" and _is_html_response(content_type, body) and contains_access_gate_text(body_text):
        raise CaptureFixtureError(
            "ACCESS_GATE_CAPTURED",
            "captured HTML is an access gate instead of the requested fixture purpose",
            retryable=False,
            status_code=status_code,
            route=route,
        )
    if purpose != "empty_shell" and _looks_empty_article_shell(content_type, body):
        raise CaptureFixtureError(
            "EMPTY_ARTICLE_SHELL",
            "captured HTML has no article text",
            retryable=False,
            status_code=status_code,
            route=route,
        )
    return content_type, body, final_url


def _map_request_failure(exc: RequestFailure, *, route: str) -> CaptureFixtureError:
    status_code = exc.status_code
    if status_code == 403:
        return CaptureFixtureError(
            "HTTP_FORBIDDEN",
            str(exc),
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if status_code == 429:
        return CaptureFixtureError(
            "HTTP_RATE_LIMITED",
            str(exc),
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if status_code is not None and status_code >= 500:
        return CaptureFixtureError(
            "NETWORK_TRANSIENT",
            str(exc),
            retryable=True,
            status_code=status_code,
            route=route,
        )
    if exc.body:
        content_type = _content_type({"headers": exc.headers})
        body_text = _decode_body(exc.body) if _is_html_response(content_type, exc.body) else ""
        if _contains_challenge(body_text):
            return CaptureFixtureError(
                "CHALLENGE_DETECTED",
                str(exc),
                retryable=True,
                status_code=status_code,
                route=route,
            )
    return CaptureFixtureError(
        "NETWORK_TRANSIENT",
        str(exc),
        retryable=True,
        status_code=status_code,
        route=route,
    )


def _capture_route(doi: str, *, route: str) -> dict[str, Any]:
    if route == "http":
        try:
            return _capture_http(doi)
        except RequestFailure as exc:
            raise _map_request_failure(exc, route=route) from exc
        except Exception as exc:
            category = classify_network_error(exc)
            detail = build_network_error_detail(exc)
            message = f"network transient during fixture capture: {category.value}"
            if detail:
                message = f"{message}: {detail}"
            raise CaptureFixtureError(
                "NETWORK_TRANSIENT",
                message,
                retryable=True,
                route=route,
            ) from exc
    if route in {"playwright", "browser"}:
        raise CaptureFixtureError(
            "BROWSER_RUNTIME_REQUIRED",
            "browser fixture capture requires an interactive browser runtime and is not implemented in this script yet",
            retryable=False,
            route=route,
        )
    raise CaptureFixtureError(
        "UNSUITABLE_DOI_SAMPLE",
        f"unsupported fixture capture route: {route}",
        retryable=False,
        route=route,
    )


def _should_retry_via(error: CaptureFixtureError, *, retry_via: str | None, manifest: ManifestContext | None) -> bool:
    if retry_via != "browser":
        return False
    return error.code in RETRY_VIA_ERROR_CODES


def _manifest_evidence(sample: dict[str, Any] | None) -> dict[str, Any]:
    if not sample:
        return {}
    return {
        "evidence_url": sample.get("evidence_url"),
        "evidence_reason": sample.get("evidence_reason"),
        "observed_signals": sample.get("observed_signals", []),
        "confidence": sample.get("confidence"),
    }


def capture_fixture(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_dir).resolve()
    purpose = normalize_purpose(args.purpose)
    manifest = _manifest_context(getattr(args, "from_manifest", None), purpose)
    if manifest and manifest.sample is None:
        raise CaptureFixtureError(
            "UNSUITABLE_DOI_SAMPLE",
            f"manifest does not define fixtures.doi_samples.{purpose}",
            retryable=False,
        )
    sample_doi = manifest.sample.get("doi") if manifest and manifest.sample else None
    raw_doi = args.doi or sample_doi
    provider = args.provider or (manifest.provider if manifest else None)
    if raw_doi is None:
        return {
            "status": "SKIPPED",
            "skipped": True,
            "purpose": purpose,
            "provider": provider,
            "manifest": manifest.path.as_posix() if manifest else None,
            "reason": f"fixtures.doi_samples.{purpose}.doi is null",
            "evidence": _manifest_evidence(manifest.sample if manifest else None),
            "evidence_confidence": (manifest.sample or {}).get("confidence") if manifest else None,
        }
    doi = normalize_doi(str(raw_doi))
    slug = doi_slug(doi)
    provider = provider or infer_provider_from_doi(doi) or "unknown"
    source_url = f"https://doi.org/{doi}"

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if args.dry_run:
        content_type = "text/html"
        body = b""
        final_url = source_url
        route = args.via
    else:
        route = args.via
        try:
            response = _capture_route(doi, route=route)
            content_type, body, final_url = _validate_capture_response(
                response=response,
                purpose=purpose,
                route=route,
            )
            final_url = final_url or source_url
        except CaptureFixtureError as exc:
            retry_via = getattr(args, "retry_via", None)
            if _should_retry_via(exc, retry_via=retry_via, manifest=manifest):
                route = retry_via
                try:
                    response = _capture_route(doi, route=route)
                    content_type, body, final_url = _validate_capture_response(
                        response=response,
                        purpose=purpose,
                        route=route,
                    )
                    final_url = final_url or source_url
                except CaptureFixtureError as retry_exc:
                    if retry_exc.previous_code is None:
                        retry_exc.previous_code = exc.code
                    raise retry_exc from exc
            else:
                raise

    fixture_path = _fixture_path(root, slug, purpose, content_type)
    manifest_path = root / "tests" / "fixtures" / "golden_criteria" / "manifest.json"
    manifest = _load_manifest(manifest_path)
    samples = manifest["samples"]
    exists = fixture_path.exists() or slug in samples
    if exists and not args.force:
        raise CaptureFixtureError(
            "UNSUITABLE_DOI_SAMPLE",
            f"refusing to overwrite existing fixture or manifest sample: {slug}",
            retryable=False,
        )

    entry = _manifest_entry(
        doi=doi,
        provider=provider,
        source_url=final_url,
        fetched_at=fetched_at,
        purpose=purpose,
        fixture_path=fixture_path,
        root=root,
        content_type=content_type,
    )
    summary = {
        "status": "OK",
        "doi": doi,
        "dry_run": bool(args.dry_run),
        "fixture_path": fixture_path.relative_to(root).as_posix(),
        "manifest_sample_id": slug,
        "manifest_entry": entry,
        "content_type": content_type,
        "bytes": len(body),
        "route": entry["route_kind"],
        "capture_route": route,
        "route_kind": entry["route_kind"],
        "purpose": purpose,
        "provider": provider,
    }
    if getattr(args, "from_manifest", None):
        provider_manifest = _manifest_context(getattr(args, "from_manifest", None), purpose)
        summary["manifest"] = str(args.from_manifest)
        summary["manifest_sample"] = _manifest_evidence(provider_manifest.sample if provider_manifest else None)
        summary["evidence_confidence"] = (provider_manifest.sample or {}).get("confidence") if provider_manifest else None
        summary["provider_routing"] = provider_manifest.routing if provider_manifest else {}

    if args.dry_run:
        summary["would_write"] = [summary["fixture_path"], "tests/fixtures/golden_criteria/manifest.json"]
        return summary

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(body)
    samples[slug] = entry
    _write_manifest(manifest_path, manifest)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = CaptureArgumentParser(description="Capture a DOI replay fixture and register it in the golden manifest.")
    parser.add_argument("--doi", help="DOI to capture, for example 10.1234/sample")
    parser.add_argument("--provider", help="provider name; defaults to DOI/catalog inference")
    parser.add_argument("--via", choices=("http", "playwright", "browser"), default="http")
    parser.add_argument("--purpose", choices=CLI_PURPOSES, required=True)
    parser.add_argument("--from-manifest", help="ProviderManifest YAML input; reads DOI, evidence, and routing by purpose")
    parser.add_argument("--retry-via", choices=("browser", "playwright"), help="retry failed capture through another route")
    parser.add_argument("--fail-fast", action="store_true", help="emit JSON stderr and exit non-zero on the first failure")
    parser.add_argument("--dry-run", action="store_true", help="print planned writes without fetching or writing")
    parser.add_argument("--output-dir", default=_repo_root(), help="repo root to write into; defaults to this checkout")
    parser.add_argument("--force", action="store_true", help="overwrite existing fixture and manifest sample")
    return parser


def _error_context(args: argparse.Namespace) -> dict[str, str | None]:
    purpose = normalize_purpose(args.purpose) if args.purpose else None
    provider = args.provider
    if getattr(args, "from_manifest", None):
        try:
            manifest = _manifest_context(args.from_manifest, purpose)
        except Exception:
            manifest = None
        if manifest and manifest.provider:
            provider = provider or manifest.provider
    return {
        "provider": provider,
        "manifest": getattr(args, "from_manifest", None),
        "purpose": purpose,
        "task_id": f"{provider}-step3-capture-fixtures" if provider else "capture-fixtures",
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.doi and not args.from_manifest:
        error = CaptureFixtureError(
            "UNSUITABLE_DOI_SAMPLE",
            "--doi is required unless --from-manifest is provided",
            retryable=False,
        )
        context = _error_context(args)
        emit_error(error.to_payload(**context))
        return 1
    try:
        summary = capture_fixture(args)
    except ToolError as exc:
        context = _error_context(args)
        details = dict(exc.details)
        if context.get("purpose"):
            details.setdefault("purpose", context["purpose"])
        emit_error(
            error_payload(
                exc.code,
                exc.message,
                provider=exc.provider or context["provider"],
                manifest=exc.manifest or context["manifest"],
                task_id=exc.task_id or context["task_id"],
                retryable=exc.retryable,
                details=details,
            )
        )
        return 1
    except CaptureFixtureError as exc:
        context = _error_context(args)
        emit_error(exc.to_payload(**context))
        return 1
    except Exception as exc:
        context = _error_context(args)
        error = CaptureFixtureError("UNSUITABLE_DOI_SAMPLE", str(exc), retryable=False)
        emit_error(error.to_payload(**context))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
