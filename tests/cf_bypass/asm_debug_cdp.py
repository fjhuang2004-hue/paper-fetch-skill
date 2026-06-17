"""Debug CDP image download for ASM."""
import sys, os, asyncio, base64

SRC = r"D:\git\paper-fetch-skill\src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("NODRIVER_USER_DATA_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
                 "nodriver_asm_debug"))

from paper_fetch.providers._nodriver_fetch import (
    _try_once_keep_alive, _stop_browser_safely, _cdp,
)
from paper_fetch.config import DEFAULT_CHROME_EXE

async def main():
    URL = "https://journals.asm.org/doi/10.1128/aem.02455-25"

    print("Fetching...")
    result = await _try_once_keep_alive(URL, DEFAULT_CHROME_EXE,
        os.environ["NODRIVER_USER_DATA_DIR"], headless=False, publisher="")
    html = result.get("html", "")
    browser = result.get("browser")
    tab = result.get("tab")
    print(f"HTML: {len(html)} chars, tab={tab is not None}, browser={browser is not None}")

    if not tab:
        print("No tab!")
        return

    # Try CDP directly
    try:
        frame_tree = await asyncio.wait_for(
            tab.send(_cdp("Page.getFrameTree")), timeout=10)
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        print(f"Frame ID: {frame_id}")
    except Exception as e:
        print(f"getFrameTree failed: {e}")
        import traceback
        traceback.print_exc()
        if browser:
            await _stop_browser_safely(browser)
        return

    # Try downloading ONE image via CDP
    test_url = "https://journals.asm.org/cms/10.1128/aem.02455-25/asset/c692bb8a-09e9-4a1b-923f-14987aca4abe/assets/images/large/aem.02455-25.f001.jpg"
    print(f"Downloading: {test_url[-80:]}")

    try:
        cdp_result = await asyncio.wait_for(
            tab.send(_cdp("Network.loadNetworkResource", {
                "frameId": frame_id, "url": test_url,
                "options": {"disableCache": False, "includeCredentials": True},
            })),
            timeout=15,
        )
        print(f"CDP result: {list(cdp_result.keys()) if isinstance(cdp_result, dict) else type(cdp_result)}")
        resource = cdp_result.get("resource", {})
        print(f"  success: {resource.get('success')}")
        print(f"  httpStatusCode: {resource.get('httpStatusCode')}")
        body_b64 = resource.get("body") or ""
        stream = resource.get("stream") or ""
        print(f"  body length: {len(body_b64)}")
        print(f"  stream: {stream[:80]}")

        if resource.get("success") and body_b64:
            from pathlib import Path
            out = Path(r"D:\Temp\asm_debug_test.jpg")
            out.write_bytes(base64.b64decode(body_b64))
            print(f"  Saved: {out} ({out.stat().st_size} bytes)")
    except Exception as e:
        print(f"Download failed: {e}")
        import traceback
        traceback.print_exc()

    if browser:
        await _stop_browser_safely(browser)

if __name__ == "__main__":
    asyncio.run(main())
