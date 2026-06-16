"""Windows-side bridge: CF bypass + login → HTML→MD → download images → save.

Called from WSL via:
    cmd.exe /c "D:\\python\\python.exe D:\\git\\paper-fetch-skill\\tests\\cf_bypass\\bridge_windows.py --doi ... --publisher ... --url ... --out-dir ..."

Uses a SINGLE browser session for HTML fetch + image download (previously two).
Path B (extract_browser_workflow_markdown) exclusively —
publisher-specific container selection, DOM cleaning, figure extraction,
and quality assessment via the 16 provider-specific _{name}_html.py modules.
"""

import sys
import os
import json
import time
import argparse
import asyncio
import base64
import re as _re
from pathlib import Path

SRC = Path(r"D:\git\paper-fetch-skill\src")
sys.path.insert(0, str(SRC))

os.environ.setdefault(
    "NODRIVER_USER_DATA_DIR",
    os.environ.get(
        "PAPER_FETCH_BRIDGE_USER_DATA_DIR",
        os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", r"C:\Temp")),
                     "nodriver_paper_fetch_test"),
    ),
)

from paper_fetch.providers._nodriver_fetch import (
    _try_once_keep_alive,
    _stop_browser_safely,
)
from paper_fetch.config import DEFAULT_CHROME_EXE, DEFAULT_NODRIVER_TEMP_PROFILE
from paper_fetch.providers.browser_runtime.types import BrowserRuntimeConfig
from paper_fetch.providers.atypon_browser_workflow.markdown import (
    extract_browser_workflow_markdown,
)


async def _download_images_async(
    tab,
    image_urls: list[str],
    output_dir: Path,
    timeout_per_image: float = 15.0,
) -> int:
    """Download images using an already-open browser tab (same-origin)."""
    if not image_urls:
        return 0

    downloaded = 0
    for url in image_urls:
        basename = url.rsplit("/", 1)[-1].split("?")[0]
        local_path = output_dir / "images" / basename
        if local_path.exists():
            downloaded += 1
            continue
        try:
            result = await asyncio.wait_for(
                tab.evaluate(
                    f"""
                    (async () => {{
                        const resp = await fetch('{url}');
                        if (!resp.ok) return null;
                        const blob = await resp.blob();
                        const reader = new FileReader();
                        return new Promise((resolve) => {{
                            reader.onload = () => resolve(reader.result);
                            reader.readAsDataURL(blob);
                        }});
                    }})()
                    """,
                    await_promise=True,
                ),
                timeout=timeout_per_image,
            )
            if result and isinstance(result, str) and result.startswith("data:"):
                payload = result.split(",", 1)[1] if "," in result else result
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(base64.b64decode(payload))
                downloaded += 1
        except Exception:
            pass
    return downloaded


async def _do_bridge(args: argparse.Namespace) -> dict:
    """Single async entry point — one browser session for everything."""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = BrowserRuntimeConfig(
        provider=args.publisher,
        doi=args.doi,
        artifact_dir=out_dir,
        headless=False,
        user_agent=None,
        binary_path=None,
        user_data_dir=Path(os.environ["NODRIVER_USER_DATA_DIR"]),
    )

    chrome_path = config.binary_path or DEFAULT_CHROME_EXE
    user_data_dir = str(config.user_data_dir or DEFAULT_NODRIVER_TEMP_PROFILE)

    result = {
        "success": False,
        "doi": args.doi,
        "publisher": args.publisher,
        "final_url": None,
        "html_len": 0,
        "md_len": 0,
        "title": None,
        "extraction_path": "B",
        "elapsed_seconds": 0,
        "error": None,
    }

    t0 = time.time()
    browser = None

    try:
        # ── Step 1: Browser fetch (CF bypass + login) — single session ──
        print(f"[bridge] Fetching: {args.url}", flush=True)
        fetch_result = await _try_once_keep_alive(
            args.url, chrome_path, user_data_dir,
            headless=config.headless, publisher=args.publisher,
        )
        if not fetch_result.get("ok"):
            raise RuntimeError(fetch_result.get("error", "CF bypass / fetch failed"))

        browser = fetch_result["browser"]
        tab = fetch_result["tab"]
        html = fetch_result["html"]
        final_url = fetch_result.get("final_url") or args.url
        page_title = fetch_result.get("title") or ""
        print(f"[bridge] HTML fetched ({len(html)} chars), browser kept alive", flush=True)

        # Save raw HTML
        html_path = out_dir / "bridge_html.html"
        html_path.write_text(html, encoding="utf-8", errors="ignore")
        print(f"[bridge] HTML saved: {html_path}", flush=True)

        # ── Step 2: HTML → Markdown (Path B, sync — but fine inside async) ──
        print(f"[bridge] Converting HTML → Markdown (Path B: publisher={args.publisher})…", flush=True)
        metadata = {
            "doi": args.doi,
            "title": args.title or page_title,
            "journal": args.journal,
        }
        md_text, extraction_payload = extract_browser_workflow_markdown(
            html, final_url, args.publisher, metadata=metadata,
        )

        # ── Step 3: Download images in the SAME browser session ──
        img_urls = list(set(
            m.group(1) for m in _re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", md_text)
        ))
        if img_urls:
            print(f"[bridge] Downloading {len(img_urls)} images in same browser session…", flush=True)
            downloaded = await _download_images_async(tab, img_urls, out_dir)
            print(f"[bridge] Downloaded {downloaded}/{len(img_urls)} images", flush=True)
            if downloaded > 0:
                from paper_fetch.providers._acs_html import rewrite_image_urls_to_local
                md_text = rewrite_image_urls_to_local(md_text, str(out_dir))

        # ── Step 4: Close browser ──
        print(f"[bridge] Closing browser…", flush=True)
        await _stop_browser_safely(browser)
        browser = None

        # ── Save outputs ──
        md_path = out_dir / "bridge_article.md"
        md_path.write_text(md_text, encoding="utf-8", errors="ignore")
        print(f"[bridge] Markdown saved: {md_path} ({len(md_text)} chars)", flush=True)

        if extraction_payload:
            payload_path = out_dir / "bridge_extraction_payload.json"
            payload_path.write_text(
                json.dumps(extraction_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

        elapsed = time.time() - t0
        result.update({
            "success": True,
            "final_url": final_url,
            "html_len": len(html),
            "html_path": str(html_path),
            "md_len": len(md_text),
            "md_path": str(md_path),
            "title": page_title,
            "elapsed_seconds": round(elapsed, 1),
        })

    except Exception as exc:
        elapsed = time.time() - t0
        result["error"] = str(exc)
        result["elapsed_seconds"] = round(elapsed, 1)
        print(f"[bridge] FAILED: {exc}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        if browser is not None:
            try:
                await _stop_browser_safely(browser)
            except Exception:
                pass

    return result


def main():
    parser = argparse.ArgumentParser(description="WSL→Windows bridge: browser fetch + HTML→MD + images")
    parser.add_argument("--doi", required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--url", required=True, help="Article landing page URL")
    parser.add_argument("--out-dir", required=True, help="Shared output directory")
    parser.add_argument("--journal", default="", help="Journal name (container-title from Crossref)")
    parser.add_argument("--title", default="", help="Article title (from Crossref metadata)")
    args = parser.parse_args()

    result = asyncio.run(_do_bridge(args))

    # Save result JSON
    out_dir = Path(args.out_dir)
    json_path = out_dir / "bridge_result.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bridge] Result saved: {json_path}", flush=True)

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
