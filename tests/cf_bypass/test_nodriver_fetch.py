"""Quick smoketest: fetch ACS article HTML via nodriver."""
import os
import sys
import time

sys.path.insert(0, r"D:\git\paper-fetch-skill\src")

# Force UTF-8 for stdout
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── Verify Windows Python resolves Path.home() correctly ──
from pathlib import Path
print(f"Path.home() = {Path.home()}")

# ── Kill any existing Chrome before we start ──
from paper_fetch._nodriver_runtime import kill_chrome

# ── Set up environment ──
env = os.environ.copy()
# Use a temp profile dir so we don't touch the real one
user_data_dir = str(Path.home() / "AppData" / "Local" / "Temp" / "nodriver_paper_fetch_test")
env["NODRIVER_USER_DATA_DIR"] = user_data_dir

# Scope kill to our temp profile only — never kill user's daily Chrome
print("Killing leftover Chrome in our temp profile...")
kill_chrome(user_data_dir=user_data_dir)
time.sleep(1)

# ── Build a BrowserRuntimeConfig ──
from paper_fetch.providers.browser_runtime.types import BrowserRuntimeConfig

config = BrowserRuntimeConfig(
    provider="acs",
    doi="10.1021/acscatal.2c04683",
    artifact_dir=Path(r"D:\Temp\paper_fetch_test"),
    headless=False,
    user_agent=None,
    binary_path=None,
    user_data_dir=Path(user_data_dir),
)

print(f"config: provider={config.provider}, doi={config.doi}")
print(f"user_data_dir: {config.user_data_dir}")

# ── Fetch ──
from paper_fetch.providers._nodriver_fetch import fetch_html_with_nodriver

url = "https://pubs.acs.org/doi/10.1021/acscatal.2c04683"
print(f"\nFetching: {url}")
print("This will launch Chrome... (set headless=True in config to hide window)\n")

t0 = time.time()
try:
    result = fetch_html_with_nodriver(
        candidate_urls=[url],
        publisher="acs",
        config=config,
    )
    elapsed = time.time() - t0
    print(f"\n[PASS] SUCCESS in {elapsed:.1f}s")
    print(f"  source_url: {result.source_url}")
    print(f"  final_url:  {result.final_url}")
    print(f"  html_len:   {len(result.html)}")
    print(f"  title:      {result.title}")
    print(f"  status:     {result.response_status}")
except Exception as e:
    elapsed = time.time() - t0
    print(f"\n[FAIL] after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()
