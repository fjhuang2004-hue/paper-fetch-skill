"""Test _nodriver_fetch.py CF bypass — all 8 publishers × 1 run."""
import sys, os, time, asyncio
sys.path.insert(0, r"D:\git\paper-fetch-skill\src")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from paper_fetch.providers._nodriver_fetch import fetch_html_with_nodriver
from paper_fetch.providers.browser_runtime.types import BrowserRuntimeConfig

os.environ['NODRIVER_USER_DATA_DIR'] = r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'

PUBLISHERS = {
    "ACS":           ("acs",           "https://pubs.acs.org/doi/10.1021/acscatal.2c04683"),
    "Wiley":         ("wiley",         "https://onlinelibrary.wiley.com/doi/10.1002/anie.202300001"),
    "ScienceDirect": ("sciencedirect",  "https://www.sciencedirect.com/science/article/pii/S002195172300001X"),
    "PNAS":          ("pnas",          "https://www.pnas.org/doi/10.1073/pnas.2300001"),
    "ASM":           ("asm",           "https://journals.asm.org/doi/10.1128/jb.00001-23"),
    "OUP":           ("oup",           "https://academic.oup.com/nar/article/51/1/1/7000001"),
    "TandF":         ("tandf",         "https://www.tandfonline.com/doi/full/10.1080/15476286.2023.0000001"),
    "cell.com":      ("cell",          "https://www.cell.com/cell/fulltext/S0092-8674(23)00001-X"),
}

results = {}
for name, (publisher, url) in PUBLISHERS.items():
    t0 = time.time()
    config = BrowserRuntimeConfig(
        provider=publisher,
        doi=f"probe-{name}",
        artifact_dir=Path(r"D:\Temp\paper_fetch_test"),
        headless=False,
        user_agent=None,
        binary_path=None,
        user_data_dir=Path(r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'),
    )
    try:
        result = fetch_html_with_nodriver(
            candidate_urls=[url],
            publisher=publisher,
            config=config,
        )
        elapsed = time.time() - t0
        print(f"  PASS {name:16s} {elapsed:5.1f}s  {len(result.html):>7d} chars  {result.title[:60] if result.title else 'N/A'}")
        results[name] = True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAIL {name:16s} {elapsed:5.1f}s  {str(e)[:80]}")
        results[name] = False

print()
passed = sum(results.values())
print(f"Result: {passed}/{len(results)} passed")
for name, ok in results.items():
    print(f"  {'PASS' if ok else 'FAIL'} {name}")
