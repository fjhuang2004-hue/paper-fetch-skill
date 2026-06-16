"""Full ACS pipeline test: DOI → metadata → CF bypass → login → HTML→MD → ArticleModel → images.

Run on Windows: python D:\git\paper-fetch-skill\tests\cf_bypass\test_acs_full_pipeline.py
"""

import sys, os, time, json
sys.path.insert(0, r"D:\git\paper-fetch-skill\src")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from paper_fetch.workflow.fulltext import fetch_article
from paper_fetch.workflow.types import FetchStrategy

DOI = "10.1021/acscatal.2c04683"
DOWNLOAD_DIR = Path(r"D:\Temp\paper_fetch_acs_full")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

os.environ['NODRIVER_USER_DATA_DIR'] = r'C:\Users\黄福京\AppData\Local\Temp\nodriver_paper_fetch_test'

print(f"DOI: {DOI}")
print(f"Output: {DOWNLOAD_DIR}")
print(f"Strategy: fulltext_only")
print()

t0 = time.time()
try:
    article = fetch_article(
        query=f"doi:{DOI}",
        strategy=FetchStrategy.FULLTEXT_ONLY,
        download_dir=DOWNLOAD_DIR,
    )
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"DONE in {elapsed:.1f}s")
    print(f"{'='*60}")
    print(f"  Title: {article.title}")
    print(f"  Authors: {len(article.authors)}")
    print(f"  Sections: {len(article.sections)}")
    print(f"  Word count: {article.word_count}")
    print(f"  Source: {article.source}")
    print(f"  Assets: {len(article.assets)}")

    # Save AI markdown
    ai_md = article.to_ai_markdown()
    md_path = DOWNLOAD_DIR / "acs_full_article.md"
    md_path.write_text(ai_md, encoding="utf-8", errors="ignore")
    print(f"\n  AI markdown saved: {md_path}")

    # Save JSON summary
    summary = {
        "title": article.title,
        "doi": DOI,
        "source": article.source,
        "authors_count": len(article.authors),
        "sections_count": len(article.sections),
        "word_count": article.word_count,
        "assets_count": len(article.assets),
        "elapsed_seconds": round(elapsed, 1),
    }
    json_path = DOWNLOAD_DIR / "acs_full_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Summary saved: {json_path}")

    # List downloaded files
    files = list(DOWNLOAD_DIR.glob("*"))
    print(f"\n  All outputs ({len(files)} files):")
    for f in sorted(files):
        size_kb = f.stat().st_size / 1024
        print(f"    {f.name} ({size_kb:.1f} KB)")

except Exception as e:
    elapsed = time.time() - t0
    print(f"\nFAILED after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()
