"""Test ACS CF bypass + auto-login end-to-end."""
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
print(f"ACS login handler: {'registered' if has_login_handler('acs') else 'MISSING'}")
if not creds or not has_login_handler('acs'):
    print("Prerequisites not met, aborting.")
    sys.exit(1)

os.environ['NODRIVER_USER_DATA_DIR'] = r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'

config = BrowserRuntimeConfig(
    provider="acs",
    doi="10.1021/acscatal.2c04683",
    artifact_dir=Path(r"D:\Temp\paper_fetch_test"),
    headless=False,
    user_agent=None,
    binary_path=None,
    user_data_dir=Path(r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'),
)

url = "https://pubs.acs.org/doi/10.1021/acscatal.2c04683"
print(f"\nFetching: {url}")
print("This will: CF bypass → detect wall → auto-login\n")

t0 = time.time()
try:
    result = fetch_html_with_nodriver(
        candidate_urls=[url],
        publisher="acs",
        config=config,
    )
    elapsed = time.time() - t0
    print(f"\nSUCCESS in {elapsed:.1f}s")
    print(f"  final_url:  {result.final_url}")
    print(f"  html_len:   {len(result.html)}")
    print(f"  title:      {result.title}")
    print(f"  summary:    {result.summary}")

    # Save HTML
    out_dir = Path(r"D:\Temp\paper_fetch_test")
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "acs_fetched.html"
    html_path.write_text(result.html, encoding="utf-8", errors="ignore")
    print(f"\n  HTML saved: {html_path}")

    # Check for wall/full-text signals
    html = result.html
    wall_signals = {
        "article_abstractPage (walled)": "article_abstractPage" in html,
        "access-denials__wrapper (walled)": "access-denials__wrapper" in html,
        "article_fullPage (full-text)": "article_fullPage" in html,
        "NLM_sec (full-text body)": "NLM_sec" in html,
    }
    print(f"\n  Content signals:")
    for label, found in wall_signals.items():
        print(f"    {'YES' if found else 'no'}: {label}")

    if "article_fullPage" in html and "NLM_sec" in html:
        print("\n  >> FULL TEXT ACCESS CONFIRMED")
    elif "article_abstractPage" in html:
        print("\n  >> Still walled (abstract only)")
    else:
        print("\n  >> Status unclear")

except Exception as e:
    elapsed = time.time() - t0
    print(f"\nFAILED after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()
