"""Windows-side bridge: CF bypass + login → HTML→MD → save to shared filesystem.

Called from WSL via:
    cmd.exe /c "D:\\python\\python.exe D:\\git\\paper-fetch-skill\\tests\\cf_bypass\\bridge_windows.py --doi ... --publisher ... --url ... --out-dir ..."

Uses Path B (extract_browser_workflow_markdown) exclusively —
publisher-specific container selection, DOM cleaning, figure extraction,
and quality assessment via the 16 provider-specific _{name}_html.py modules.
"""

import sys
import os
import json
import time
import argparse
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

from paper_fetch.providers._nodriver_fetch import fetch_html_with_nodriver
from paper_fetch.providers.browser_runtime.types import BrowserRuntimeConfig
from paper_fetch.providers.atypon_browser_workflow.markdown import (
    extract_browser_workflow_markdown,
)


def _download_images_via_nodriver(
    image_urls: list[str],
    output_dir: Path,
    *,
    user_data_dir: str,
    main_page_url: str = "",
    chrome_path: str | None = None,
    headless: bool = False,
    timeout_per_image: float = 15.0,
) -> int:
    """Download images using nodriver browser (carries session cookies).

    Navigates to the main article page first so the browser has the
    correct origin for same-origin ``fetch()`` calls.  Returns number
    of successfully downloaded images.
    """
    import asyncio, base64

    if not image_urls:
        return 0

    async def _run():
        from paper_fetch.providers._nodriver_fetch import import_nodriver
        uc = import_nodriver()
        browser = await uc.start(
            user_data_dir=user_data_dir,
            browser_args=["--profile-directory=Default"],
            headless=headless,
            sandbox=False,
        )
        # Navigate to the main page to establish authenticated origin
        tab = await browser.get(main_page_url or "about:blank")
        await tab.sleep(1)

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

        try:
            await browser.stop()
            await asyncio.sleep(0.5)
        except Exception:
            pass
        return downloaded

    return asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(description="WSL→Windows bridge: browser fetch + HTML→MD")
    parser.add_argument("--doi", required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--url", required=True, help="Article landing page URL")
    parser.add_argument("--out-dir", required=True, help="Shared output directory")
    parser.add_argument("--journal", default="", help="Journal name (container-title from Crossref)")
    parser.add_argument("--title", default="", help="Article title (from Crossref metadata)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
    try:
        config = BrowserRuntimeConfig(
            provider=args.publisher,
            doi=args.doi,
            artifact_dir=out_dir,
            headless=False,
            user_agent=None,
            binary_path=None,
            user_data_dir=Path(os.environ["NODRIVER_USER_DATA_DIR"]),
        )

        # ── Step 1: Browser fetch (CF bypass + login) ──
        print(f"[bridge] Fetching: {args.url}", flush=True)
        browser_result = fetch_html_with_nodriver(
            candidate_urls=[args.url],
            publisher=args.publisher,
            config=config,
        )
        html = browser_result.html
        final_url = browser_result.final_url or args.url
        page_title = browser_result.title or ""

        # Save raw HTML
        html_path = out_dir / "bridge_html.html"
        html_path.write_text(html, encoding="utf-8", errors="ignore")
        print(f"[bridge] HTML saved: {html_path} ({len(html)} chars)", flush=True)

        # ── Step 2: HTML → Markdown (Path B) ──
        print(f"[bridge] Converting HTML → Markdown (Path B: publisher={args.publisher})…", flush=True)
        metadata = {
            "doi": args.doi,
            "title": args.title or page_title,
            "journal": args.journal,
        }
        md_text, extraction_payload = extract_browser_workflow_markdown(
            html,
            final_url,
            args.publisher,
            metadata=metadata,
        )

        # ── Step 2.5: Download images + rewrite URLs (ACS only) ──
        if args.publisher == "acs" and md_text:
            import re as _re
            img_urls = list(set(
                m.group(1) for m in _re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", md_text)
            ))
            if img_urls:
                print(f"[bridge] Downloading {len(img_urls)} images via browser…", flush=True)
                downloaded = _download_images_via_nodriver(
                    img_urls,
                    out_dir,
                    user_data_dir=os.environ["NODRIVER_USER_DATA_DIR"],
                    main_page_url=final_url,
                    headless=False,
                )
                print(f"[bridge] Downloaded {downloaded}/{len(img_urls)} images", flush=True)
                if downloaded > 0:
                    from paper_fetch.providers._acs_html import rewrite_image_urls_to_local
                    md_text = rewrite_image_urls_to_local(md_text, str(out_dir))

        md_path = out_dir / "bridge_article.md"
        md_path.write_text(md_text, encoding="utf-8", errors="ignore")
        print(f"[bridge] Markdown saved: {md_path} ({len(md_text)} chars)", flush=True)

        # Save extraction payload for debugging
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

    # Save result JSON
    json_path = out_dir / "bridge_result.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bridge] Result saved: {json_path}", flush=True)

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
