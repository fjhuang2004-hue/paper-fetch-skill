"""Test Elsevier/ScienceDirect browser workflow + auto-login."""
import sys, os, time
sys.path.insert(0, r"D:\git\paper-fetch-skill\src")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from paper_fetch.providers._nodriver_fetch import fetch_html_with_nodriver
from paper_fetch.providers.browser_runtime.types import BrowserRuntimeConfig
from paper_fetch.providers._nodriver_login import _resolve_credentials, has_login_handler

# Check prerequisites
creds = _resolve_credentials()
print(f"Credentials: {'found' if creds else 'MISSING'}")
print(f"Elsevier login handler: {'registered' if has_login_handler('elsevier') else 'MISSING'}")
if not creds or not has_login_handler('elsevier'):
    print("Prerequisites not met, aborting.")
    sys.exit(1)

user_data_dir = r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'
os.environ['NODRIVER_USER_DATA_DIR'] = user_data_dir

config = BrowserRuntimeConfig(
    provider="elsevier",
    doi="10.1016/j.biortech.2024.130873",
    artifact_dir=Path(r"D:\Temp\paper_fetch_test"),
    headless=False,
    user_agent=None,
    binary_path=None,
    user_data_dir=Path(user_data_dir),
)

# Use the DOI landing page — browser will redirect to sciencedirect.com
url = "https://doi.org/10.1016/j.biortech.2024.130873"
print(f"\nFetching: {url}")
print("This will: DOI redirect → CF bypass → detect wall → auto-login\n")

t0 = time.time()
try:
    result = fetch_html_with_nodriver(
        candidate_urls=[url],
        publisher="elsevier",
        config=config,
    )
    elapsed = time.time() - t0
    print(f"\nSUCCESS in {elapsed:.1f}s")
    print(f"  final_url:  {result.final_url}")
    print(f"  html_len:   {len(result.html)}")
    print(f"  title:      {result.title}")
    print(f"  summary:    {result.summary}")

    html = result.html
    body_text_len = len(html)
    print(f"\n  Content signals:")
    print(f"    sciencedirect.com: {'sciencedirect.com' in result.final_url}")
    print(f"    preview-sidebar (walled): {'preview-sidebar' in html}")
    print(f"    Section snippets (walled): {'Section snippets' in html}")
    print(f"    body text > 50000: {body_text_len > 50000}")

    if body_text_len > 50000 and "preview-sidebar" not in html:
        print("\n  >> FULL TEXT ACCESS CONFIRMED")
    elif "preview-sidebar" in html:
        print("\n  >> Still walled (abstract only)")
    else:
        print("\n  >> Status unclear")

except Exception as e:
    elapsed = time.time() - t0
    print(f"\nFAILED after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()
