"""Test ScienceDirect CF bypass + auto-login end-to-end."""
import sys, os, time
sys.path.insert(0, r"D:\git\paper-fetch-skill\src")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from paper_fetch.providers._nodriver_fetch import fetch_html_with_nodriver
from paper_fetch.providers.browser_runtime.types import BrowserRuntimeConfig
from paper_fetch.providers._nodriver_login import _resolve_credentials, has_login_handler

creds = _resolve_credentials()
print(f"Credentials: {'found' if creds else 'MISSING'}")
print(f"SD login handler: {'registered' if has_login_handler('sciencedirect') else 'MISSING'}")
if not creds or not has_login_handler('sciencedirect'):
    sys.exit(1)

os.environ['NODRIVER_USER_DATA_DIR'] = r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'

config = BrowserRuntimeConfig(
    provider="sciencedirect",
    doi="S0960852424004760",
    artifact_dir=Path(r"D:\Temp\paper_fetch_test"),
    headless=False, user_agent=None, binary_path=None,
    user_data_dir=Path(r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'),
)

url = "https://www.sciencedirect.com/science/article/abs/pii/S0960852424004760?via%3Dihub"
print(f"\nFetching: {url}")
print("CF bypass → detect wall → auto-login\n")

t0 = time.time()
try:
    result = fetch_html_with_nodriver(candidate_urls=[url], publisher="sciencedirect", config=config)
    elapsed = time.time() - t0
    print(f"SUCCESS {elapsed:.1f}s | {len(result.html)} chars | {result.title[:80] if result.title else 'N/A'}")

    html = result.html
    wall_signals = {
        "preview-sidebar (walled)": "preview-sidebar" in html,
        "Section snippets (walled)": "Section snippets" in html,
        "Article preview (walled)": "Article preview" in html,
    }
    for label, found in wall_signals.items():
        print(f"  {'WALL' if found else 'CLEAN'} {label}")

    if any(wall_signals.values()):
        print("  >> Still walled")
    else:
        print("  >> Full text access confirmed")
except Exception as e:
    print(f"FAILED {time.time()-t0:.1f}s: {e}")
