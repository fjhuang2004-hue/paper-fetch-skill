"""RSC login flow reconnaissance — trace every URL in the CARSI/Shibboleth chain.

Usage (on Windows, from PowerShell):
    python D:/git/paper-fetch-skill/tests/cf_bypass/rsc_login_recon.py

Opens a browser, walks the RSC login flow step by step, printing URL changes.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

SRC = r"D:\git\paper-fetch-skill\src"
sys.path.insert(0, SRC)

os.environ["NODRIVER_USER_DATA_DIR"] = os.path.join(
    os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
    "nodriver_rsc_recon",
)

import nodriver as uc
from paper_fetch.config import DEFAULT_CHROME_EXE

ARTICLE_URL = "https://pubs.rsc.org/en/content/articlelanding/2026/cs/d5cs01021g"
LOGIN_PAGE = "https://pubs.rsc.org/en/account/logon"


async def _eval(tab, js: str) -> str:
    """Evaluate JS in tab, return string (empty on error/None)."""
    try:
        result = await tab.evaluate(js)
        if result is None:
            return ""
        if isinstance(result, list):
            return str(result[0]) if result else ""
        return str(result)
    except Exception:
        return ""


async def _eval_json(tab, js: str):
    """Evaluate JS that returns JSON.stringify, parse it."""
    raw = await _eval(tab, js)
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def recon():
    chrome_path = DEFAULT_CHROME_EXE

    print("=" * 70)
    print("RSC Login Reconnaissance")
    print("=" * 70)

    print("\nStarting Chrome...")
    browser = await uc.start(
        browser_executable_path=chrome_path,
        headless=False,
        sandbox=False,
    )
    await asyncio.sleep(2)

    try:
        tab = await browser.get(ARTICLE_URL)
    except Exception:
        await asyncio.sleep(1)
        tab = await browser.get(ARTICLE_URL)
    await tab.sleep(5)

    # ── Step 1: Article page ──
    print("=" * 40)
    print("[Step 1] Article page")
    url = await _eval(tab, "window.location.href")
    print("  URL:", url)

    html_str = await _eval(tab, "document.documentElement.outerHTML")
    print("  HTML:", len(html_str), "chars")

    for m in [
        "paywall__body", "auth-header__institute-access",
        "articleDenialBlock", "Access through your institution",
        "Log in via your home institution", "Find my institution",
        "Sign in to access", "View full article",
    ]:
        if m.lower() in html_str.lower():
            print("  WALL:", m)

    # Auth links on article page
    auth_links = await _eval_json(tab,
        "JSON.stringify(Array.from(document.querySelectorAll('a[href]'))"
        ".filter(function(a){var t=(a.textContent||'')+(a.href||'');"
        "return /login|logon|institution|shib|sign|carsi|federat/i.test(t);})"
        ".map(function(a){return [a.textContent.trim().slice(0,80), a.href];}))"
    )
    if auth_links:
        print("  Auth links:")
        for item in auth_links:
            if isinstance(item, list) and len(item) == 2:
                print("    [%s] -> %s" % (str(item[0])[:80], str(item[1])[:200]))

    # ── Step 2: Login page ──
    print("=" * 40)
    print("[Step 2] Login page")
    await tab.get(LOGIN_PAGE)
    await tab.sleep(5)
    url = await _eval(tab, "window.location.href")
    print("  URL:", url)

    html_str = await _eval(tab, "document.documentElement.outerHTML")
    print("  HTML:", len(html_str), "chars")

    # Save HTML
    try:
        out = Path(r"D:\Temp\rsc_login_page.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_str, encoding="utf-8", errors="ignore")
        print("  Saved:", str(out))
    except Exception:
        pass

    # Dump all links
    all_links = await _eval_json(tab,
        "JSON.stringify(Array.from(document.querySelectorAll('a[href]'))"
        ".filter(function(a){return a.href && !a.href.startsWith('javascript:');})"
        ".map(function(a){return [a.textContent.trim().slice(0,80), a.href];}))"
    )
    if all_links:
        print("  All links (%d):" % len(all_links))
        for item in all_links:
            if isinstance(item, list) and len(item) == 2:
                print("    [%s] -> %s" % (str(item[0])[:80], str(item[1])[:200]))

    # Body text
    body_str = await _eval(tab, "document.body ? document.body.innerText : ''")
    if body_str:
        print("  Body text (%d chars, first 2000):" % len(body_str))
        print(body_str[:2000])

    # ── Step 3: Manual login ──
    print("=" * 40)
    print("[Step 3] MANUAL LOGIN")
    print("  1. Find + click institutional login")
    print("  2. Select 'China CERNET Federation'")
    print("  3. Select 'Huazhong Agricultural University'")
    print("  4. Log in with CAS credentials")
    print()
    print("  After login SUCCEEDS, press Enter...")
    input()

    url = await _eval(tab, "window.location.href")
    html_str = await _eval(tab, "document.documentElement.outerHTML")

    print("  Final URL:", url)
    print("  HTML:", len(html_str), "chars")
    print("  #wrapper:", "#wrapper" in html_str)
    print("  References:", "References" in html_str)
    print("  image_table count:", html_str.count("image_table"))

    try:
        out = Path(r"D:\Temp\rsc_after_login.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_str, encoding="utf-8", errors="ignore")
        print("  Saved:", str(out))
    except Exception:
        pass

    await asyncio.sleep(1)
    browser.stop()
    print("\n[DONE]")


if __name__ == "__main__":
    asyncio.run(recon())
