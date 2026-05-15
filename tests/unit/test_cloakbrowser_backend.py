from __future__ import annotations

from unittest import mock

import pytest

from paper_fetch.providers import _cloakbrowser, _flaresolverr, browser_runtime
from paper_fetch.providers.browser_workflow.html_extraction import _fetch_browser_html_payload
from paper_fetch.runtime import RuntimeContext


class _FakeResponse:
    status = 200

    def all_headers(self) -> dict[str, str]:
        return {"Content-Type": "text/html"}


class _FakeRequest:
    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type


class _FakeRoute:
    def __init__(self, resource_type: str) -> None:
        self.request = _FakeRequest(resource_type)
        self.aborted = False
        self.continued = False

    def abort(self) -> None:
        self.aborted = True

    def continue_(self) -> None:
        self.continued = True


class _FakePage:
    def __init__(self) -> None:
        self.url = ""
        self.closed = False
        self.goto_calls: list[str] = []
        self.aborted_media = False
        self.continued_document = False

    def route(self, _pattern: str, handler) -> None:
        image_route = _FakeRoute("image")
        handler(image_route)
        document_route = _FakeRoute("document")
        handler(document_route)
        self.aborted_media = image_route.aborted
        self.continued_document = document_route.continued

    def goto(self, url: str, **_kwargs):
        self.goto_calls.append(url)
        self.url = url
        return _FakeResponse()

    def wait_for_timeout(self, _timeout_ms: int) -> None:
        return None

    def content(self) -> str:
        return (
            "<html><head><title>Example Article</title></head>"
            "<body><main>Readable full text body.</main></body></html>"
        )

    def title(self) -> str:
        return "Example Article"

    def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self) -> None:
        self.page = _FakePage()
        self.closed = False

    def new_page(self) -> _FakePage:
        return self.page

    def cookies(self) -> list[dict[str, str]]:
        return [{"name": "cf_clearance", "value": "secret", "domain": ".science.org", "path": "/"}]

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.context = _FakeContext()
        self.new_context_kwargs: dict[str, object] = {}
        self.closed = False

    def new_context(self, **kwargs):
        self.new_context_kwargs = dict(kwargs)
        return self.context

    def close(self) -> None:
        self.closed = True


class _FakeCloakBrowserModule:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()
        self.launch_kwargs: dict[str, object] = {}

    def launch(self, **kwargs):
        self.launch_kwargs = dict(kwargs)
        return self.browser


def _runtime_config(tmp_path):
    return _cloakbrowser.CloakBrowserRuntimeConfig(
        provider="science",
        doi="10.1126/science.example",
        artifact_dir=tmp_path / "artifacts",
        headless=True,
        user_agent="paper-fetch-test/1",
        timeout_ms=12345,
    )


class _FakeWorkflowClient:
    name = "science"

    def extract_markdown(self, _html_text, _final_url, *, metadata):
        return "# Example Article\n\n## Results\n\n" + ("Readable body. " * 80), {
            "title": metadata.get("title") or "Example Article",
        }


def test_fetch_html_with_cloakbrowser_returns_existing_html_contract(tmp_path) -> None:
    fake_module = _FakeCloakBrowserModule()
    config = _runtime_config(tmp_path)

    with mock.patch.object(_cloakbrowser, "_import_cloakbrowser", return_value=fake_module):
        result = _cloakbrowser.fetch_html_with_cloakbrowser(
            ["https://www.science.org/doi/full/10.1126/science.example"],
            publisher="science",
            config=config,
            disable_media=True,
            wait_seconds=0,
        )

    assert result.final_url == "https://www.science.org/doi/full/10.1126/science.example"
    assert result.response_status == 200
    assert result.response_headers["content-type"] == "text/html"
    assert result.title == "Example Article"
    assert result.browser_context_seed["browser_user_agent"] == "paper-fetch-test/1"
    assert result.browser_context_seed["browser_cookies"][0]["name"] == "cf_clearance"
    assert fake_module.launch_kwargs["headless"] is True
    assert fake_module.browser.new_context_kwargs["user_agent"] == "paper-fetch-test/1"
    assert fake_module.browser.context.page.aborted_media is True
    assert fake_module.browser.context.page.continued_document is True
    assert fake_module.browser.context.closed is True
    assert fake_module.browser.closed is True


def test_fetch_html_with_browser_marks_diagnostic(tmp_path) -> None:
    fake_module = _FakeCloakBrowserModule()
    config = _runtime_config(tmp_path)

    with mock.patch.object(_cloakbrowser, "_import_cloakbrowser", return_value=fake_module):
        _html_result, payload = _fetch_browser_html_payload(
            _FakeWorkflowClient(),
            ["https://www.science.org/doi/full/10.1126/science.example"],
            runtime=config,
            metadata={"doi": "10.1126/science.example", "title": "Example Article"},
            context=RuntimeContext(env={}),
            wait_seconds=0,
        )

    assert payload.content is not None
    assert payload.content.diagnostics["html_fetcher"] == "cloakbrowser"


def test_fetch_html_with_cloakbrowser_reports_unsupported_image_payload(tmp_path) -> None:
    with pytest.raises(_cloakbrowser.CloakBrowserFailure) as exc_info:
        _cloakbrowser.fetch_html_with_cloakbrowser(
            ["https://www.science.org/image.png"],
            publisher="science",
            config=_runtime_config(tmp_path),
            return_image_payload=True,
        )

    assert exc_info.value.kind == "cloakbrowser_image_payload_unsupported"


def test_probe_runtime_status_reports_missing_cloakbrowser_dependency() -> None:
    with (
        mock.patch.object(_cloakbrowser, "_dependency_available", return_value=False),
        mock.patch.object(_cloakbrowser, "_dependency_details", return_value={"probe": "importlib.find_spec"}),
    ):
        result = _cloakbrowser.probe_runtime_status({}, provider="science")

    checks = {check.name: check for check in result.checks}
    assert result.status == "not_configured"
    assert checks["runtime_env"].status == "not_configured"
    assert checks["cloakbrowser_dependency"].status == "not_configured"


def test_browser_runtime_module_imports() -> None:
    assert browser_runtime.BrowserRuntimeConfig is _cloakbrowser.CloakBrowserRuntimeConfig
    assert browser_runtime.BrowserRuntimeFailure is _cloakbrowser.CloakBrowserFailure
    assert issubclass(browser_runtime.BrowserRuntimeFailure, _flaresolverr.FlareSolverrFailure)
    assert browser_runtime.BrowserFetchedHtml is _flaresolverr.FetchedPublisherHtml
    assert hasattr(browser_runtime, "BrowserImagePayload")
    assert browser_runtime.fetch_html_with_browser.paper_fetch_html_fetcher_name == "cloakbrowser"
    assert callable(browser_runtime.warm_browser_context)
    assert callable(browser_runtime.load_runtime_config)
    assert callable(browser_runtime.ensure_runtime_ready)
    assert callable(browser_runtime.probe_runtime_status)
