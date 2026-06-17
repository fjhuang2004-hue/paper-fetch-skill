"""RSC articlehtml fetch — login then navigate to articlehtml endpoint and save HTML.

Usage (on Windows):
    python D:/git/paper-fetch-skill/tests/cf_bypass/rsc_articlehtml_fetch.py
"""

import asyncio
import os
import sys
import time
import pathlib

SRC = r"D:\git\paper-fetch-skill\src"
sys.path.insert(0, SRC)

os.environ["NODRIVER_USER_DATA_DIR"] = os.path.join(
    os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
    "nodriver_rsc_articlehtml",
)

from paper_fetch.providers._nodriver_fetch import (
    _try_once_keep_alive,
    _stop_browser_safely,
)
from paper_fetch.config import DEFAULT_CHROME_EXE

LANDING_URL = "https://pubs.rsc.org/en/content/articlelanding/2022/gc/d2gc00698g"
ARTICLEHTML_URL = "https://pubs.rsc.org/en/content/articlehtml/2022/gc/d2gc00698g"
PUBLISHER = "rsc"


async def main():
    chrome_path = DEFAULT_CHROME_EXE
    user_data_dir = os.environ["NODRIVER_USER_DATA_DIR"]

    print("=" * 70)
    print("RSC ArticleHTML Fetch (login first, then navigate)")
    print("Landing:", LANDING_URL)
    print("Target:", ARTICLEHTML_URL)
    print("=" * 70)

    t0 = time.time()

    # Step 1: Login via landing page
    print("\n[1] Logging in via landing page...")
    try:
        result = await _try_once_keep_alive(
            LANDING_URL,
            chrome_path,
            user_data_dir,
            headless=False,
            publisher=PUBLISHER,
        )
    except Exception as exc:
        print("\n[FATAL] Login exception:", exc)
        import traceback
        traceback.print_exc()
        return

    elapsed = time.time() - t0
    ok_str = str(result.get("ok"))
    cf_str = str(result.get("cf_type"))
    print(f"\n  Login done ({elapsed:.0f}s): ok={ok_str}, cf_type={cf_str}")

    browser = result.get("browser")
    if not browser:
        print("[FATAL] No browser in result")
        return

    tab = result.get("tab")
    html = result.get("html", "")
    landing_html_str = str(html) if html else ""
    if landing_html_str:
        print(f"  Landing page HTML: {len(landing_html_str)} chars")

    # Step 2: Navigate to articlehtml endpoint
    print("\n[2] Navigating to articlehtml endpoint...")
    try:
        if tab is None:
            tab = await browser.get(ARTICLEHTML_URL)
        else:
            await tab.get(ARTICLEHTML_URL)
        await tab.sleep(5)

        article_html = await tab.evaluate("document.documentElement.outerHTML")
        article_html_str = str(article_html) if article_html else ""

        print(f"  ArticleHTML size: {len(article_html_str)} chars")
        if article_html_str:
            wrapper_id = 'id="wrapper"'
            has_wrapper = wrapper_id in article_html_str
            img_count = article_html_str.count("image_table")
            print(f"  has_wrapper: {has_wrapper}, image_table count: {img_count}")

        body_text = (await tab.evaluate(
            "document.body ? document.body.innerText : ''"
        )) or ""
        print(f"  Body text: {len(str(body_text))} chars")

    except Exception as exc:
        print(f"[ERROR] Navigate to articlehtml: {exc}")
        import traceback
        traceback.print_exc()
        article_html_str = ""

    # Step 3: Save
    out_dir = pathlib.Path(r"D:\Temp")
    out_dir.mkdir(parents=True, exist_ok=True)

    landing_path = out_dir / "rsc_d2gc00698g_landing_loggedin.html"
    landing_path.write_text(landing_html_str, encoding="utf-8", errors="ignore")
    print(f"\n[3] Saved landing: {landing_path} ({len(landing_html_str)} chars)")

    if article_html_str:
        art_path = out_dir / "rsc_d2gc00698g_articlehtml_loggedin.html"
        art_path.write_text(article_html_str, encoding="utf-8", errors="ignore")
        print(f"    Saved articlehtml: {art_path} ({len(article_html_str)} chars)")

    total_elapsed = time.time() - t0
    print(f"\n[DONE] Total: {total_elapsed:.0f}s")

    # Cleanup
    if browser:
        await _stop_browser_safely(browser)


if __name__ == "__main__":
    asyncio.run(main())
