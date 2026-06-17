"""ASM end-to-end bridge test — CF bypass + extract + download images."""
import sys, os, asyncio, time, json, base64, re
from pathlib import Path

SRC = r"D:\git\paper-fetch-skill\src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("NODRIVER_USER_DATA_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
                 "nodriver_asm_e2e"))

from paper_fetch.providers._nodriver_fetch import (
    _try_once_keep_alive, _stop_browser_safely, _cdp,
)
from paper_fetch.config import DEFAULT_CHROME_EXE
from paper_fetch.providers.atypon_browser_workflow.markdown import (
    extract_browser_workflow_markdown,
)


async def main():
    DOI = "10.1128/aem.02455-25"
    URL = f"https://journals.asm.org/doi/{DOI}"
    OUT = Path(r"D:\Temp\asm_e2e")
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"ASM E2E: {DOI}")
    t0 = time.time()

    # Step 1: Browser fetch (CF bypass, no login)
    print("[1] Browser fetch...")
    result = await _try_once_keep_alive(URL, DEFAULT_CHROME_EXE,
        os.environ["NODRIVER_USER_DATA_DIR"], headless=False, publisher="")
    html = result.get("html", "")
    browser = result.get("browser")
    tab = result.get("tab")
    print(f"    HTML: {len(html)} chars, ok={result.get('ok')}, cf={result.get('cf_type')}")

    if not html or not browser or not tab:
        print("[FATAL] No HTML or browser")
        return

    # Step 2: Extract markdown
    print("[2] Extracting markdown...")
    md_text, payload = extract_browser_workflow_markdown(
        html, URL, "asm",
        metadata={"doi": DOI, "journal": "Applied and Environmental Microbiology"},
    )
    print(f"    MD: {len(md_text)} chars")
    print(f"    Title: {payload.get('title', '')[:80]}")
    print(f"    Abstract: {len(payload.get('abstract_text', '') or '')} chars")

    # Step 3: Extract image URLs from markdown
    img_urls = re.findall(r'!\[([^\]]*)\]\(([^)]+)\)', md_text)
    print(f"[3] Images in MD: {len(img_urls)}")

    # Step 4: Download images via CDP
    if img_urls:
        (OUT / "images").mkdir(exist_ok=True)
        frame_tree = await asyncio.wait_for(
            tab.send(_cdp("Page.getFrameTree")), timeout=10)
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        downloaded = 0
        for _, url in img_urls:
            basename = url.rsplit("/", 1)[-1].split("?")[0]
            local_path = OUT / "images" / basename
            if local_path.exists():
                downloaded += 1
                continue
            try:
                cdp_result = await asyncio.wait_for(
                    tab.send(_cdp("Network.loadNetworkResource", {
                        "frameId": frame_id, "url": url,
                        "options": {"disableCache": False, "includeCredentials": True},
                    })),
                    timeout=15,
                )
                resource = cdp_result.get("resource", {})
                success = resource.get("success")
                status = resource.get("httpStatusCode")
                body_b64 = resource.get("body") or ""
                stream_handle = resource.get("stream") or ""
                if success and status in (200, 304):
                    if not body_b64 and stream_handle:
                        # Read large response from IO stream
                        io_result = await asyncio.wait_for(
                            tab.send(_cdp("IO.read", {
                                "handle": stream_handle, "size": 10 * 1024 * 1024,
                            })),
                            timeout=15,
                        )
                        body_b64 = io_result.get("data") or ""
                        try:
                            await tab.send(_cdp("IO.close", {"handle": stream_handle}))
                        except Exception:
                            pass
                    if body_b64:
                        local_path.write_bytes(base64.b64decode(body_b64))
                        downloaded += 1
            except Exception as e:
                print(f"    [WARN] {basename[:50]}: {e}")
        print(f"[4] Downloaded: {downloaded}/{len(img_urls)}")

    # Step 5: Rewrite image URLs to local
    from paper_fetch.providers._asm_html import rewrite_image_urls_to_local
    md_text = rewrite_image_urls_to_local(md_text, str(OUT))
    print(f"[5] Rewrote image URLs")

    # Step 6: Save and report
    md_path = OUT / "bridge_article.md"
    md_path.write_text(md_text, encoding="utf-8", errors="ignore")
    print(f"[6] Saved: {md_path} ({len(md_text)} chars)")

    img_files = sorted((OUT / "images").iterdir()) if (OUT / "images").exists() else []
    for img in img_files:
        size_kb = img.stat().st_size / 1024
        print(f"    {img.name} — {size_kb:.0f} KB")

    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.0f}s total, {len(img_files)} images")

    if browser:
        await _stop_browser_safely(browser)


if __name__ == "__main__":
    asyncio.run(main())
