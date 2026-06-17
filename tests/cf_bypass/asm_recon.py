"""ASM reconnaissance — fetch page HTML to analyze platform, DOM, and login flow.

Usage (on Windows):
    python D:/git/paper-fetch-skill/tests/cf_bypass/asm_recon.py
"""

import asyncio
import os
import sys
import time
import pathlib
import json

SRC = r"D:\git\paper-fetch-skill\src"
sys.path.insert(0, SRC)

os.environ["NODRIVER_USER_DATA_DIR"] = os.path.join(
    os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
    "nodriver_asm_recon",
)

from paper_fetch.providers._nodriver_fetch import (
    _try_once_keep_alive,
    _stop_browser_safely,
)
from paper_fetch.config import DEFAULT_CHROME_EXE

# mBio (OA) — should show full text without login
OA_URLS = [
    "https://journals.asm.org/doi/10.1128/aem.02455-25",
    "https://journals.asm.org/doi/10.1128/aem.00996-17",
]

OUT_DIR = pathlib.Path(r"D:\Temp\asm_recon")


async def fetch_and_save(url: str, label: str, publisher: str = ""):
    chrome_path = DEFAULT_CHROME_EXE
    user_data_dir = os.environ["NODRIVER_USER_DATA_DIR"]

    print(f"\n{'='*70}")
    print(f"[{label}] {url}")
    print(f"{'='*70}")

    t0 = time.time()

    # Don't use publisher= for login — we just want CF bypass
    try:
        result = await _try_once_keep_alive(
            url, chrome_path, user_data_dir,
            headless=False, publisher=publisher,
        )
    except Exception as exc:
        print(f"[FATAL] {exc}")
        import traceback
        traceback.print_exc()
        return None

    elapsed = time.time() - t0
    print(f"  Done ({elapsed:.0f}s): ok={result.get('ok')}, cf_type={result.get('cf_type')}")

    browser = result.get("browser")
    html = result.get("html", "")
    final_url = result.get("final_url", url)
    html_str = str(html) if html else ""

    if html_str:
        # Save HTML
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        html_path = OUT_DIR / f"{label}.html"
        html_path.write_text(html_str, encoding="utf-8", errors="ignore")

        # Quick scan
        body_text = ""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_str, "html.parser")
            body_text = soup.body.get_text(" ", strip=True) if soup.body else ""
        except Exception:
            pass

        # Key signals
        signals = {
            "html_chars": len(html_str),
            "body_text_chars": len(body_text),
            "final_url": final_url,
            "has_access_denied": "access denied" in html_str.lower()[:5000] or "403" in html_str[:5000],
            "has_cf_challenge": "challenge-platform" in html_str.lower()[:5000],
            "has_fulltext": "fulltext" in html_str.lower()[:5000] or "full-text" in html_str.lower()[:5000],
            "has_login": "login" in html_str.lower()[:10000] or "sign in" in html_str.lower()[:10000],
            "has_sso": "shibboleth" in html_str.lower()[:10000] or "openathens" in html_str.lower()[:10000] or "carsi" in html_str.lower()[:10000] or "federated" in html_str.lower()[:10000],
            # Platform detection
            "is_silverchair": "S" in html_str[:500],
            "is_atypon": "atypon" in html_str.lower()[:5000],
            "has_literatum": "literatum" in html_str.lower()[:5000],
            # Content structure
            "main_content_ids": [],
        }

        # Find main content containers
        if soup:
            for sel in ["#main-content", "#content", "#article", "#article-body",
                        "[role='main']", "article", ".article", ".article-body",
                        ".main-content", ".content", "#fulltext", ".fulltext"]:
                el = soup.select_one(sel)
                if el:
                    text_len = len(el.get_text(" ", strip=True))
                    signals["main_content_ids"].append(f"{sel} ({text_len} chars)")

        print(f"  HTML: {len(html_str)} chars, body text: {len(body_text)} chars")
        print(f"  Signals: {json.dumps({k: v for k, v in signals.items() if k != 'main_content_ids'}, indent=2)}")
        print(f"  Content containers: {signals['main_content_ids'][:10]}")

        # Save signals
        sig_path = OUT_DIR / f"{label}_signals.json"
        sig_path.write_text(json.dumps(signals, indent=2, ensure_ascii=False),
                           encoding="utf-8")

        return html_str, signals

    if browser:
        await _stop_browser_safely(browser)
    return None


async def main():
    print("ASM Reconnaissance")
    print(f"Output: {OUT_DIR}")

    for i, url in enumerate(OA_URLS):
        label = f"asm_oa_{i+1}"
        await fetch_and_save(url, label)


if __name__ == "__main__":
    asyncio.run(main())
