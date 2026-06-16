"""nodriver-based HTML fetching with Cloudflare bypass.

Built directly from the verified ``cf_unified.py`` (24/24 tests passed).
Adds the paper-fetch-skill API surface (``fetch_html_with_nodriver`` etc.)
on top of the battle-tested CF bypass engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Any, Mapping

from .._nodriver_runtime import import_nodriver, kill_chrome
from ..config import (
    DEFAULT_CHROME_EXE,
    DEFAULT_NODRIVER_TEMP_PROFILE,
    NODRIVER_CHROME_PATH_ENV_VAR,
    NODRIVER_USER_DATA_DIR_ENV_VAR,
    NODRIVER_HEADLESS_ENV_VAR,
)
from .browser_runtime.types import (
    BrowserFetchedHtml,
    BrowserRuntimeConfig,
    BrowserRuntimeFailure,
)

logger = logging.getLogger("paper_fetch.providers.nodriver_fetch")

DEFAULT_BROWSER_RUNTIME_MAX_TIMEOUT_MS = 120_000
DEFAULT_BROWSER_RUNTIME_WAIT_SECONDS = 5
DEFAULT_BROWSER_RUNTIME_WARM_WAIT_SECONDS = 2

# ═══════════════════════════════════════════════════════════════════
# Bezier / CDP helpers (from cf_unified.py)
# ═══════════════════════════════════════════════════════════════════

def _cdp(method, params=None):
    result = yield {"method": method, "params": params or {}}
    return result


def _bezier(t, p0, p1, p2, p3):
    mt = 1 - t
    return (mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
            mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1])


def _human_path(sx, sy, ex, ey, steps=50):
    c1 = (sx + random.randint(-60, 100), sy + random.randint(-40, 60))
    c2 = (ex + random.randint(-80, 50), ey + random.randint(-50, 40))
    pts = [_bezier(i / steps, (sx, sy), c1, c2, (ex, ey)) for i in range(steps + 1)]
    result = [(x + random.randint(-3, 3), y + random.randint(-3, 3)) if 5 < i < steps - 5 else (x, y)
              for i, (x, y) in enumerate(pts)]
    result[-1] = (ex, ey)
    return result


# ═══════════════════════════════════════════════════════════════════
# Profile management (from cf_unified.py, adapted for config)
# ═══════════════════════════════════════════════════════════════════

def _resolve_real_profile() -> str | None:
    """Find the real Chrome profile to copy from."""
    candidates = [
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
    ]
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        candidates.insert(0, Path(local_appdata) / "Google" / "Chrome" / "User Data")
    for p in candidates:
        if p.exists() and (p / "Default").exists():
            return str(p)
    return None


def _copy_profile_fresh(real: str, temp: str) -> str | None:
    """Copy the Chrome profile fresh every time (like cf_unified.py)."""
    real_p = Path(real)
    temp_p = Path(temp)
    if not real_p.exists() or not (real_p / "Default").exists():
        return None
    if temp_p.exists():
        import shutil
        shutil.rmtree(temp_p, ignore_errors=True)
    temp_p.mkdir(parents=True, exist_ok=True)
    import shutil
    for sub in ["Default", "Local State", "Preferences"]:
        src, dst = real_p / sub, temp_p / sub
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "Cache", "Code Cache", "GPUCache", "Service Worker",
                "IndexedDB", "WebStorage", "shared_proto_db",
                "History", "Favicons", "Top Sites", "Media History",
                # Chrome locks these while running
                "Cookies", "Cookies-journal",
                "Safe Browsing*", "Network", "Sessions",
                "Tabs_*", "Session_*",
                "TransportSecurity", "Reporting and NEL",
                "Trust Tokens", "TrustToken*",
                "Site Characteristics Database",
                "segmentation_platform",
                "MediaFoundationWidevineCdm*",
            ), dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)
    return str(temp_p)


# ═══════════════════════════════════════════════════════════════════
# CF detection (from cf_unified.py, identical logic)
# ═══════════════════════════════════════════════════════════════════

async def _check_cf_passed(tab) -> bool:
    try:
        title = (await tab.evaluate("document.title")) or ""
        body = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''")
    except Exception:
        return False
    blocked = any(w in (title + body).lower() for w in (
        "just a moment", "checking your browser", "cf-browser-verify",
        "请稍候", "verify you are human", "attention required",
    ))
    return not blocked and len(body) > 50 and title not in ("", "403 Forbidden", "请稍候…")


async def _detect_cf_type(tab) -> str:
    try:
        html = await tab.evaluate("document.documentElement.outerHTML")
    except Exception:
        return "unknown"
    hl = html.lower()

    if "h-captcha" in hl or "hcaptcha" in hl:
        return "hcaptcha"

    # JS challenge BEFORE Turnstile (cf-ch1 / cf_chl markers)
    if any(s in hl for s in ("jschl", "cf_chl", "cf-chl", "cf-browser-verify")):
        return "js_challenge"

    # Turnstile — must have visible widget in DOM
    if "challenges.cloudflare.com" in hl or "cf-turnstile" in hl:
        has_turnstile_widget = await tab.evaluate("""
        (() => {
            let f = document.querySelector(
                'iframe[src*="challenges.cloudflare.com"], ' +
                'iframe[src*="cf-turnstile"], ' +
                'iframe[src*="turnstile"]'
            );
            if (f && f.getBoundingClientRect().width > 20) return true;
            let cb = document.querySelector('input[type="checkbox"]');
            if (cb && cb.getBoundingClientRect().width > 0) return true;
            let widget = document.querySelector('.cf-turnstile, [class*="cf-turnstile"]');
            if (widget) return true;
            return false;
        })()
        """)
        if not has_turnstile_widget:
            return "js_challenge"

        has_challenge = any(w in hl for w in (
            "verify you are human", "just a moment", "checking your browser",
            "请稍候", "正在检查",
        ))
        return "turnstile_visible" if has_challenge else "turnstile_hidden"

    try:
        title = (await tab.evaluate("document.title")) or ""
        if "403" in title:
            return "js_challenge"
    except Exception:
        pass

    return "none"


# ═══════════════════════════════════════════════════════════════════
# Turnstile click (from cf_unified.py, identical logic)
# ═══════════════════════════════════════════════════════════════════

async def _find_checkbox(tab, max_retries=5):
    for attempt in range(max_retries):
        pos = await tab.evaluate("""
        (() => {
            let frame = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (frame) { let r = frame.getBoundingClientRect(); if (r.width>0&&r.height>0) return [r.left+r.width*0.33,r.top+r.height*0.5,'s1']; }
            let c = document.querySelector('.cf-turnstile, [class*="cf-turnstile"]');
            if (c) { let f = c.querySelector('iframe'); if (f) { let r = f.getBoundingClientRect(); if (r.width>0) return [r.left+r.width*0.33,r.top+r.height*0.5,'s2']; } }
            let all = document.querySelectorAll('iframe');
            for (let f of all) { let a = (f.src||'')+(f.name||'')+(f.getAttribute('data-src')||''); if (/cloudflare|turnstile|challenges|cf-chl/i.test(a)) { let r = f.getBoundingClientRect(); if (r.width>0&&r.height>0) return [r.left+r.width*0.33,r.top+r.height*0.5,'s3']; } }
            for (let f of all) { let r = f.getBoundingClientRect(); if (r.width>=200&&r.width<=400&&r.height>=40&&r.height<=120) { if (r.left>-100&&r.top>-100&&r.left<window.innerWidth+100) return [r.left+r.width*0.33,r.top+r.height*0.5,'s4']; } }
            let cbs = document.querySelectorAll('input[type="checkbox"]');
            for (let cb of cbs) { let r = cb.getBoundingClientRect(); if (r.width>0&&r.height>0) return [r.left+r.width/2,r.top+r.height/2,'s5']; }
            return [];
        })()
        """)
        if isinstance(pos, list) and len(pos) >= 2 and pos[0] > 0:
            return round(pos[0]), round(pos[1])
        if attempt < max_retries - 1:
            await tab.sleep(0.5)
    return None, None


async def _do_click(tab, cx, cy, vp_w=1920, vp_h=1080):
    sx = random.randint(int(vp_w * 0.15), int(vp_w * 0.45))
    sy = random.randint(int(vp_h * 0.3), int(vp_h * 0.65))
    path = _human_path(sx, sy, cx, cy, steps=random.randint(40, 65))
    for i, (x, y) in enumerate(path):
        await tab.send(_cdp("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": round(x), "y": round(y), "button": "none", "buttons": 0}))
        d = random.randint(8, 18) if i < len(path) - 5 else random.randint(20, 40)
        await tab.sleep(d / 1000)
    await tab.sleep(random.uniform(0.25, 0.7))
    await tab.send(_cdp("Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": cx, "y": cy, "button": "left", "buttons": 1, "clickCount": 1}))
    await tab.sleep(random.uniform(0.05, 0.12))
    await tab.send(_cdp("Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": cx, "y": cy, "button": "left", "buttons": 0}))


async def _click_turnstile(tab) -> bool:
    cx, cy = await _find_checkbox(tab)
    vp_w = (await tab.evaluate("window.innerWidth")) or 1920
    vp_h = (await tab.evaluate("window.innerHeight")) or 1080
    if isinstance(vp_w, list): vp_w = vp_w[0] if vp_w else 1920
    if isinstance(vp_h, list): vp_h = vp_h[0] if vp_h else 1080

    if cx is not None:
        await _do_click(tab, cx, cy, vp_w, vp_h)
        for _ in range(8):
            await tab.sleep(1)
            if await _check_cf_passed(tab): return True
        for ox, oy in [(6, 0), (-6, 0), (0, 6), (0, -6)]:
            await _do_click(tab, cx + ox, cy + oy, vp_w, vp_h)
            await tab.sleep(3)
            if await _check_cf_passed(tab): return True
        return False

    # Multi-point fallback
    fallbacks = [
        (int(vp_w * 0.5), int(vp_h * 0.42)),
        (int(vp_w * 0.33), int(vp_h * 0.5)),
        (int(vp_w * 0.67), int(vp_h * 0.5)),
    ]
    for fx, fy in fallbacks:
        logger.debug("Turnstile fallback click (%d,%d)", fx, fy)
        await _do_click(tab, fx, fy, vp_w, vp_h)
        for _ in range(5):
            await tab.sleep(1)
            if await _check_cf_passed(tab): return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Core: single-shot CF bypass attempt (from cf_unified.py try_once)
# ═══════════════════════════════════════════════════════════════════

async def _finalize(tab, browser, url: str, cf_type: str, publisher: str = "") -> dict[str, Any]:
    """Read page HTML, optionally auto-login, stop browser, return result."""
    # Auto-login if publisher supports it
    if publisher:
        try:
            from ._nodriver_login import has_login_handler, try_auto_login
            if has_login_handler(publisher):
                logger.info("Checking if auto-login needed for %s…", publisher)
                login_result = await try_auto_login(tab, publisher)
                if login_result.get("access") == "full_text":
                    logger.info("Auto-login succeeded for %s", publisher)
                else:
                    logger.debug("Auto-login: access=%s for %s", login_result.get("access"), publisher)
        except Exception as exc:
            logger.debug("Auto-login skipped (%s)", exc)

    html = await tab.evaluate("document.documentElement.outerHTML") or ""
    title = await tab.evaluate("document.title") or ""
    final_url = await tab.evaluate("window.location.href") or url
    try:
        await browser.stop()
    except Exception:
        pass
    if isinstance(html, list): html = html[0] if html else ""
    if isinstance(title, list): title = title[0] if title else ""
    if isinstance(final_url, list): final_url = final_url[0] if final_url else url
    return {"ok": True, "html": str(html), "title": str(title), "final_url": str(final_url), "cf_type": cf_type}


async def _try_once(url: str, chrome_path: str, user_data_dir: str,
                    headless: bool = False, publisher: str = "") -> dict[str, Any]:
    """One CF bypass attempt — fresh profile, fresh browser. (cf_unified.py logic)"""
    kill_chrome(user_data_dir=user_data_dir)

    real = _resolve_real_profile()
    if not real:
        return {"ok": False, "cf_type": "no_profile", "error": "No real Chrome profile found"}

    profile = _copy_profile_fresh(real, user_data_dir)
    kwargs = dict(browser_executable_path=chrome_path, headless=headless, sandbox=False)
    if profile:
        kwargs["user_data_dir"] = profile
        kwargs["browser_args"] = ["--profile-directory=Default"]

    uc = import_nodriver()
    browser = await uc.start(**kwargs)
    await asyncio.sleep(2)

    for _ in range(5):
        try:
            tab = await browser.get(url)
            break
        except (StopIteration, RuntimeError):
            await asyncio.sleep(1)
    else:
        try:
            await browser.stop()
        except Exception:
            pass
        raise RuntimeError("Cannot get browser tab")

    await tab.sleep(3)

    # Already passed?
    if await _check_cf_passed(tab):
        return await _finalize(tab, browser, url, "none", publisher)

    cf_type = await _detect_cf_type(tab)
    logger.debug("CF type=%s for %s", cf_type, url)

    if cf_type == "hcaptcha":
        try:
            await browser.stop()
        except Exception:
            pass
        return {"ok": False, "cf_type": "hcaptcha", "error": "hcaptcha cannot be auto-bypassed"}

    if cf_type == "turnstile_visible":
        ok = await _click_turnstile(tab)
        if ok:
            return await _finalize(tab, browser, url, "turnstile", publisher)
        try:
            await browser.stop()
        except Exception:
            pass
        return {"ok": False, "cf_type": "turnstile", "error": "Turnstile click failed"}

    if cf_type in ("turnstile_hidden", "js_challenge"):
        for i in range(25):
            await tab.sleep(1)
            if await _check_cf_passed(tab):
                return await _finalize(tab, browser, url, cf_type, publisher)
            if i == 5:
                h = await tab.evaluate("document.documentElement.outerHTML") or ""
                if "challenges.cloudflare.com" in h.lower():
                    logger.debug("Delayed Turnstile detected for %s", url)
                    ok = await _click_turnstile(tab)
                    if ok:
                        return await _finalize(tab, browser, url, "turnstile", publisher)
                    try:
                        await browser.stop()
                    except Exception:
                        pass
                    return {"ok": False, "cf_type": "turnstile", "error": "Delayed Turnstile failed"}

    # "none" or "unknown" — assume passed
    return await _finalize(tab, browser, url, cf_type, publisher)


# ═══════════════════════════════════════════════════════════════════
# Paper-fetch-skill API surface
# ═══════════════════════════════════════════════════════════════════

def _run_fetch_in_new_loop(_fetch_coro) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch_coro())
    finally:
        loop.close()


def fetch_html_with_nodriver(
    candidate_urls: list[str],
    *,
    publisher: str,
    config: BrowserRuntimeConfig,
    context: Any | None = None,
    **kwargs: Any,
) -> BrowserFetchedHtml:
    """Fetch HTML via nodriver Chrome with CF bypass (up to 3 retries per URL).

    Identical CF bypass logic to the verified ``cf_unified.py`` (24/24 tests).
    """
    chrome_path = config.binary_path or DEFAULT_CHROME_EXE
    user_data_dir = str(config.user_data_dir or DEFAULT_NODRIVER_TEMP_PROFILE)
    headless = config.headless

    if not candidate_urls:
        raise BrowserRuntimeFailure("no_candidate_urls", "No candidate URLs provided")

    async def _fetch():
        last_error = None
        for url in candidate_urls:
            # Up to 3 retries per URL (fresh browser + fresh profile each time)
            for attempt in range(3):
                result = await _try_once(url, chrome_path, user_data_dir, headless, publisher)
                if result.get("ok"):
                    html = result.get("html", "")
                    if html and len(html) > 500:
                        return BrowserFetchedHtml(
                            source_url=url,
                            final_url=result.get("final_url", url),
                            html=html,
                            response_status=200,
                            response_headers={},
                            title=result.get("title"),
                            summary=f"nodriver fetch (cf={result.get('cf_type')})",
                            browser_context_seed={},
                        )
                last_error = result.get("error", "unknown")
                logger.debug("Retry %d/3 for %s: %s", attempt + 1, url, last_error)
                await asyncio.sleep(2)

        if last_error:
            raise BrowserRuntimeFailure(
                "html_fetch_failed",
                f"All {len(candidate_urls)} candidate URLs failed. Last error: {last_error}",
            )
        raise BrowserRuntimeFailure(
            "html_fetch_failed",
            f"No usable HTML from {len(candidate_urls)} candidates",
        )

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is not None:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_fetch_in_new_loop, _fetch)
            return future.result(timeout=config.timeout_ms / 1000.0)
    else:
        return _run_fetch_in_new_loop(_fetch)


fetch_html_with_nodriver.paper_fetch_html_fetcher_name = "nodriver"  # type: ignore[attr-defined]


# ── Compat wrappers ─────────────────────────────────────────────────

def load_runtime_config(env: Mapping[str, str], *, provider: str, doi: str) -> BrowserRuntimeConfig:
    chrome_path = str(env.get(NODRIVER_CHROME_PATH_ENV_VAR, "")).strip() or None
    user_data = str(env.get(NODRIVER_USER_DATA_DIR_ENV_VAR, "")).strip() or None
    headless = str(env.get(NODRIVER_HEADLESS_ENV_VAR, "")).strip().lower() in {"1", "true", "yes"}
    artifact_dir = Path(os.environ.get("TEMP", "/tmp")) / "paper_fetch_browser"
    return BrowserRuntimeConfig(
        provider=provider,
        doi=doi,
        artifact_dir=artifact_dir,
        headless=headless,
        user_agent=None,
        binary_path=chrome_path,
        user_data_dir=Path(user_data) if user_data else None,
    )


def ensure_runtime_ready(config: BrowserRuntimeConfig) -> None:
    """Pre-seed the temp profile dir (copy happens per-fetch in _try_once)."""
    if config.user_data_dir:
        Path(str(config.user_data_dir)).mkdir(parents=True, exist_ok=True)


def probe_runtime_status(env, *, provider, doi="probe://browser/status"):
    from .base import ProviderStatusResult
    return ProviderStatusResult(
        provider=provider,
        status="nodriver",
        available=True,
        official_provider=False,
        missing_env=[],
        notes=[],
        checks=[],
    )


def warm_browser_context_with_nodriver(
    candidate_urls: list[str],
    *,
    publisher: str,
    config: BrowserRuntimeConfig,
    browser_context_seed=None,
    runtime_context=None,
) -> dict[str, Any]:
    try:
        fetch_html_with_nodriver(candidate_urls[:1], publisher=publisher, config=config)
    except Exception:
        pass
    return dict(browser_context_seed or {})
