"""Browser lifecycle manager."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserContextManager:
    """Owns a shared CloakBrowser-launched browser for one fetch runtime."""

    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _browser: Any | None = field(default=None, init=False, repr=False)
    _headless: bool | None = field(default=None, init=False, repr=False)

    def browser(self, *, headless: bool = True) -> Any:
        active_headless = bool(headless)
        with self._lock:
            if self._browser is not None and self._headless == active_headless:
                return self._browser
            if self._browser is not None:
                self.close()

            import cloakbrowser

            browser = cloakbrowser.launch(headless=active_headless, locale="en-US")
            self._browser = browser
            self._headless = active_headless
            return browser

    def new_context(self, *, headless: bool = True, **context_kwargs: Any) -> Any:
        with self._lock:
            return self.browser(headless=headless).new_context(**context_kwargs)

    def close(self) -> None:
        with self._lock:
            browser = self._browser
            self._browser = None
            self._headless = None
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup at GC/interpreter shutdown
        try:
            self.close()
        except Exception:
            pass

__all__ = [
    "BrowserContextManager",
]
