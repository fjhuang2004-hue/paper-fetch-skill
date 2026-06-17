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


    @staticmethod
    def _is_rsc_domain(url: str) -> bool:
        """Check if url is on any RSC domain (pubs, www, shib)."""
        return any(d in url for d in (
            "pubs.rsc.org", "www.rsc.org", "shib.rsc.org"
        ))

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

        # Already back at ScienceDirect / Cell?
        if "sciencedirect.com" in current_url or "cell.com" in current_url:
            logger.info("SSO session still valid, already back at publisher")
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
                if "sciencedirect.com" in url or "cell.com" in url:
                    logger.info("Back at publisher (IdP self-redirected)")
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
            current_url = await self._current_url()
            is_cell = "cell.com" in current_url.lower()

            # ScienceDirect wall markers + Cell.com wall markers
            walled = ("preview-sidebar" in html or "Section snippets" in body_text
                      or "Article preview" in body_text
                      or "articleDenialBlock" in html
                      or "Access through your institution" in body_text)
            # False-positive guard: "You have full access" means already logged in
            if walled and "You have full access" in body_text:
                walled = False

            if walled:
                consecutive_clean = 0
                if attempt >= 5:
                    entity_id = self._creds.get("idp_entity_id", _DEFAULT_ENTITY_ID)
                    if is_cell:
                        # cell.com uses Atypon SSO (/action/ssostart)
                        parsed = urlparse(current_url)
                        redirect_uri = parsed.path
                        if parsed.query:
                            redirect_uri += "?" + parsed.query
                        federation_id = "urn:mace:shibboleth:carsifed"
                        sso_url = (
                            "https://www.cell.com/action/ssostart"
                            f"?idp={quote(entity_id, safe='')}"
                            f"&redirectUri={quote(redirect_uri, safe='')}"
                            f"&federationId={quote(federation_id, safe='')}"
                        )
                        logger.info("Cell wall confirmed (after %ds)", attempt + 1)
                        return {"access": "walled", "shib_url": sso_url}
                    else:
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
            current_url = await self._current_url()
            is_cell = "cell.com" in current_url.lower()

            if is_cell:
                # cell.com: no denial block + substantial body text
                has_denial = "articleDenialBlock" in html
                if not has_denial and len(body_text) > 8000:
                    logger.info("Cell full-text rendered after %ds", i + 1)
                    return {"access": "full_text"}
            else:
                if "preview-sidebar" not in html and "Section snippets" not in body_text and len(body_text) > 5000:
                    logger.info("SD full-text rendered after %ds", i + 1)
                    return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for full-text… %ds body=%d", i + 1, len(body_text))
        logger.warning("Full-text render timed out")
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

        # Wait for redirect to SD / Cell
        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "sciencedirect.com" in url or "cell.com" in url:
                logger.info("CAS login successful, back at publisher")
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


# ═══════════════════════════════════════════════════════════════════
# PNAS (Proceedings of the National Academy of Sciences)
# ═══════════════════════════════════════════════════════════════════
@_register("pnas")
class _PnasLoginHandler:
    """PNAS: Atypon-based CARSI/Shibboleth SSO → HZAU IdP → CAS login."""

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

        logger.info("PNAS walled — starting SSO login…")
        await tab.get(sso_url)
        await tab.sleep(5)

        current_url = await self._current_url()

        if "pnas.org" in current_url and "ssostart" not in current_url:
            logger.info("SSO session still valid, already back at PNAS")
            await tab.sleep(3)
            return await self._wait_fulltext()

        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()

        if "carsi" in current_url.lower() or "ds.carsi.edu" in current_url \
                or "wayf" in current_url.lower() or "ssostart" in current_url:
            return await self._search_institution()

        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "pnas_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown PNAS login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        tab = self._tab
        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            walled = ("article_abstractPage" in html
                      or '"access":"no"' in html
                      or "access-denials__wrapper" in html
                      or "accessDenialWidget" in html)

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
                        "https://www.pnas.org/action/ssostart"
                        f"?idp={quote(entity_id, safe='')}"
                        f"&redirectUri={quote(redirect_uri, safe='')}"
                        f"&federationId={quote(federation_id, safe='')}"
                    )
                    logger.info("PNAS wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled", "sso_url": sso_url}
            else:
                consecutive_walled = 0
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
            if "article_fullPage" in html and len(body_text) > 8000:
                logger.info("PNAS full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for PNAS full-text… %ds body=%d", i + 1, len(body_text))
        logger.warning("PNAS full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]
        logger.info("Filling HZAU CAS login form (PNAS)…")
        await tab.sleep(1)

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(username)
        else:
            logger.warning("CAS username field not found")

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(password)
        else:
            logger.warning("CAS password field not found")

        await tab.sleep(0.5)
        await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click(); return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)

        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "pnas.org" in url and "ssostart" not in url:
                logger.info("CAS login successful, back at PNAS")
                await tab.sleep(3)
                return await self._wait_fulltext()
            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials"]):
                return {"access": "login_failed", "error": body_text[:150]}
        return {"access": "login_timeout"}

    async def _handle_idp_page(self) -> dict[str, Any]:
        tab = self._tab
        logger.info("On HZAU IdP page, waiting for CAS redirect…")
        for _ in range(10):
            await tab.sleep(1)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()
            if "pnas.org" in url and "ssostart" not in url:
                logger.info("Back at PNAS (IdP self-redirected)")
                return await self._wait_fulltext()
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
        tab = self._tab
        logger.info("Searching for HZAU in institution list…")
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
        await search_input.click(); await tab.sleep(0.3)
        await search_input.send_keys("Huazhong")
        for attempt in range(10):
            await tab.sleep(1)
            found = await tab.evaluate("""
            (() => {
                let items = document.querySelectorAll('span.sso-institution');
                for (let item of items) {
                    if ((item.innerText || item.textContent || '').includes('Huazhong')) {
                        item.click(); return 'clicked';
                    }
                }
                let links = document.querySelectorAll('a[href]');
                for (let link of links) {
                    if ((link.innerText || link.textContent || '').includes('Huazhong')) {
                        link.click(); return 'clicked link';
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
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas.hzau.edu.cn" in url or "cas." in url:
            return await self._fill_cas_form()
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
# Taylor & Francis (tandfonline.com)
# ═══════════════════════════════════════════════════════════════════
@_register("tandf")
class _TandfLoginHandler:
    """T&F: Atypon-based CARSI/Shibboleth SSO → HZAU IdP → CAS login."""

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

        logger.info("T&F walled — starting SSO login…")
        await tab.get(sso_url)
        await tab.sleep(5)

        current_url = await self._current_url()

        if "tandfonline.com" in current_url and "ssostart" not in current_url:
            logger.info("SSO session still valid, already back at T&F")
            await tab.sleep(3)
            return await self._wait_fulltext()

        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()

        if "carsi" in current_url.lower() or "ds.carsi.edu" in current_url \
                or "wayf" in current_url.lower() or "ssostart" in current_url:
            return await self._search_institution()

        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "tandf_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown T&F login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        tab = self._tab
        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            walled = ("article_abstractPage" in html
                      or '"access":"no"' in html
                      or "access-denials__wrapper" in html
                      or "accessDenialWidget" in html)

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
                        "https://www.tandfonline.com/action/ssostart"
                        f"?idp={quote(entity_id, safe='')}"
                        f"&redirectUri={quote(redirect_uri, safe='')}"
                        f"&federationId={quote(federation_id, safe='')}"
                    )
                    logger.info("T&F wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled", "sso_url": sso_url}
            else:
                consecutive_walled = 0
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
            if "article_fullPage" in html and len(body_text) > 8000:
                logger.info("T&F full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if "hlFld-Fulltext" in html and len(body_text) > 5000:
                logger.info("T&F full-text (hlFld) rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for T&F full-text… %ds body=%d", i + 1, len(body_text))
        logger.warning("T&F full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]
        logger.info("Filling HZAU CAS login form (T&F)…")
        await tab.sleep(1)

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(username)
        else:
            logger.warning("CAS username field not found")

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(password)
        else:
            logger.warning("CAS password field not found")

        await tab.sleep(0.5)
        await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click(); return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)

        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "tandfonline.com" in url and "ssostart" not in url:
                logger.info("CAS login successful, back at T&F")
                await tab.sleep(3)
                return await self._wait_fulltext()
            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials"]):
                return {"access": "login_failed", "error": body_text[:150]}
        return {"access": "login_timeout"}

    async def _handle_idp_page(self) -> dict[str, Any]:
        tab = self._tab
        logger.info("On HZAU IdP page, waiting for CAS redirect…")
        for _ in range(10):
            await tab.sleep(1)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()
            if "tandfonline.com" in url and "ssostart" not in url:
                logger.info("Back at T&F (IdP self-redirected)")
                return await self._wait_fulltext()
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
        tab = self._tab
        logger.info("Searching for HZAU in institution list…")
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
        await search_input.click(); await tab.sleep(0.3)
        await search_input.send_keys("Huazhong")
        for attempt in range(10):
            await tab.sleep(1)
            found = await tab.evaluate("""
            (() => {
                let items = document.querySelectorAll('span.sso-institution');
                for (let item of items) {
                    if ((item.innerText || item.textContent || '').includes('Huazhong')) {
                        item.click(); return 'clicked';
                    }
                }
                let links = document.querySelectorAll('a[href]');
                for (let link of links) {
                    if ((link.innerText || link.textContent || '').includes('Huazhong')) {
                        link.click(); return 'clicked link';
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
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas.hzau.edu.cn" in url or "cas." in url:
            return await self._fill_cas_form()
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
# Oxford Academic / Oxford University Press (academic.oup.com)
# ═══════════════════════════════════════════════════════════════════
@_register("oxfordacademic")
class _OxfordAcademicLoginHandler:
    """OUP: Silverchair/Shibboleth SSO → HZAU IdP → CAS login.

    OUP uses a JS-triggered Shibboleth flow via the
    ``.js-shibboleth-action`` button rather than a direct SSO URL.
    """

    def __init__(self, tab, creds: dict[str, str]):
        self._tab = tab
        self._creds = creds

    async def execute(self) -> dict[str, Any]:
        tab = self._tab

        detect_result = await self._detect_wall()
        if detect_result["access"] != "walled":
            return detect_result

        # Navigate to /sign-in page and click institutional login
        logger.info("OUP walled — navigating to /sign-in…")
        await tab.get("https://academic.oup.com/sign-in")
        await tab.sleep(3)

        # Click "Sign in through your institution" on the sign-in page
        clicked = await self._click_institution_login()
        if not clicked:
            return {"access": "no_institution_button"}

        await tab.sleep(5)
        current_url = await self._current_url()

        # Already back at OUP? (SSO session still valid)
        if "academic.oup.com" in current_url:
            logger.info("SSO session still valid, already back at OUP")
            await tab.sleep(3)
            return await self._wait_fulltext()

        # CAS login page
        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        # HZAU IdP page
        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()

        # CARSI / Shibboleth / WAYF institution selection page
        if "carsi" in current_url.lower() or "ds.carsi.edu" in current_url \
                or "wayf" in current_url.lower() or "idp" in current_url.lower() \
                or "shibboleth" in current_url.lower():
            return await self._search_institution()

        # Sign-in page — try clicking institution again or search
        if "sign-in" in current_url or "signin" in current_url:
            return await self._search_institution()

        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "oup_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown OUP login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        """Check if OUP page is walled (abstract-only / login=false)."""
        tab = self._tab
        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            walled = ("subscription-needed" in html
                      or "noAccessReveal" in html
                      or "NeedSubscription" in html
                      or "This Feature Is Available To Subscribers Only" in body_text
                      or "This PDF is available to Subscribers Only" in body_text)

            if walled:
                consecutive_clean = 0
                consecutive_walled += 1
                if consecutive_walled >= 3:
                    logger.info("OUP wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled"}
            else:
                consecutive_walled = 0
                consecutive_clean += 1
                # OUP full-text: article body with substantial content
                if consecutive_clean >= 8 and len(body_text) > 8000:
                    return {"access": "full_text"}

        return {"access": "unknown"}

    async def _click_institution_login(self) -> bool:
        """Click the 'Sign in through your institution' Shibboleth button."""
        tab = self._tab
        try:
            result = await tab.evaluate("""
            (() => {
                // Try the shibboleth-action button first
                let btn = document.querySelector('.js-shibboleth-action, [class*="shibboleth-action"]');
                if (btn) { btn.click(); return 'clicked shib'; }
                // Try the institutional sign-in button
                btn = document.querySelector('.at-institutional-sign-in, [class*="institutional-sign-in"]');
                if (btn) { btn.click(); return 'clicked inst'; }
                // Try any link with "institution" text
                let links = document.querySelectorAll('a, button');
                for (let el of links) {
                    let text = (el.innerText || el.textContent || '').toLowerCase();
                    if (text.includes('institution') || text.includes('shibboleth')) {
                        el.click(); return 'clicked link: ' + text.substring(0, 40);
                    }
                }
                // Fallback: navigate to /sign-in
                return 'not found';
            })()
            """)
            logger.debug("Institution login click: %s", result)
            return result and "not found" not in str(result)
        except Exception as e:
            logger.debug("Click institution login failed: %s", e)
            return False

    async def _wait_fulltext(self, timeout: int = 60) -> dict[str, Any]:
        """Wait for full text to render (post-login).

        OUP keeps ``subscription-needed`` divs in the page template even
        after login, so we check for login=true in URL and article-body
        content presence instead.
        """
        tab = self._tab
        for i in range(timeout):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""
            url = await self._current_url()
            # login=true in URL indicates successful institutional login
            if "login=true" in url and len(body_text) > 10000:
                logger.info("OUP full-text rendered after %ds (login=true)", i + 1)
                return {"access": "full_text"}
            # Fallback: article-body content present
            if "article-body" in html and len(body_text) > 15000:
                logger.info("OUP full-text rendered after %ds (article-body)", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for OUP full-text… %ds body=%d url=%s",
                             i + 1, len(body_text), url[:80])
        logger.warning("OUP full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]
        logger.info("Filling HZAU CAS login form (OUP)…")
        await tab.sleep(1)

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(username)
        else:
            logger.warning("CAS username field not found")

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(password)
        else:
            logger.warning("CAS password field not found")

        await tab.sleep(0.5)
        await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click(); return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)

        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "academic.oup.com" in url:
                logger.info("CAS login successful, back at OUP")
                await tab.sleep(3)
                return await self._wait_fulltext()
            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials"]):
                return {"access": "login_failed", "error": body_text[:150]}
        return {"access": "login_timeout"}

    async def _handle_idp_page(self) -> dict[str, Any]:
        tab = self._tab
        logger.info("On HZAU IdP page, waiting for CAS redirect…")
        for _ in range(10):
            await tab.sleep(1)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()
            if "academic.oup.com" in url:
                logger.info("Back at OUP (IdP self-redirected)")
                return await self._wait_fulltext()
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
        tab = self._tab
        logger.info("Searching for HZAU in institution list…")

        # OUP / Shibboleth WAYF may have a different search pattern
        # Try clicking "search for your institution" first if needed
        try:
            search_input = await tab.find("input.ms-inv", timeout=5)
        except Exception:
            try:
                search_input = await tab.find('input[placeholder*="Search By University"]', timeout=3)
            except Exception:
                try:
                    search_input = await tab.find('input[placeholder*="search" i]', timeout=3)
                except Exception:
                    try:
                        search_input = await tab.find('input[type="text"]', timeout=3)
                    except Exception:
                        logger.warning("Institution search box not found")
                        return {"access": "no_search_input"}

        await search_input.click(); await tab.sleep(0.3)
        await search_input.send_keys("Huazhong")

        for attempt in range(10):
            await tab.sleep(1)
            found = await tab.evaluate("""
            (() => {
                // sso-institution spans (Atypon/CARSI style)
                let items = document.querySelectorAll('span.sso-institution');
                for (let item of items) {
                    if ((item.innerText || item.textContent || '').includes('Huazhong')) {
                        item.click(); return 'clicked';
                    }
                }
                // Shibboleth/OUP style links
                let links = document.querySelectorAll('a[href], button, .institution-item, .idp-item, [role="option"]');
                for (let link of links) {
                    if ((link.innerText || link.textContent || '').includes('Huazhong')) {
                        link.click(); return 'clicked link';
                    }
                }
                // Try any clickable with the text
                let all = document.querySelectorAll('*');
                for (let el of all) {
                    let t = (el.innerText || el.textContent || '');
                    if (t.trim() === 'Huazhong Agricultural University' && el.click) {
                        el.click(); return 'clicked exact';
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

        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas.hzau.edu.cn" in url or "cas." in url:
            return await self._fill_cas_form()
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
# Springer Nature (nature.com / link.springer.com)
# ═══════════════════════════════════════════════════════════════════
@_register("springer")
class _SpringerLoginHandler:
    """Springer Nature: WAYF/Shibboleth SSO → HZAU IdP → CAS login.

    Nature.com uses ``wayf.springernature.com`` as its institutional
    login hub (WAYF = Where Are You From).  The flow is:

    1. Detect paywall on the article page.
    2. Navigate to ``wayf.springernature.com?redirect_uri=...``
    3. Search for HZAU → Shibboleth → IdP → CAS → back to nature.com.
    """

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

        logger.info("Springer Nature walled — starting WAYF login…")
        await tab.get(sso_url)
        await tab.sleep(5)

        current_url = await self._current_url()

        if "nature.com" in current_url and "wayf" not in current_url:
            logger.info("SSO session still valid, already back at Nature")
            await tab.sleep(3)
            return await self._wait_fulltext()

        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()

        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()

        if "wayf" in current_url.lower() or "shibboleth" in current_url.lower() \
                or "carsi" in current_url.lower() or "ssostart" in current_url:
            return await self._search_institution()

        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "springer_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown Springer Nature login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        """Check if Springer Nature page is walled (abstract-only)."""
        tab = self._tab
        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""

            # Nature wall indicators:
            # - "Access through your institution" link present
            # - "Subscribe to journal" or "Rent or Buy" present
            # - paywall-container or access-denied elements
            # NOT walled:
            # - "You have full access" present
            # - OA badge present
            # - Full article body with c-article-body > main-content
            walled = (
                "Access through your institution" in body_text
                or "Subscribe to journal" in body_text
                or "Rent or Buy" in body_text
                or "paywall-container" in html
                or "article-paywall" in html
                or "Access options:" in body_text
            )

            # False-positive guard: "You have full access" means already logged in
            if walled and "You have full access" in body_text:
                walled = False

            if walled:
                consecutive_clean = 0
                consecutive_walled += 1
                if consecutive_walled >= 5:
                    current_url = await self._current_url()
                    sso_url = (
                        "https://wayf.springernature.com/"
                        f"?redirect_uri={quote(current_url, safe='')}"
                    )
                    logger.info("Springer Nature wall confirmed (after %ds)", attempt + 1)
                    return {"access": "walled", "sso_url": sso_url}
            else:
                consecutive_walled = 0
                # Full text indicators: has article body with substantial content
                has_body = (
                    "c-article-body" in html
                    or "main-content" in html
                )
                if has_body and len(body_text) > 5000:
                    consecutive_clean += 1
                    if consecutive_clean >= 8:
                        return {"access": "full_text"}

        return {"access": "unknown"}

    async def _wait_fulltext(self, timeout: int = 60) -> dict[str, Any]:
        """Wait for full text to render (post-login)."""
        tab = self._tab
        for i in range(timeout):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = await tab.evaluate("document.body ? document.body.innerText : ''") or ""
            # Full text: has article body with substantial content, no paywall
            has_body = "c-article-body" in html or "main-content" in html
            no_paywall = (
                "Access through your institution" not in body_text
                or "You have full access" in body_text
            )
            if has_body and no_paywall and len(body_text) > 10000:
                logger.info("Springer Nature full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug("Waiting for Springer Nature full-text… %ds body=%d",
                             i + 1, len(body_text))
        logger.warning("Springer Nature full-text render timed out")
        return {"access": "render_timeout"}

    async def _fill_cas_form(self) -> dict[str, Any]:
        """Fill the HZAU CAS (Vue.js Element UI) login form."""
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]
        logger.info("Filling HZAU CAS login form (Springer Nature)…")
        await tab.sleep(1)

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(username)
        else:
            logger.warning("CAS username field not found")

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
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(password)
        else:
            logger.warning("CAS password field not found")

        await tab.sleep(0.5)
        await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click(); return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)

        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if "nature.com" in url or "springer.com" in url:
                logger.info("CAS login successful, back at Springer Nature")
                await tab.sleep(3)
                return await self._wait_fulltext()
            body_text = await tab.evaluate("document.body ? document.body.innerText.substring(0,300) : ''") or ""
            if any(w in body_text for w in ["用户名或密码错误", "密码错误", "账号不存在",
                                              "认证失败", "Invalid credentials"]):
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
            if "nature.com" in url or "springer.com" in url:
                logger.info("Back at Springer Nature (IdP self-redirected)")
                return await self._wait_fulltext()
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

    async def _accept_cookies(self) -> None:
        """Dismiss the cookie consent banner if present.

        Springer Nature shows a TCF consent overlay on first visit
        (especially with a fresh Chrome profile).  The banner blocks
        interaction with the underlying form, so it must be dismissed
        before we can type into the search input.
        """
        tab = self._tab
        try:
            await tab.sleep(2)
            await tab.evaluate("""
            (() => {
                // "Accept all cookies" button variants
                let buttons = document.querySelectorAll('button');
                for (let b of buttons) {
                    let text = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (text.includes('accept all') || text.includes('accept')
                        || text === 'ok' || text.includes('agree')) {
                        b.click(); return 'clicked: ' + text;
                    }
                }
                // "Save" or "Confirm" or "Continue" without accepting
                for (let b of buttons) {
                    let text = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (text.includes('save') || text.includes('confirm')
                        || text.includes('continue') || text.includes('reject all')
                        || text === 'close') {
                        b.click(); return 'clicked: ' + text;
                    }
                }
                return 'no button found';
            })()
            """)
        except Exception:
            pass

    async def _search_institution(self) -> dict[str, Any]:
        """Navigate directly to the search results URL.

        The WAYF form uses ``GET``, so we can bypass the autocomplete
        entirely by navigating to
        ``/?redirect_uri=...&search=Huazhong``.
        """
        tab = self._tab
        logger.info("Navigating to WAYF search results for HZAU…")

        current_url = await self._current_url()
        search_url = current_url + ("&" if "?" in current_url else "?") + "search=Huazhong"
        await tab.get(search_url)
        await tab.sleep(5)

        # ── Click HZAU in the search results ──

        # ── Step 3: Click HZAU in the autocomplete modal ──
        for attempt in range(12):
            await tab.sleep(1)
            found = await tab.evaluate("""
            (() => {
                // autocomplete modal / dropdown items
                for (let sel of [
                    '[data-test="autocomplete-item"]',
                    '[data-test="search-result"]',
                    '.autocomplete-item', '.autocomplete-result',
                    '.search-result', '.result-item',
                    '[role="option"]', '[role="listbox"] li',
                    '.eds-c-autocomplete__item', '.eds-autocomplete-item',
                    '.modal a', '.modal button', '.modal li',
                ]) {
                    try {
                        let items = document.querySelectorAll(sel);
                        for (let item of items) {
                            let text = (item.innerText || item.textContent || '').toLowerCase();
                            if (text.includes('huazhong') || text.includes('华中')) {
                                item.click(); return 'clicked: ' + text.substring(0, 60);
                            }
                        }
                    } catch(e) {}
                }
                // Any clickable with matching text (broad fallback)
                let all = document.querySelectorAll('a[href], button, li, td, span, div[onclick]');
                for (let el of all) {
                    let text = (el.innerText || el.textContent || '');
                    if ((text.includes('Huazhong') || text.includes('华中'))
                        && text.length < 300 && typeof el.click === 'function') {
                        el.click(); return 'clicked broad: ' + text.trim().substring(0, 60);
                    }
                }
                return 'waiting';
            })()
            """)
            if found and found.startswith("clicked"):
                logger.info("Selected HZAU (%s)", found)
                break
            if found and found != "waiting":
                logger.debug("WAYF search %d: %s", attempt + 1, found)
        else:
            html = await self._get_html()
            debug_path = Path(os.environ.get("TEMP", "/tmp")) / "springer_wayf_results_debug.html"
            debug_path.write_text(html, encoding="utf-8", errors="ignore")
            logger.warning("HZAU not found in autocomplete results, saved to %s", debug_path)
            return {"access": "institution_not_found", "debug_html": str(debug_path)}

        # ── Step 4: Wait for Shibboleth → IdP → CAS ──
        await tab.sleep(5)
        url = await self._current_url()
        if "cas-paas.hzau.edu.cn" in url or "cas." in url:
            return await self._fill_cas_form()
        if "idp.hzau.edu.cn" in url:
            return await self._handle_idp_page()
        if "nature.com" in url or "springer.com" in url:
            logger.info("Already back at publisher after institution selection")
            await tab.sleep(3)
            return await self._wait_fulltext()

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
# RSC (Royal Society of Chemistry) — pubs.rsc.org
# ═══════════════════════════════════════════════════════════════════
@_register("rsc")
class _RscLoginHandler:
    """RSC: /rsc-id/account/checkfederatedaccess → Shibboleth → CAS."""

    _PLATFORM_ID = "1c576962-b994-4139-a186-8120433be7b7"

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
        logger.info("RSC walled — starting federated access login…")
        await tab.get(sso_url)
        await tab.sleep(5)
        current_url = await self._current_url()
        if ("pubs.rsc.org" in current_url or "www.rsc.org" in current_url) and "checkfederatedaccess" not in current_url:
            logger.info("SSO session still valid, already back at RSC")
            await tab.sleep(3)
            return await self._wait_fulltext()
        if "cas-paas.hzau.edu.cn" in current_url or "cas." in current_url:
            return await self._fill_cas_form()
        if "idp.hzau.edu.cn" in current_url:
            return await self._handle_idp_page()
        if ("shibboleth" in current_url.lower()
                or "carsi" in current_url.lower()
                or "federat" in current_url.lower()):
            logger.info("At Shibboleth/federation page, waiting for redirect…")
            for _ in range(30):
                await tab.sleep(2)
                url = await self._current_url()
                if "idp.hzau.edu.cn" in url:
                    return await self._handle_idp_page()
                if "cas-paas" in url or "cas." in url:
                    return await self._fill_cas_form()
                if _is_rsc_domain(url) and "checkfederatedaccess" not in url:
                        return await self._wait_fulltext()
            return {"access": "shibboleth_redirect_timeout"}
        html = await self._get_html()
        debug_path = Path(os.environ.get("TEMP", "/tmp")) / "rsc_login_debug.html"
        debug_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Unknown RSC login page, saved to %s", debug_path)
        return {"access": "unknown_idp_page", "debug_html": str(debug_path)}

    async def _detect_wall(self) -> dict[str, Any]:
        """Check if RSC page is walled.

        Uses the JSON-LD ``isAccessibleForFree`` field embedded in the
        article landing page — ``False`` = paywalled, ``True`` = OA.
        This is far more reliable than body-text heuristics because
        the wall UI is loaded asynchronously and the footer navigation
        contains "Open Access" links on every page.
        """
        tab = self._tab
        consecutive_clean = 0
        consecutive_walled = 0

        for attempt in range(60):
            await tab.sleep(1)
            html = await self._get_html()

            # RSC embeds isAccessibleForFree in JSON-LD on landing pages.
            # "False" = non-OA (paywalled); "True" = OA (freely accessible).
            import re as _re
            free_match = _re.search(
                r'isAccessibleForFree["\'"]?\s*:\s*["\'"]?(\w+)', html
            )
            if free_match:
                value = free_match.group(1).lower()
                if value == "false":
                    consecutive_clean = 0
                    consecutive_walled += 1
                    if consecutive_walled >= 5:
                        current_url = await self._current_url()
                        entity_id = self._creds.get(
                            "idp_entity_id",
                            "https://idp.hzau.edu.cn/idp/shibboleth",
                        )
                        from urllib.parse import quote as _uq
                        sso_url = (
                            "https://www.rsc.org/rsc-id/account/"
                            "checkfederatedaccess"
                            f"?instituteurl={_uq(entity_id, safe='')}"
                            f"&returnurl={_uq(current_url, safe='')}"
                            f"&platformID={self._PLATFORM_ID}"
                        )
                        logger.info(
                            "RSC wall confirmed (after %ds)", attempt + 1
                        )
                        return {"access": "walled", "sso_url": sso_url}
                elif value == "true":
                    consecutive_walled = 0
                    consecutive_clean += 1
                    if consecutive_clean >= 3:
                        return {"access": "full_text"}
            else:
                # No JSON-LD (e.g. articlehtml full-text page after login
                # or direct full-text endpoint).  Check for body content.
                consecutive_walled = 0
                body_text = (
                    await tab.evaluate(
                        "document.body ? document.body.innerText : ''"
                    )
                ) or ""
                has_body = (
                    "article-control" in html
                    or 'id="wrapper"' in html
                )
                if has_body and len(body_text) > 8000:
                    consecutive_clean += 1
                    if consecutive_clean >= 25:
                        return {"access": "full_text"}
        return {"access": "unknown"}

    async def _wait_fulltext(self, timeout: int = 60) -> dict[str, Any]:
        """Wait for full text to render (post-login return to RSC).

        After CAS login the browser lands back on the article page, but
        the page still shows the abstract-only view.  Reload once so the
        server sees the fresh Shibboleth session and serves the full-text
        tab content (loaded via AJAX into ``#pnlArticleContentLoaded``).

        Note: RSC's ``paywall__body`` div persists in the DOM even after
        authentication — it is never removed.  We detect full-text by
        looking for substantial body content rather than the absence of
        the paywall marker.
        """
        tab = self._tab
        url = await self._current_url()

        # Reload now that auth cookies are present.
        if "pubs.rsc.org" in url:
            logger.info("Reloading RSC article page with auth session\u2026")
            await tab.get(url)
            await tab.sleep(5)

        for i in range(timeout):
            await tab.sleep(1)
            html = await self._get_html()
            body_text = (
                await tab.evaluate("document.body ? document.body.innerText : ''")
            ) or ""
            # After successful login, the AJAX-loaded full text has
            # substantial body text with article sections.
            if len(body_text) > 15000:
                logger.info("RSC full-text rendered after %ds", i + 1)
                return {"access": "full_text"}
            if i % 15 == 14:
                logger.debug(
                    "Waiting for RSC full-text\u2026 %ds body=%d",
                    i + 1, len(body_text),
                )
        logger.warning("RSC full-text render timed out")
        return {"access": "render_timeout"}
    async def _fill_cas_form(self) -> dict[str, Any]:
        tab = self._tab
        username = self._creds["username"]
        password = self._creds["password"]
        logger.info("Filling HZAU CAS login form (RSC)…")
        await tab.sleep(1)
        el = None
        for attempt in range(3):
            for sel in [
                "input[placeholder*='学工号']",
                "input[placeholder*='学号']",
                "input.el-input__inner[type='text']",
            ]:
                try:
                    el = await tab.find(sel, timeout=3)
                    break
                except Exception:
                    pass
            if el:
                break
            if attempt < 2:
                await tab.sleep(1)
        if el:
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(username)
        else:
            logger.warning("CAS username field not found")
        el = None
        for attempt in range(3):
            for sel in [
                "input[placeholder*='登录密码']",
                "input[placeholder*='密码']",
                "input.el-input__inner[type='password']",
            ]:
                try:
                    el = await tab.find(sel, timeout=3)
                    break
                except Exception:
                    pass
            if el:
                break
            if attempt < 2:
                await tab.sleep(1)
        if el:
            await el.click(); await tab.sleep(0.3)
            await el.clear_input(); await tab.sleep(0.2)
            await el.send_keys(password)
        else:
            logger.warning("CAS password field not found")
        await tab.sleep(0.5)
        await tab.evaluate("""
        (() => {
            let buttons = document.querySelectorAll('button, input[type="submit"], a');
            for (let btn of buttons) {
                let text = (btn.innerText || btn.textContent || btn.value || '').trim();
                if (text === '登录' || text === 'Login' || text === '登 录') {
                    btn.click(); return 'clicked: ' + text;
                }
            }
            let form = document.getElementById('fm1');
            if (form) { form.submit(); return 'submitted fm1'; }
            form = document.querySelector('form[method="post"]');
            if (form) { form.submit(); return 'submitted post form'; }
            return 'no button found';
        })()
        """)
        # Wait for CAS to process login and start redirecting.
        await tab.sleep(5)
        for i in range(30):
            await tab.sleep(2)
            url = await self._current_url()
            if _is_rsc_domain(url) and "checkfederatedaccess" not in url:
                logger.info("CAS login successful, back at RSC")
                await tab.sleep(3)
                return await self._wait_fulltext()
            body_text = (
                await tab.evaluate(
                    "document.body ? document.body.innerText.substring(0,300) : ''"
                )
            ) or ""
            if any(w in body_text for w in [
                "用户名或密码错误", "密码错误", "账号不存在",
                "认证失败", "Invalid credentials",
            ]):
                return {"access": "login_failed", "error": body_text[:150]}
        return {"access": "login_timeout"}

    async def _handle_idp_page(self) -> dict[str, Any]:
        tab = self._tab
        logger.info("On HZAU IdP page, waiting for CAS redirect…")
        for _ in range(10):
            await tab.sleep(1)
            url = await self._current_url()
            if "cas-paas" in url or "cas." in url:
                return await self._fill_cas_form()
            if _is_rsc_domain(url):
                logger.info("Back at RSC (IdP self-redirected)")
                return await self._wait_fulltext()
        logger.debug("IdP did not auto-redirect, trying manual form submit…")
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
