"""RSC login test — verify auto-login flow via bridge pipeline.

Usage (on Windows):
    python D:/git/paper-fetch-skill/tests/cf_bypass/rsc_login_test.py
"""

import asyncio
import os
import sys
import time

SRC = r"D:\git\paper-fetch-skill\src"
sys.path.insert(0, SRC)

os.environ["NODRIVER_USER_DATA_DIR"] = os.path.join(
    os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
    "nodriver_rsc_test",
)

from paper_fetch.providers._nodriver_fetch import (
    _try_once_keep_alive,
    _stop_browser_safely,
)
from paper_fetch.config import DEFAULT_CHROME_EXE

ARTICLE_URL = "https://pubs.rsc.org/en/content/articlelanding/2022/gc/d2gc00698g"
PUBLISHER = "rsc"


async def test():
    chrome_path = DEFAULT_CHROME_EXE
    user_data_dir = os.environ["NODRIVER_USER_DATA_DIR"]

    print("=" * 70)
    print("RSC Login Test")
    print("  URL:", ARTICLE_URL)
    print("  Publisher:", PUBLISHER)
    print("=" * 70)

    t0 = time.time()

    try:
        result = await _try_once_keep_alive(
            ARTICLE_URL,
            chrome_path,
            user_data_dir,
            headless=False,
            publisher=PUBLISHER,
        )
    except Exception as exc:
        print("\n[FATAL] Exception:", exc)
        import traceback
        traceback.print_exc()
        return

    elapsed = time.time() - t0

    print("\n" + "=" * 70)
    print("Result (%.1fs):" % elapsed)
    for key in ["ok", "cf_type", "error", "final_url"]:
        print("  %s: %s" % (key, result.get(key)))

    html = result.get("html", "")
    if html:
        html_str = str(html)
        print("  HTML size:", len(html_str), "chars")
        print("  paywall__body:", "paywall__body" in html_str)
        print("  article-control:", "article-control" in html_str)
        print('  id="wrapper":', 'id="wrapper"' in html_str)
        print("  References:", "References" in html_str)
        print("  image_table:", html_str.count("image_table"))

        # Save HTML
        try:
            import pathlib
            out = pathlib.Path(r"D:\Temp\rsc_test_result.html")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html_str, encoding="utf-8", errors="ignore")
            print("  Saved:", str(out))
        except Exception:
            pass

    browser = result.get("browser")
    if browser:
        await _stop_browser_safely(browser)

    print("\n[DONE]")


if __name__ == "__main__":
    asyncio.run(test())
