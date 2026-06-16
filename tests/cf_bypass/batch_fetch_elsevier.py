"""Batch fetch Elsevier HTML for DOM analysis."""
import asyncio, sys, os
from pathlib import Path

SRC = Path(r"D:\git\paper-fetch-skill\src")
sys.path.insert(0, str(SRC))
os.environ.setdefault("NODRIVER_USER_DATA_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "nodriver_paper_fetch_test"))

from paper_fetch.providers._nodriver_fetch import (
    import_nodriver, _resolve_real_profile, _copy_profile_fresh,
    _check_cf_passed, _detect_cf_type, _click_turnstile,
    _stop_browser_safely, _auto_login_if_needed,
)

PAPERS = [
    ("10.1016/j.ymben.2024.10.011", "Metabolic Engineering"),
    ("10.1016/j.ymben.2024.06.006", "Metabolic Engineering"),
    ("10.1016/j.ymben.2024.05.004", "Metabolic Engineering"),
    ("10.1016/j.biortech.2024.131189", "Bioresource Technology"),
    ("10.1016/j.cell.2024.05.057", "Cell"),
    ("10.1016/j.cell.2024.02.041", "Cell"),
]

OUT_DIR = Path(r"D:\Temp\paper_fetch_bridge")
USER_DATA_DIR = os.environ["NODRIVER_USER_DATA_DIR"]


async def fetch_one(tab, doi, journal):
    url = f"https://doi.org/{doi}"
    safe_doi = doi.replace("/", "_").replace(".", "_")
    out = OUT_DIR / safe_doi
    out.mkdir(parents=True, exist_ok=True)

    print(f"  Navigating to: {url}", flush=True)
    await tab.get(url)
    await tab.sleep(4)

    # Check CF
    if not await _check_cf_passed(tab):
        cf_type = await _detect_cf_type(tab)
        if cf_type in ("turnstile_visible", "turnstile_hidden", "js_challenge"):
            await _click_turnstile(tab)
        for _ in range(15):
            await tab.sleep(1)
            if await _check_cf_passed(tab):
                break

    # Login if needed
    current_url = await tab.evaluate("window.location.href")
    if "sciencedirect.com" in str(current_url):
        await _auto_login_if_needed(tab, "elsevier")
        await tab.sleep(6)

    html = await tab.evaluate("document.documentElement.outerHTML") or ""
    if isinstance(html, list):
        html = html[0] if html else ""
    html = str(html)

    html_path = out / "article.html"
    html_path.write_text(html, encoding="utf-8", errors="ignore")
    final_url = await tab.evaluate("window.location.href")
    print(f"  Saved: {html_path} ({len(html)} chars) -> {final_url}", flush=True)


async def main():
    print(f"Batch fetching {len(PAPERS)} Elsevier papers...", flush=True)
    uc = import_nodriver()
    real = _resolve_real_profile()
    profile = _copy_profile_fresh(real, USER_DATA_DIR)
    browser = await uc.start(
        user_data_dir=profile or USER_DATA_DIR,
        browser_args=["--profile-directory=Default"],
        headless=False, sandbox=False,
    )
    tab = await browser.get("about:blank")
    await tab.sleep(1)

    for i, (doi, journal) in enumerate(PAPERS):
        print(f"\n[{i+1}/{len(PAPERS)}] {doi} ({journal})", flush=True)
        try:
            await fetch_one(tab, doi, journal)
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)

    print("\nAll done. Closing browser...", flush=True)
    await _stop_browser_safely(browser)


if __name__ == "__main__":
    asyncio.run(main())
