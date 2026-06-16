"""Browser lifecycle manager — nodriver (CDP-based Chrome) runtime."""

from __future__ import annotations

import asyncio
import os
import threading
import time as time_mod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._nodriver_runtime import copy_profile, import_nodriver, kill_chrome
from .config import (
    DEFAULT_CHROME_EXE,
    DEFAULT_NODRIVER_TEMP_PROFILE,
    NODRIVER_CHROME_PATH_ENV_VAR,
    NODRIVER_USER_DATA_DIR_ENV_VAR,
    NODRIVER_HEADLESS_ENV_VAR,
)

DEFAULT_BROWSER_LOCALE = "en-US"
DEFAULT_BROWSER_VIEWPORT = {"width": 1440, "height": 1600}


def browser_context_options(
    *,
    user_agent: str | None = None,
    locale: str = DEFAULT_BROWSER_LOCALE,
    viewport: dict[str, int] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "locale": locale,
        "viewport": dict(DEFAULT_BROWSER_VIEWPORT if viewport is None else viewport),
    }
    active_user_agent = str(user_agent or "").strip()
    if active_user_agent:
        options["user_agent"] = active_user_agent
    options.update(extra)
    return options


def browser_page_user_agent(page: Any) -> str | None:
    """Read navigator.userAgent from a browser page/tab."""
    try:
        user_agent = page.evaluate("() => navigator.userAgent")
    except Exception:
        return None
    if isinstance(user_agent, list):
        user_agent = user_agent[0] if user_agent else ""
    normalized = str(user_agent or "").strip()
    return normalized or None


def _resolve_chrome_path(env: dict[str, str] | None = None) -> str:
    # Precedence: explicit env dict > os.environ > built-in default
    from_env = ""
    if env:
        from_env = env.get(NODRIVER_CHROME_PATH_ENV_VAR, "")
    if not from_env:
        from_env = os.environ.get(NODRIVER_CHROME_PATH_ENV_VAR, "")
    if from_env.strip():
        return from_env.strip()
    return DEFAULT_CHROME_EXE


def _resolve_user_data_dir(env: dict[str, str] | None = None) -> str | None:
    # Precedence: explicit env dict > os.environ > built-in default
    from_env = ""
    if env:
        from_env = env.get(NODRIVER_USER_DATA_DIR_ENV_VAR, "")
    if not from_env:
        from_env = os.environ.get(NODRIVER_USER_DATA_DIR_ENV_VAR, "")
    if from_env.strip():
        return from_env.strip()
    return str(DEFAULT_NODRIVER_TEMP_PROFILE)


def _ensure_profile(user_data_dir: str) -> None:
    """Seed the persistent temp profile on first run (copy from real Chrome)."""
    target = Path(user_data_dir)
    if (target / "Default").exists():
        return  # already seeded

    real_candidates = [
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
    ]
    # Also try LOCALAPPDATA env
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        real_candidates.append(Path(local_appdata) / "Google" / "Chrome" / "User Data")

    for real in real_candidates:
        if real.exists() and (real / "Default").exists():
            print(f"[nodriver] 首次复制 profile: {real} → {target}")
            copy_profile(str(real), str(target))
            return

    target.mkdir(parents=True, exist_ok=True)


@contextmanager
def nodriver_chrome_path_env(chrome_path: str):
    """Temporarily set NODRIVER_CHROME_PATH in the environment."""
    active_path = str(chrome_path or "").strip()
    if not active_path:
        yield
        return
    previous = os.environ.get(NODRIVER_CHROME_PATH_ENV_VAR)
    os.environ[NODRIVER_CHROME_PATH_ENV_VAR] = active_path
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(NODRIVER_CHROME_PATH_ENV_VAR, None)
        else:
            os.environ[NODRIVER_CHROME_PATH_ENV_VAR] = previous


class _NodriverPage:
    """Wraps a nodriver Tab to expose a sync Playwright-like API."""

    def __init__(self, tab: Any):
        self._tab = tab

    def goto(self, url: str, **kwargs: Any) -> None:
        """Navigate the tab to *url* (sync wrapper).

        Accepts (and ignores) Playwright kwargs like ``wait_until``, ``timeout``
        for backward compatibility.
        """
        async def _go():
            await self._tab.get(url)
        asyncio.run(_go())

    def evaluate(self, expression: str) -> Any:
        """Evaluate JS in the tab (sync wrapper).

        .. note::
            Unlike the raw nodriver ``tab.evaluate()``, this does NOT
            unwrap single-element lists. Callers receive the raw return
            value from CDP.
        """
        async def _eval():
            return await self._tab.evaluate(expression)
        return asyncio.run(_eval())

    def content(self) -> str:
        """Return the full page HTML."""
        result = self.evaluate("document.documentElement.outerHTML")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")

    @property
    def url(self) -> str:
        async def _url():
            return await self._tab.evaluate("window.location.href")
        result = asyncio.run(_url())
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")

    @property
    def title(self) -> str:
        async def _title():
            return await self._tab.evaluate("document.title")
        result = asyncio.run(_title())
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")

    def wait_for_timeout(self, timeout_ms: float) -> None:
        """Sleep for *timeout_ms* milliseconds."""
        time_mod.sleep(timeout_ms / 1000.0)

    def screenshot(self, *, path: str | None = None, full_page: bool = False) -> bytes | None:
        """Capture a screenshot via CDP (best-effort, may raise)."""
        async def _capture():
            params: dict[str, Any] = {"format": "png"}
            if full_page:
                params["captureBeyondViewport"] = True
            result = await self._tab.send(
                _NodriverPage._cdp("Page.captureScreenshot", params)
            )
            data = result.get("data") if isinstance(result, dict) else None
            return data
        b64 = asyncio.run(_capture())
        if not b64:
            return None
        import base64
        raw = base64.b64decode(b64)
        if path:
            Path(path).write_bytes(raw)
        return raw

    @staticmethod
    def _cdp(method, params=None):
        result = yield {"method": method, "params": params or {}}
        return result


class _NodriverBrowserContext:
    """Mimics a Playwright browser-context using nodriver."""

    def __init__(self, browser: Any):
        self._browser = browser
        self._user_agent = ""
        self._viewport: dict[str, int] | None = None

    def new_page(self) -> _NodriverPage:
        tab = self._browser.main_tab
        page = _NodriverPage(tab)
        # Apply stored user-agent via CDP if set
        if self._user_agent:
            try:
                async def _set_ua():
                    await tab.send(_NodriverPage._cdp(
                        "Network.setUserAgentOverride",
                        {"userAgent": self._user_agent},
                    ))
                asyncio.run(_set_ua())
            except Exception:
                pass
        return page

    def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Add cookies via CDP (best-effort)."""
        async def _add():
            for cookie in cookies:
                params: dict[str, Any] = {
                    "name": str(cookie.get("name", "")),
                    "value": str(cookie.get("value", "")),
                    "domain": str(cookie.get("domain", "")),
                }
                if "path" in cookie:
                    params["path"] = str(cookie["path"])
                try:
                    await self._browser.main_tab.send(
                        _NodriverPage._cdp("Network.setCookie", params)
                    )
                except Exception:
                    pass
        asyncio.run(_add())

    def cookies(self) -> list[dict[str, Any]]:
        """Return cookies via CDP."""
        async def _get():
            try:
                result = await self._browser.main_tab.send(
                    _NodriverPage._cdp("Network.getCookies")
                )
                if isinstance(result, dict):
                    return result.get("cookies", [])
                return []
            except Exception:
                return []
        return asyncio.run(_get())

    @property
    def request(self) -> "_NodriverRequestAdapter":
        return _NodriverRequestAdapter()


class _NodriverRequestAdapter:
    """Minimal ``context.request.get()`` adapter for browser-scoped HTTP GET.

    This falls back to Python's ``urllib`` — it does NOT use the browser's
    cookie jar or network stack.  For most paper-fetch use-cases (fetching
    supplementary PDFs or images that are publicly accessible once the
    browser session is established) this is sufficient.
    """

    def get(self, url: str, **kwargs: Any) -> "_NodriverResponse":
        import urllib.request
        timeout = kwargs.get("timeout", 30)
        if isinstance(timeout, (int, float)) and timeout > 0:
            timeout_sec = timeout / 1000.0 if timeout > 1000 else float(timeout)
        else:
            timeout_sec = 30.0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-fetch-skill/2.0"})
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                body = resp.read()
                return _NodriverResponse(
                    status=resp.status,
                    headers=dict(resp.headers),
                    body=body,
                )
        except Exception as exc:
            raise RuntimeError(f"request.get({url!r}) failed: {exc}") from exc


class _NodriverResponse:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body


@dataclass
class BrowserContextManager:
    """Owns a shared nodriver-launched Chrome for one fetch runtime.

    Public API matches the old CloakBrowser version so callers stay unchanged.
    """

    binary_path: str | None = None
    user_data_dir: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _browser: Any | None = field(default=None, init=False, repr=False)
    _headless: bool | None = field(default=None, init=False, repr=False)

    def _start_browser(self, *, headless: bool = False) -> Any:
        """Start Chrome via nodriver (blocking, runs asyncio.run under the hood)."""
        uc = import_nodriver()
        chrome_path = self.binary_path or _resolve_chrome_path()
        user_data_dir = self.user_data_dir or _resolve_user_data_dir()

        kill_chrome(user_data_dir=user_data_dir)
        time_mod.sleep(1)

        if user_data_dir:
            _ensure_profile(user_data_dir)

        async def _launch():
            kwargs: dict[str, Any] = dict(
                browser_executable_path=chrome_path,
                headless=headless,
                sandbox=False,
            )
            if user_data_dir:
                kwargs["user_data_dir"] = user_data_dir
                kwargs["browser_args"] = ["--profile-directory=Default"]

            browser = await uc.start(**kwargs)
            # Give Chrome time to register its tab targets
            await asyncio.sleep(2)
            return browser

        return asyncio.run(_launch())

    def browser(self, *, headless: bool = False) -> Any:
        """Return (or start) the shared nodriver Browser."""
        active_headless = bool(headless)
        with self._lock:
            if self._browser is not None and self._headless == active_headless:
                return self._browser
            if self._browser is not None:
                self.close()

            self._browser = self._start_browser(headless=active_headless)
            self._headless = active_headless
            return self._browser

    def new_context(self, *, headless: bool = False, **context_kwargs: Any) -> Any:
        """Return a nodriver browser context (thin wrapper for compat).

        Accepted (and stored) Playwright-compat kwargs: ``user_agent``, ``viewport``.
        """
        with self._lock:
            browser = self.browser(headless=headless)
            ctx = _NodriverBrowserContext(browser)
            ctx._user_agent = str(context_kwargs.get("user_agent") or "")
            ctx._viewport = context_kwargs.get("viewport") or context_kwargs.get("screen")
            return ctx

    def close(self) -> None:
        """Stop the browser and release resources."""
        with self._lock:
            browser = self._browser
            self._browser = None
            self._headless = None
            if browser is not None:
                try:
                    asyncio.run(browser.stop())
                except Exception:
                    pass

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


# Temporary backward-compat alias during CloakBrowser → nodriver migration
cloakbrowser_binary_path_env = nodriver_chrome_path_env

__all__ = [
    "BrowserContextManager",
    "browser_context_options",
    "browser_page_user_agent",
    "cloakbrowser_binary_path_env",
    "nodriver_chrome_path_env",
]
