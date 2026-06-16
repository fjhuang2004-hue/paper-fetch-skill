"""Try fetching ScienceDirect article content from various URL patterns.

Uses the browser's authenticated session (post-CARSI login) to fetch
candidate content URLs and report what format each returns.
"""
import asyncio, json, sys, os
from pathlib import Path

SRC = Path(r"D:\git\paper-fetch-skill\src")
sys.path.insert(0, str(SRC))

os.environ.setdefault(
    "NODRIVER_USER_DATA_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "nodriver_paper_fetch_test"),
)

from paper_fetch.providers._nodriver_fetch import (
    import_nodriver, _resolve_real_profile, _copy_profile_fresh,
    _check_cf_passed, _detect_cf_type, _click_turnstile,
    _stop_browser_safely, _auto_login_if_needed,
)

ARTICLE_URL = "https://www.sciencedirect.com/science/article/pii/S1096717624001393"
PII = "S1096717624001393"
DOI = "10.1016/j.ymben.2024.10.011"
USER_DATA_DIR = os.environ["NODRIVER_USER_DATA_DIR"]


async def main():
    def _cdp(method, params=None):
        result = yield {"method": method, "params": params or {}}
        return result

    print("[sniff] Starting browser...", flush=True)
    uc = import_nodriver()
    real = _resolve_real_profile()
    if not real:
        print("[sniff] ERROR: No Chrome profile found", flush=True)
        return

    profile = _copy_profile_fresh(real, USER_DATA_DIR)
    browser = await uc.start(
        user_data_dir=profile or USER_DATA_DIR,
        browser_args=["--profile-directory=Default"],
        headless=False,
        sandbox=False,
    )
    tab = await browser.get("about:blank")
    await tab.sleep(1)

    # Step 1: Navigate to article page and login
    print(f"[sniff] Loading: {ARTICLE_URL}", flush=True)
    await tab.get(ARTICLE_URL)
    await tab.sleep(5)

    if not await _check_cf_passed(tab):
        cf_type = await _detect_cf_type(tab)
        print(f"[sniff] CF: {cf_type}, bypassing...", flush=True)
        if cf_type in ("turnstile_visible", "turnstile_hidden", "js_challenge"):
            await _click_turnstile(tab)
        for _ in range(20):
            await tab.sleep(1)
            if await _check_cf_passed(tab):
                break

    print("[sniff] Auto-login elsevier...", flush=True)
    await _auto_login_if_needed(tab, "elsevier")
    await tab.sleep(5)

    current_url = await tab.evaluate("window.location.href")
    print(f"[sniff] Current URL: {current_url}", flush=True)

    # Step 2: Try fetching candidate content URLs from within the page
    candidates = [
        # XML format
        f"/science/article/pii/{PII}/xml",
        f"/science/article/pii/{PII}?format=xml",
        # JSON APIs that ScienceDirect uses internally
        f"/science/article/pii/{PII}/fulltext",
        f"/science/article/abs/pii/{PII}",
        # API-style
        f"/content/article/{DOI}",
        # Via DOI
        f"/science/article/doi/{DOI}",
        # Fetch with different Accept header
    ]

    print(f"\n[sniff] Probing {len(candidates)} URL patterns...", flush=True)

    for url_suffix in candidates:
        fetch_url = url_suffix if url_suffix.startswith("http") else f"https://www.sciencedirect.com{url_suffix}"
        try:
            result = await tab.evaluate(f"""
            (async () => {{
                try {{
                    const resp = await fetch('{fetch_url}');
                    const ct = resp.headers.get('content-type') || '';
                    const text = await resp.text();
                    return {{
                        ok: resp.ok,
                        status: resp.status,
                        contentType: ct,
                        bodyLen: text.length,
                        preview: text.substring(0, 300),
                        isXml: text.startsWith('<?xml') || text.startsWith('<'),
                        startsWith: text.substring(0, 80),
                    }};
                }} catch(e) {{
                    return {{ok: false, error: e.message}};
                }}
            }})()
            """, await_promise=True)
        except Exception as e:
            result = {"ok": False, "error": str(e)}

        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            result = {}
        status = result.get("status", 0)
        body_len = result.get("bodyLen", 0) if result else 0
        ct = result.get("contentType", "") if result else ""
        preview = result.get("preview", "") if result else ""
        starts = result.get("startsWith", "") if result else ""

        marker = ""
        if "article" in str(preview).lower()[:200]:
            marker = " ★ ARTICLE CONTENT"
        elif "introduction" in str(preview).lower()[:300]:
            marker = " ★ BODY TEXT"
        elif result and result.get("isXml"):
            marker = " (XML)"

        print(f"\n  [{status}] {body_len:>7}B {url_suffix}{marker}")
        if ct:
            print(f"       Content-Type: {ct}")
        if starts:
            print(f"       StartsWith: {starts[:120]}")

    # Step 3: Also check the main page's __PRELOADED_STATE__
    print(f"\n\n[sniff] === Checking __PRELOADED_STATE__ keys ===")
    keys = await tab.evaluate("""
    (() => {
        try {
            const state = window.__PRELOADED_STATE__;
            if (!state) return 'NOT FOUND';
            return Object.keys(state);
        } catch(e) { return 'ERROR: ' + e.message; }
    })()
    """)
    print(f"  Keys: {keys}")

    # Check if body key has content
    body_state = await tab.evaluate("""
    (() => {
        try {
            const s = window.__PRELOADED_STATE__;
            if (!s || !s.body) return 'NO BODY';
            return JSON.stringify(s.body).substring(0, 500);
        } catch(e) { return 'ERROR: ' + e.message; }
    })()
    """)
    print(f"  body sub-state: {body_state}")

    # Also look for embedded data in the page
    has_embedded = await tab.evaluate("""
    (() => {
        const html = document.documentElement.outerHTML;
        const markers = [];
        if (html.includes('full-text-retrieval-response')) markers.push('full-text-retrieval-response');
        if (html.includes('<article ')) markers.push('<article> tag');
        if (html.includes('jats')) markers.push('jats');
        if (html.includes('__PRELOADED_STATE__')) markers.push('__PRELOADED_STATE__');
        return markers;
    })()
    """)
    print(f"  HTML markers: {has_embedded}")

    print("\n[sniff] Done. Closing browser...", flush=True)
    await _stop_browser_safely(browser)


if __name__ == "__main__":
    asyncio.run(main())
