from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

from paper_fetch import runtime_browser
from paper_fetch.runtime import RuntimeContext
from paper_fetch.runtime_browser import BrowserContextManager


class _FakeBrowser:
    def __init__(self, *, headless: bool, locale: str) -> None:
        self.headless = headless
        self.locale = locale
        self.context_kwargs: list[dict[str, Any]] = []
        self.close_count = 0

    def new_context(self, **kwargs: Any) -> Any:
        self.context_kwargs.append(dict(kwargs))
        return SimpleNamespace(kwargs=dict(kwargs))

    def close(self) -> None:
        self.close_count += 1


def test_browser_reused_across_calls(monkeypatch) -> None:
    launches: list[_FakeBrowser] = []

    def launch(*, headless: bool, locale: str) -> _FakeBrowser:
        browser = _FakeBrowser(headless=headless, locale=locale)
        launches.append(browser)
        return browser

    monkeypatch.setattr("cloakbrowser.launch", launch)
    lifecycle = BrowserContextManager()

    first_context = lifecycle.new_context(headless=True, locale="en-US")
    second_context = lifecycle.new_context(headless=True, viewport={"width": 800})

    assert len(launches) == 1
    assert launches[0].headless is True
    assert launches[0].locale == "en-US"
    assert first_context.kwargs == {"locale": "en-US"}
    assert second_context.kwargs == {"viewport": {"width": 800}}
    assert launches[0].context_kwargs == [{"locale": "en-US"}, {"viewport": {"width": 800}}]


def test_headless_change_restarts_browser(monkeypatch) -> None:
    launches: list[_FakeBrowser] = []

    def launch(*, headless: bool, locale: str) -> _FakeBrowser:
        browser = _FakeBrowser(headless=headless, locale=locale)
        launches.append(browser)
        return browser

    monkeypatch.setattr("cloakbrowser.launch", launch)
    lifecycle = BrowserContextManager()

    first_browser = lifecycle.browser(headless=True)
    second_browser = lifecycle.browser(headless=False)

    assert len(launches) == 2
    assert first_browser is launches[0]
    assert second_browser is launches[1]
    assert first_browser.close_count == 1
    assert second_browser.close_count == 0
    assert second_browser.headless is False


def test_runtime_context_recommended_browser_context_entrypoint() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeLifecycle:
        def browser(self, **kwargs: Any) -> str:
            calls.append(("browser", dict(kwargs)))
            return "browser"

        def new_context(self, **kwargs: Any) -> str:
            calls.append(("new_context", dict(kwargs)))
            return "context"

        def close(self) -> None:
            calls.append(("close", {}))

    context = RuntimeContext(env={})
    context._browser_context_manager = FakeLifecycle()  # type: ignore[assignment]

    assert context.new_browser_context(headless=True, locale="en-US") == "context"
    assert context.new_browser_context(headless=True, viewport={"width": 800}) == "context"
    context.close()

    assert calls == [
        ("new_context", {"headless": True, "locale": "en-US"}),
        ("new_context", {"headless": True, "viewport": {"width": 800}}),
        ("close", {}),
    ]


def test_no_direct_sync_playwright_usage() -> None:
    source = inspect.getsource(runtime_browser)

    assert "sync_playwright(" not in source
