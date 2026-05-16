from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tests.script_modules import load_script_module


REPO_ROOT = Path(__file__).resolve().parents[2]


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values = {
        "doi": "10.1234/example",
        "provider": "examplepub",
        "via": "http",
        "purpose": "structure",
        "from_manifest": None,
        "retry_via": None,
        "fail_fast": False,
        "dry_run": False,
        "output_dir": str(tmp_path),
        "force": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _write_provider_manifest(
    path: Path,
    *,
    provider: str = "mdpi",
    doi: str | None = "10.3390/membranes15030093",
) -> None:
    rendered_doi = "null" if doi is None else f'"{doi}"'
    path.write_text(
        f"""
name: {provider}
routing:
  primary: doi_prefix
  doi_prefixes:
    - "10.3390/"
probe:
  requires_browser_runtime: false
fixtures:
  doi_samples:
    structure:
      doi: {rendered_doi}
      evidence_url: "https://example.test/article"
      evidence_reason: "Sample covers structure capture."
      observed_signals:
        - body_sections
      confidence: high
""",
        encoding="utf-8",
    )


def test_capture_fixture_writes_fixture_manifest_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module("capture_fixture")

    class FakeTransport:
        def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
            assert method == "GET"
            assert url == "https://doi.org/10.1234/example"
            return {
                "headers": {"content-type": "text/html; charset=utf-8"},
                "body": b"<html><title>Fixture</title></html>",
                "url": "https://publisher.test/article",
                "status_code": 200,
            }

    monkeypatch.setattr(module, "HttpTransport", FakeTransport)

    summary = module.capture_fixture(_args(tmp_path))

    fixture_path = tmp_path / "tests" / "fixtures" / "golden_criteria" / "10.1234_example" / "original.html"
    manifest_path = tmp_path / "tests" / "fixtures" / "golden_criteria" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert fixture_path.read_bytes() == b"<html><title>Fixture</title></html>"
    assert summary["fixture_path"] == "tests/fixtures/golden_criteria/10.1234_example/original.html"
    assert summary["content_type"] == "text/html; charset=utf-8"
    assert summary["bytes"] == len(b"<html><title>Fixture</title></html>")
    assert manifest["samples"]["10.1234_example"]["expected_outcome"] == "pending"
    assert manifest["samples"]["10.1234_example"]["purpose"] == "structure"
    assert manifest["samples"]["10.1234_example"]["assets"]["original.html"] == summary["fixture_path"]


def test_capture_fixture_dry_run_does_not_fetch_or_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module("capture_fixture")

    class FailingTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("dry-run must not fetch")

    monkeypatch.setattr(module, "HttpTransport", FailingTransport)

    summary = module.capture_fixture(_args(tmp_path, doi="10.0000/probe", dry_run=True))

    assert summary["dry_run"] is True
    assert summary["bytes"] == 0
    assert summary["would_write"]
    assert not (tmp_path / "tests").exists()


def test_capture_fixture_refuses_to_overwrite_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module("capture_fixture")

    fixture_dir = tmp_path / "tests" / "fixtures" / "golden_criteria" / "10.1234_example"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "original.html").write_text("old", encoding="utf-8")

    class FakeTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"headers": {"content-type": "text/html"}, "body": b"new", "url": "https://example.test"}

    monkeypatch.setattr(module, "HttpTransport", FakeTransport)

    with pytest.raises(module.CaptureFixtureError) as exc_info:
        module.capture_fixture(_args(tmp_path))
    assert exc_info.value.code == "UNSUITABLE_DOI_SAMPLE"


def test_capture_fixture_routes_block_purpose_to_block_fixture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module("capture_fixture")

    class FakeTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"headers": {"content-type": "text/html"}, "body": b"<html>gate</html>", "url": "https://example.test"}

    monkeypatch.setattr(module, "HttpTransport", FakeTransport)

    summary = module.capture_fixture(_args(tmp_path, purpose="access-gate"))

    assert summary["route"] == "block"
    assert (tmp_path / "tests" / "fixtures" / "block" / "10.1234_example" / "original.html").is_file()
    manifest = json.loads((tmp_path / "tests" / "fixtures" / "golden_criteria" / "manifest.json").read_text())
    assert manifest["samples"]["10.1234_example"]["fixture_family"] == "block"


def test_capture_fixture_reads_doi_and_evidence_from_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("capture_fixture")
    manifest_path = tmp_path / "mdpi.yml"
    _write_provider_manifest(manifest_path)

    class FakeTransport:
        def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
            assert method == "GET"
            assert url == "https://doi.org/10.3390/membranes15030093"
            return {
                "headers": {"content-type": "text/html"},
                "body": b"<html><body><article>Full article body</article></body></html>",
                "url": "https://www.mdpi.com/10.3390/membranes15030093",
                "status_code": 200,
            }

    monkeypatch.setattr(module, "HttpTransport", FakeTransport)

    summary = module.capture_fixture(
        _args(tmp_path, doi=None, provider=None, from_manifest=str(manifest_path), purpose="structure"),
    )

    assert summary["doi"] == "10.3390/membranes15030093"
    assert summary["provider"] == "mdpi"
    assert summary["manifest_sample"]["evidence_url"] == "https://example.test/article"
    assert summary["evidence_confidence"] == "high"
    assert summary["provider_routing"]["primary"] == "doi_prefix"


def test_capture_fixture_skips_manifest_null_doi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module("capture_fixture")
    manifest_path = tmp_path / "mdpi.yml"
    _write_provider_manifest(manifest_path, doi=None)

    class FailingTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("null DOI sample must skip without fetching")

    monkeypatch.setattr(module, "HttpTransport", FailingTransport)

    summary = module.capture_fixture(
        _args(tmp_path, doi=None, provider=None, from_manifest=str(manifest_path), purpose="structure"),
    )

    assert summary["status"] == "SKIPPED"
    assert summary["skipped"] is True
    assert summary["reason"] == "fixtures.doi_samples.structure.doi is null"
    assert not (tmp_path / "tests").exists()


def test_capture_fixture_retries_403_with_browser_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("capture_fixture")

    class ForbiddenTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"headers": {"content-type": "text/html"}, "body": b"forbidden", "status_code": 403}

    monkeypatch.setattr(module, "HttpTransport", ForbiddenTransport)

    with pytest.raises(module.CaptureFixtureError) as exc_info:
        module.capture_fixture(_args(tmp_path, retry_via="browser"))

    assert exc_info.value.code == "BROWSER_RUNTIME_REQUIRED"
    assert exc_info.value.previous_code == "HTTP_FORBIDDEN"
    assert exc_info.value.route == "browser"
    assert not (tmp_path / "tests").exists()


def test_capture_fixture_maps_challenge_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script_module("capture_fixture")

    class ChallengeTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "headers": {"content-type": "text/html"},
                "body": b"<html><title>Just a moment</title><body>verify you are human captcha</body></html>",
                "status_code": 200,
            }

    monkeypatch.setattr(module, "HttpTransport", ChallengeTransport)

    with pytest.raises(module.CaptureFixtureError) as exc_info:
        module.capture_fixture(_args(tmp_path))

    assert exc_info.value.code == "CHALLENGE_DETECTED"


def test_capture_fixture_maps_non_pdf_fallback_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("capture_fixture")

    class HtmlTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "headers": {"content-type": "text/html"},
                "body": b"<html><body>PDF wrapper page</body></html>",
                "status_code": 200,
            }

    monkeypatch.setattr(module, "HttpTransport", HtmlTransport)

    with pytest.raises(module.CaptureFixtureError) as exc_info:
        module.capture_fixture(_args(tmp_path, purpose="pdf_fallback"))

    assert exc_info.value.code == "NON_PDF_FALLBACK_CONTENT"


def test_capture_fixture_maps_timeout_to_network_transient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script_module("capture_fixture")

    class TimeoutTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise TimeoutError("read timed out")

    monkeypatch.setattr(module, "HttpTransport", TimeoutTransport)

    with pytest.raises(module.CaptureFixtureError) as exc_info:
        module.capture_fixture(_args(tmp_path))

    assert exc_info.value.code == "NETWORK_TRANSIENT"
    assert exc_info.value.retryable is True
    assert exc_info.value.route == "http"


def test_capture_fixture_fail_fast_writes_json_stderr_without_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_script_module("capture_fixture")

    class RateLimitedTransport:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"headers": {"content-type": "text/html"}, "body": b"slow down", "status_code": 429}

    monkeypatch.setattr(module, "HttpTransport", RateLimitedTransport)

    rc = module.main(
        [
            "--doi",
            "10.1234/example",
            "--provider",
            "examplepub",
            "--purpose",
            "structure",
            "--fail-fast",
            "--output-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert rc == 1
    assert captured.out == ""
    assert error["code"] == "HTTP_RATE_LIMITED"
    assert error["provider"] == "examplepub"
    assert error["purpose"] == "structure"
