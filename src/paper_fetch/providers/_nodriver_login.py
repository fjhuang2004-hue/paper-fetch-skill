"""Institutional login automation for nodriver browser sessions.

Ported from the standalone CF bypass scripts. Each publisher gets its own
detect + login pair. Credentials are resolved from environment variables
first, with a fallback to ``login_config.json``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

logger = logging.getLogger("paper_fetch.providers.nodriver_login")

# ── Credential resolution ──────────────────────────────────────────
_ENV_USERNAME = "PAPER_FETCH_HZAU_USERNAME"
_ENV_PASSWORD = "PAPER_FETCH_HZAU_PASSWORD"
_ENV_IDP_ENTITY_ID = "PAPER_FETCH_HZAU_IDP_ENTITY_ID"
_DEFAULT_ENTITY_ID = "https://idp.hzau.edu.cn/idp/shibboleth"


def _resolve_credentials() -> dict[str, str] | None:
    """Resolve HZAU credentials from environment or config file.

    Priority:
    1. Environment variables (``PAPER_FETCH_HZAU_USERNAME`` etc.)
    2. ``login_config.json`` in the project script directory
    3. ``login_config.json`` in the user config dir
    """
    username = os.environ.get(_ENV_USERNAME, "").strip()
    password = os.environ.get(_ENV_PASSWORD, "").strip()
    entity_id = os.environ.get(_ENV_IDP_ENTITY_ID, "").strip() or _DEFAULT_ENTITY_ID

    if username and password:
        return {"username": username, "password": password, "idp_entity_id": entity_id}

    # Fallback: login_config.json in known locations
    candidates = [
        # Explicit env var path
        Path(p) for p in [os.environ.get("PAPER_FETCH_LOGIN_CONFIG", "").strip()]
        if p and Path(p).exists()
    ]
    candidates += [
        Path(os.environ.get("LOCALAPPDATA", "")) / "paper-fetch" / "login_config.json",
        Path.home() / "AppData" / "Local" / "paper-fetch" / "login_config.json",
        # Original script directory (where cf_acs_full.py lives)
        Path(r"D:\dogtor\甲醇转多元醇的生物合成\构建好的脚本") / "login_config.json",
        Path(r"D:\dogtor\甲醇转多元醇的生物合成\每日研读报告\HTML") / "login_config.json",
    ]

    for config_path in candidates:
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                hzau = data.get("hzau", {})
                username = str(hzau.get("username", "")).strip()
                password = str(hzau.get("password", "")).strip()
                entity_id = str(hzau.get("idp_entity_id", _DEFAULT_ENTITY_ID)).strip()
                if username and password:
                    return {"username": username, "password": password, "idp_entity_id": entity_id}
        except Exception:
            continue

    return None


# ── Publisher dispatch ─────────────────────────────────────────────
# Map publisher name → (detect_fn, login_fn)
_LOGIN_HANDLERS: dict[str, tuple] = {}


def _register(publisher: str):
    """Decorator: register a detect+login pair for a publisher."""
    def deco(cls):
        _LOGIN_HANDLERS[publisher] = cls
        return cls
    return deco


def has_login_handler(publisher: str) -> bool:
    """Return True if we know how to login for *publisher*."""
    return publisher in _LOGIN_HANDLERS


async def try_auto_login(tab, publisher: str) -> dict[str, Any]:
    """Attempt institutional login if the page is walled.

    Returns:
      ``{"access": "full_text"}`` — login succeeded (page now full-text)
      ``{"access": "no_credentials"}`` — no credentials configured
      ``{"access": "not_walled"}`` — page was not walled to begin with
      ``{"access": "login_failed", "error": ...}`` — login was attempted but failed
      ``{"access": "render_timeout"}`` — login succeeded but full-text didn't render
    """
    if not has_login_handler(publisher):
        return {"access": "not_supported", "publisher": publisher}

    creds = _resolve_credentials()
    if not creds:
        logger.warning("No HZAU credentials found — skipping auto-login for %s", publisher)
        return {"access": "no_credentials"}

    handler_cls = _LOGIN_HANDLERS[publisher]
    handler = handler_cls(tab, creds)
    return await handler.execute()


# ═══════════════════════════════════════════════════════════════════
# ACS Publications
# ═══════════════════════════════════════════════════════════════════
@_register("acs")
class _AcsLoginHandler:
    """ACS: CARSI/Shibboleth SSO → HZAU IdP → CAS login."""

    def __init__(self, tab, creds: dict[str, str]):
        self._tab = tab
        self._creds = creds

    async def execute(self) -> dict[str, Any]:
        tab = self._tab

        # Step 1: detect wall
        detect_result = await self._detect_wall()
        if detect_result["access"] != "walled":
            return detect_result  # full_text / unknown

        sso_url = detect_result.get("sso_url")
        if not sso_url:
            return {"access": "no_sso_url"}

        # Step 2: navigate SSO
        logger.info("ACS walled — starting SSO login…")
        await tab.get(sso_url)
        await tab.sleep(5)

        current_url = await self._current_url()

        # Already back at ACS? (SSO session still valid)
        if "pubs.acs.org" in current_url and "ssostart" not in current_url:
            logger.info("SSO session still valid, already back at ACS")
            await tab.sleep(3)
            return await self._wait_fulltext()

        # CAS login page?
        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        # HZAU IdP page?
        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()

        # CARSI / ACS institution selection page
        if "carsi" in current_url.lower() or "ds.carsi.edu" in current_url \
                or "wayf" in current_url.lower() or "ssostart" in current_url:
            return await self._search_institution()

        # Unknown
        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "acs_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown ACS login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        """Check if ACS page is walled (abstract-only)."""
        tab = self._tab

        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            walled = ("article_abstractPage" in html
                      or "access-denials__wrapper" in html
                      or "accessDenialWidget" in html)

            if walled:
                consecutive_clean = 0
                consecutive_walled += 1
                if consecutive_walled >= 5:
                    # Build SSO URL
                    entity_id = self._creds.get("idp_entity_id", _DEFAULT_ENTITY_ID)
                    current_url = await self._current_url()
                    parsed = urlparse(current_url)
                    redirect_uri = parsed.path
                    if parsed.query:
                        redirect_uri += "?" + parsed.query

                    federation_id = "urn:mace:shibboleth:carsifed"
                    sso_url = (
                        "https://pubs.acs.org/action/ssostart"
                        f"?idp={quote(entity_id, safe='')}"
                        f"&redirectUri={quote(redirect_uri, safe='')}"
                        f"&federationId={quote(federation_id, safe='')}"
                    )
                    logger.info("ACS wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled", "sso_url": sso_url}
            else:
                consecutive_walled = 0
                consecutive_clean += 1
                if consecutive_clean >= 8 and len(body_text) > 3000:
                    return {"access": "full_text"}

        return {"access": "unknown"}

    async def _wait_fulltext(self, timeout: int = 60) -> dict[str, Any]:
        """Wait for full text to render (post-login)."""
        tab = self._tab
        for i in range(timeout):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""
            if "article_fullPage" in html and "NLM_sec" in html and len(body_text) > 5000:
                logger.info("ACS full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for full-text… %ds body=%d", i + 1, len(body_text))
        logger.warning("ACS full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        """Fill the HZAU CAS (Vue.js Element UI) login form."""
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]

        logger.info("Filling HZAU CAS login form…")

        # Wait for Vue.js to render the form (intermittently slow)
        await tab.sleep(1)

        # Username field — retry up to 3 times (Vue.js may still be mounting)
        el = None
        for attempt in range(3):
            try:
                el = await tab.find("input[placeholder*='学工号']", timeout=3)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input[placeholder*='学号']", timeout=2)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input.el-input__inner[type='text']", timeout=2)
                break
            except Exception:
                pass
            if attempt < 2:
                await tab.sleep(1)

        if el:
            await el.click()
            await tab.sleep(0.3)
            await el.clear_input()
            await tab.sleep(0.2)
            await el.send_keys(username)
            logger.debug("CAS username entered")
        else:
            logger.warning("CAS username field not found")

        # Password field — retry up to 3 times
        el = None
        for attempt in range(3):
            try:
                el = await tab.find("input[placeholder*='登录密码']", timeout=3)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input[placeholder*='密码']", timeout=2)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input.el-input__inner[type='password']", timeout=2)
                break
            except Exception:
                pass
            if attempt < 2:
                await tab.sleep(1)

        if el:
            await el.click()
            await tab.sleep(0.3)
            await el.clear_input()
            await tab.sleep(0.2)
            await el.send_keys(password)
            logger.debug("CAS password entered")
        else:
            logger.warning("CAS password field not found")

        # Click login button
        await tab.sleep(0.5)
        submitted = await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click();
                    return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)
        logger.debug("CAS submit: %s", submitted)

        # Wait for redirect back to ACS
        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "pubs.acs.org" in url and "ssostart" not in url:
                logger.info("CAS login successful, back at ACS")
                await tab.sleep(3)
                return await self._wait_fulltext()

            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials", "authentication failed"]):
                return {"access": "login_failed", "error": body_text[:150]}

        return {"access": "login_timeout"}

    async def _handle_idp_page(self) -> dict[str, Any]:
        """Handle HZAU IdP page — wait for or trigger redirect to CAS."""
        tab = self._tab
        logger.info("On HZAU IdP page, waiting for CAS redirect…")
        for _ in range(10):
            await tab.sleep(1)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()
            if "pubs.acs.org" in url and "ssostart" not in url:
                logger.info("Back at ACS (IdP self-redirected)")
                return await self._wait_fulltext()

        # Try manually submitting the SAML form
        logger.debug("IdP didn't auto-redirect, trying manual form submit…")
        await tab.evaluate("""
        (() => {
            let form = document.querySelector('form');
            if (form) { form.submit(); return 'submitted'; }
            let btn = document.querySelector('input[type="submit"], button[type="submit"]');
            if (btn) { btn.click(); return 'clicked'; }
            return 'none';
        })()
        """)
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas" in url or "cas." in url:
            return await self._fill_cas_form()
        return {"access": "idp_redirect_failed"}

    async def _search_institution(self) -> dict[str, Any]:
        """Search for HZAU in ACS/CARSI institution selection page."""
        tab = self._tab
        logger.info("Searching for HZAU in institution list…")

        # Find search box
        try:
            search_input = await tab.find("input.ms-inv", timeout=5)
        except Exception:
            try:
                search_input = await tab.find('input[placeholder*="Search By University"]', timeout=3)
            except Exception:
                logger.warning("Institution search box not found")
                return {"access": "no_search_input"}

        await search_input.click()
        await tab.sleep(0.3)
        await search_input.send_keys("Huazhong")
        logger.debug("Typed 'Huazhong' into institution search")

        # Wait for dropdown and click HZAU
        for attempt in range(10):
            await tab.sleep(1)
            found = await tab.evaluate("""
            (() => {
                let items = document.querySelectorAll('span.sso-institution');
                for (let item of items) {
                    if ((item.innerText || item.textContent || '').includes('Huazhong')) {
                        item.click();
                        return 'clicked';
                    }
                }
                return 'waiting';
            })()
            """)
            if found == "clicked":
                logger.info("Clicked HZAU institution")
                break
        else:
            logger.warning("HZAU not found in institution dropdown")
            return {"access": "institution_not_found"}

        # Wait for redirect to CAS
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas.hzau.edu.cn" in url or "cas." in url:
            return await self._fill_cas_form()

        logger.debug("After institution click: %s", url[:120])
        return {"access": "redirect_unknown"}

    async def _current_url(self) -> str:
        result = await self._tab.evaluate("window.location.href")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")

    async def _get_html(self) -> str:
        result = await self._tab.evaluate("document.documentElement.outerHTML")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")


# ═══════════════════════════════════════════════════════════════════
# ScienceDirect
# ═══════════════════════════════════════════════════════════════════
@_register("elsevier")
class _ScienceDirectLoginHandler:
    """ScienceDirect: Shibboleth SSO → HZAU IdP → CAS login."""

    def __init__(self, tab, creds: dict[str, str]):
        self._tab = tab
        self._creds = creds

    async def execute(self) -> dict[str, Any]:
        tab = self._tab

        detect_result = await self._detect_wall()
        if detect_result["access"] != "walled":
            return detect_result

        shib_url = detect_result.get("shib_url")
        if not shib_url:
            # Fallback: click "Access through" link on page
            logger.info("No Shib URL extracted, trying to click Access through…")
            shib_url = await self._click_access_through()
            if not shib_url:
                return {"access": "no_shib_url"}

        # Navigate SSO
        logger.info("SD walled — starting Shibboleth login…")
        await tab.get(shib_url)
        await tab.sleep(5)

        current_url = await self._current_url()

        # Already back at ScienceDirect?
        if "sciencedirect.com" in current_url:
            logger.info("SSO session still valid, already back at SD")
            await tab.sleep(3)
            return await self._wait_fulltext()

        # CAS login page
        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        # HZAU IdP page
        if "idp.hzau.edu.cn" in current_url:
            logger.info("On HZAU IdP page, waiting for CAS redirect…")
            for _ in range(10):
                await tab.sleep(1)
                url = await self._current_url()
                if "cas-paas" in url or "cas." in url:
                    return await self._fill_cas_form()
                if "sciencedirect.com" in url:
                    logger.info("Back at SD (IdP self-redirected)")
                    return await self._wait_fulltext()

            # Try manual form submit
            await tab.evaluate("""
            (() => {
                let form = document.querySelector('form');
                if (form) { form.submit(); return 'submitted'; }
                let btn = document.querySelector('input[type="submit"], button[type="submit"]');
                if (btn) { btn.click(); return 'clicked'; }
                return 'none';
            })()
            """)
            await tab.sleep(5)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()

        # Unknown page — dump HTML
        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "sd_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown SD login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        tab = self._tab
        consecutive_clean = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            walled = ("preview-sidebar" in html or "Section snippets" in body_text
                      or "Article preview" in body_text)

            if walled:
                consecutive_clean = 0
                if attempt >= 5:
                    entity_id = self._creds.get("idp_entity_id", _DEFAULT_ENTITY_ID)
                    current_url = await self._current_url()
                    shib_url = (
                        "https://auth.elsevier.com/ShibAuth/institutionLogin"
                        f"?entityID={quote(entity_id, safe='')}"
                        f"&appReturnURL={quote(current_url, safe='')}"
                    )
                    logger.info("SD wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled", "shib_url": shib_url}
            else:
                consecutive_clean += 1
                if consecutive_clean >= 8 and len(body_text) > 5000:
                    return {"access": "full_text"}

        return {"access": "unknown"}

    async def _wait_fulltext(self, timeout: int = 60) -> dict[str, Any]:
        tab = self._tab
        for i in range(timeout):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""
            if "preview-sidebar" not in html and "Section snippets" not in body_text and len(body_text) > 5000:
                logger.info("SD full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for SD full-text… %ds body=%d", i + 1, len(body_text))
        logger.warning("SD full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        """Fill HZAU CAS (Vue.js Element UI) login form, wait for SD redirect."""
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]

        logger.info("Filling HZAU CAS form for ScienceDirect…")

        # Wait for Vue.js to render the form
        await tab.sleep(1)

        # Username field — retry up to 3 times
        el = None
        for attempt in range(3):
            try:
                el = await tab.find("input[placeholder*='学工号']", timeout=3)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input[placeholder*='学号']", timeout=2)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input.el-input__inner[type='text']", timeout=2)
                break
            except Exception:
                pass
            if attempt < 2:
                await tab.sleep(1)
        if el:
            await el.click()
            await tab.sleep(0.3)
            await el.clear_input()
            await tab.sleep(0.2)
            await el.send_keys(username)
        else:
            logger.warning("CAS username field not found")

        # Password field — retry up to 3 times
        el = None
        for attempt in range(3):
            try:
                el = await tab.find("input[placeholder*='登录密码']", timeout=3)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input[placeholder*='密码']", timeout=2)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input.el-input__inner[type='password']", timeout=2)
                break
            except Exception:
                pass
            if attempt < 2:
                await tab.sleep(1)
        if el:
            await el.click()
            await tab.sleep(0.3)
            await el.clear_input()
            await tab.sleep(0.2)
            await el.send_keys(password)
        else:
            logger.warning("CAS password field not found")

        # Click login
        await tab.sleep(0.5)
        submitted = await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click();
                    return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)
        logger.debug("CAS submit: %s", submitted)

        # Wait for redirect to SD
        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "sciencedirect.com" in url:
                logger.info("CAS login successful, back at SD")
                await tab.sleep(3)
                return await self._wait_fulltext()

            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials", "authentication failed"]):
                return {"access": "login_failed", "error": body_text[:150]}

        return {"access": "login_timeout"}

    async def _click_access_through(self) -> str | None:
        """Fallback: click 'Access through your institution' link on SD page."""
        tab = self._tab
        try:
            clicked = await tab.evaluate("""
            (() => {
                let links = document.querySelectorAll('a, button');
                for (let el of links) {
                    let text = (el.innerText || el.textContent || '').toLowerCase();
                    if (text.includes('access through') || text.includes('institution')) {
                        el.click();
                        return 'clicked: ' + text.substring(0, 60);
                    }
                }
                for (let a of document.querySelectorAll('a')) {
                    if ((a.href || '').includes('ShibAuth') || (a.href || '').includes('institutionLogin')) {
                        a.click();
                        return 'clicked href';
                    }
                }
                return 'not found';
            })()
            """)
            logger.debug("Access through click: %s", clicked)
            if clicked == "not found":
                return None
            await tab.sleep(5)
            html = await self._get_html()
            matches = __import__('re').findall(r'https?://auth\.elsevier\.com/ShibAuth[^"\'\\s]+', html)
            if matches:
                return matches[0].replace("&amp;", "&")
            url = await self._current_url()
            matches = __import__('re').findall(r'https?://auth\.elsevier\.com/ShibAuth[^"\'\\s]+', url)
            if matches:
                return matches[0]
            return None
        except Exception as e:
            logger.debug("Click access through failed: %s", e)
            return None

    async def _current_url(self) -> str:
        result = await self._tab.evaluate("window.location.href")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")

    async def _get_html(self) -> str:
        result = await self._tab.evaluate("document.documentElement.outerHTML")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")


# ═══════════════════════════════════════════════════════════════════
# Wiley Online Library (also covers embopress.org / EMBO Press)
# ═══════════════════════════════════════════════════════════════════
@_register("wiley")
class _WileyLoginHandler:
    """Wiley: CARSI/Shibboleth SSO → HZAU IdP → CAS login."""

    def __init__(self, tab, creds: dict[str, str]):
        self._tab = tab
        self._creds = creds

    async def execute(self) -> dict[str, Any]:
        tab = self._tab

        detect_result = await self._detect_wall()
        if detect_result["access"] != "walled":
            return detect_result

        sso_url = detect_result.get("sso_url")
        if not sso_url:
            return {"access": "no_sso_url"}

        # Navigate SSO
        logger.info("Wiley walled — starting SSO login…")
        await tab.get(sso_url)
        await tab.sleep(5)

        current_url = await self._current_url()

        # Already back at Wiley?
        if "onlinelibrary.wiley.com" in current_url and "ssostart" not in current_url:
            logger.info("SSO session still valid, already back at Wiley")
            await tab.sleep(3)
            return await self._wait_fulltext()

        # CAS login page
        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        # HZAU IdP page
        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()

        # CARSI / Wiley institution selection page
        if "carsi" in current_url.lower() or "ds.carsi.edu" in current_url \
                or "wayf" in current_url.lower() or "ssostart" in current_url:
            return await self._search_institution()

        # Unknown
        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "wiley_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown Wiley login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        """Check if Wiley page is walled via Adobe Data Layer access field."""
        tab = self._tab
        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            # Wiley: "access":"no" in adobe data layer → walled
            walled = '"access":"no"' in html

            if walled:
                consecutive_clean = 0
                consecutive_walled += 1
                if consecutive_walled >= 5:
                    entity_id = self._creds.get("idp_entity_id", _DEFAULT_ENTITY_ID)
                    current_url = await self._current_url()
                    parsed = urlparse(current_url)
                    redirect_uri = parsed.path
                    if parsed.query:
                        redirect_uri += "?" + parsed.query

                    federation_id = "urn:mace:shibboleth:carsifed"
                    sso_url = (
                        "https://onlinelibrary.wiley.com/action/ssostart"
                        f"?idp={quote(entity_id, safe='')}"
                        f"&redirectUri={quote(redirect_uri, safe='')}"
                        f"&federationId={quote(federation_id, safe='')}"
                    )
                    logger.info("Wiley wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled", "sso_url": sso_url}
            else:
                consecutive_walled = 0
                consecutive_clean += 1
                if consecutive_clean >= 8 and len(body_text) > 5000:
                    return {"access": "full_text"}

        return {"access": "unknown"}

    async def _wait_fulltext(self, timeout: int = 60) -> dict[str, Any]:
        """Wait for full text to render (post-login)."""
        tab = self._tab
        for i in range(timeout):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""
            if '"access":"full"' in html and len(body_text) > 10000:
                logger.info("Wiley full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for Wiley full-text… %ds body=%d", i + 1, len(body_text))
        logger.warning("Wiley full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        """Fill the HZAU CAS (Vue.js Element UI) login form."""
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]

        logger.info("Filling HZAU CAS login form…")

        # Wait for Vue.js to render the form (intermittently slow)
        await tab.sleep(1)

        # Username field — retry up to 3 times (Vue.js may still be mounting)
        el = None
        for attempt in range(3):
            try:
                el = await tab.find("input[placeholder*='学工号']", timeout=3)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input[placeholder*='学号']", timeout=2)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input.el-input__inner[type='text']", timeout=2)
                break
            except Exception:
                pass
            if attempt < 2:
                await tab.sleep(1)

        if el:
            await el.click()
            await tab.sleep(0.3)
            await el.clear_input()
            await tab.sleep(0.2)
            await el.send_keys(username)
            logger.debug("CAS username entered")
        else:
            logger.warning("CAS username field not found")

        # Password field — retry up to 3 times
        el = None
        for attempt in range(3):
            try:
                el = await tab.find("input[placeholder*='登录密码']", timeout=3)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input[placeholder*='密码']", timeout=2)
                break
            except Exception:
                pass
            try:
                el = await tab.find("input.el-input__inner[type='password']", timeout=2)
                break
            except Exception:
                pass
            if attempt < 2:
                await tab.sleep(1)

        if el:
            await el.click()
            await tab.sleep(0.3)
            await el.clear_input()
            await tab.sleep(0.2)
            await el.send_keys(password)
            logger.debug("CAS password entered")
        else:
            logger.warning("CAS password field not found")

        # Click login button
        await tab.sleep(0.5)
        submitted = await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click();
                    return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)
        logger.debug("CAS submit: %s", submitted)

        # Wait for redirect back to Wiley
        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "onlinelibrary.wiley.com" in url and "ssostart" not in url:
                logger.info("CAS login successful, back at Wiley")
                await tab.sleep(3)
                return await self._wait_fulltext()

            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials", "authentication failed"]):
                return {"access": "login_failed", "error": body_text[:150]}

        return {"access": "login_timeout"}

    async def _handle_idp_page(self) -> dict[str, Any]:
        """Handle HZAU IdP page — wait for or trigger redirect to CAS."""
        tab = self._tab
        logger.info("On HZAU IdP page, waiting for CAS redirect…")
        for _ in range(10):
            await tab.sleep(1)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()
            if "onlinelibrary.wiley.com" in url and "ssostart" not in url:
                logger.info("Back at Wiley (IdP self-redirected)")
                return await self._wait_fulltext()

        # Try manually submitting the SAML form
        logger.debug("IdP didn't auto-redirect, trying manual form submit…")
        await tab.evaluate("""
        (() => {
            let form = document.querySelector('form');
            if (form) { form.submit(); return 'submitted'; }
            let btn = document.querySelector('input[type="submit"], button[type="submit"]');
            if (btn) { btn.click(); return 'clicked'; }
            return 'none';
        })()
        """)
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas" in url or "cas." in url:
            return await self._fill_cas_form()
        return {"access": "idp_redirect_failed"}

    async def _search_institution(self) -> dict[str, Any]:
        """Search for HZAU in Wiley/CARSI institution selection page."""
        tab = self._tab
        logger.info("Searching for HZAU in institution list…")

        # Find search box
        try:
            search_input = await tab.find("input.ms-inv", timeout=5)
        except Exception:
            try:
                search_input = await tab.find('input[placeholder*="Search By University"]', timeout=3)
            except Exception:
                try:
                    search_input = await tab.find('input[placeholder*="search" i]', timeout=3)
                except Exception:
                    logger.warning("Institution search box not found")
                    return {"access": "no_search_input"}

        await search_input.click()
        await tab.sleep(0.3)
        await search_input.send_keys("Huazhong")
        logger.debug("Typed 'Huazhong' into institution search")

        # Wait for dropdown and click HZAU
        for attempt in range(10):
            await tab.sleep(1)
            found = await tab.evaluate("""
            (() => {
                let items = document.querySelectorAll('span.sso-institution');
                for (let item of items) {
                    if ((item.innerText || item.textContent || '').includes('Huazhong')) {
                        item.click();
                        return 'clicked';
                    }
                }
                let links = document.querySelectorAll('a[href]');
                for (let link of links) {
                    if ((link.innerText || link.textContent || '').includes('Huazhong')) {
                        link.click();
                        return 'clicked link';
                    }
                }
                return 'waiting';
            })()
            """)
            if found and found.startswith("clicked"):
                logger.info("Clicked HZAU institution (%s)", found)
                break
        else:
            logger.warning("HZAU not found in institution dropdown")
            return {"access": "institution_not_found"}

        # Wait for redirect to CAS
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas.hzau.edu.cn" in url or "cas." in url:
            return await self._fill_cas_form()

        logger.debug("After institution click: %s", url[:120])
        return {"access": "redirect_unknown"}

    async def _current_url(self) -> str:
        result = await self._tab.evaluate("window.location.href")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")

    async def _get_html(self) -> str:
        result = await self._tab.evaluate("document.documentElement.outerHTML")
        if isinstance(result, list) and result:
            result = result[0]
        return str(result or "")
