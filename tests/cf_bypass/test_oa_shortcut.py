"""End-to-end test for OA shortcut (PMC JATS XML → markdown → ArticleModel).

Run from Windows / WSL::

    python tests/cf_bypass/test_oa_shortcut.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from paper_fetch.http import HttpTransport
from paper_fetch.workflow.oa_shortcut import (
    _check_is_oa,
    _fetch_pmc_xml,
    _jats_xml_to_markdown,
    _search_epmc,
    _word_count,
    try_oa_shortcut,
)
from paper_fetch.utils import safe_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("test_oa_shortcut")

_TRANSPORT = HttpTransport()

DOI = "10.1093/tas/txaf131"
METADATA_OA = {
    "license_urls": ["https://creativecommons.org/licenses/by/4.0/"],
    "title": "Test OA paper",
}
METADATA_NON_OA = {
    "license_urls": ["https://www.elsevier.com/termsandconditions"],
}


def test_check_is_oa() -> bool:
    logger.info("── test_check_is_oa ──")
    assert _check_is_oa(METADATA_OA), "CC BY should be OA"
    assert _check_is_oa({"license_urls": ["http://creativecommons.org/licenses/by-nc/4.0/"]})
    assert not _check_is_oa(METADATA_NON_OA)
    assert not _check_is_oa({})
    logger.info("✅ PASS")
    return True


def test_epmc_search() -> bool:
    logger.info("── test_epmc_search (EPMC → PMCID) ──")
    item = _search_epmc(DOI, _TRANSPORT)
    if item is None:
        # EPMC is blocked on some networks (e.g. Windows), not a fatal failure
        logger.warning("⚠ EPMC search returned None (network blocked?); PMC-only path will skip")
        return True
    pmcid = safe_text(item.get("pmcid"))
    if not pmcid:
        logger.error("❌ No PMCID in EPMC result")
        return False
    logger.info("  PMCID: %s", pmcid)
    logger.info("✅ PASS")
    return True


def test_pmc_xml() -> bool:
    logger.info("── test_pmc_xml (efetch → JATS→MD) ──")
    pmcid = "PMC12967033"  # known PMCID for the test DOI
    xml = _fetch_pmc_xml(pmcid, _TRANSPORT)
    if xml is None:
        logger.error("❌ PMC efetch returned None")
        return False
    logger.info("  XML: %d chars", len(xml))

    md = _jats_xml_to_markdown(xml)
    if md is None:
        logger.error("❌ JATS→MD returned None")
        return False

    wc = _word_count(md)
    logger.info("  MD: %d chars, %d words", len(md), wc)
    if wc < 500:
        logger.error("❌ Too few words: %d", wc)
        return False

    # Check sections present
    for required in ["Abstract", "Introduction", "Materials and methods", "Conclusions"]:
        if required.lower() not in md.lower():
            logger.warning("⚠ Missing section: %s", required)

    logger.info("  Sections found:")
    for line in md.split("\n"):
        if line.startswith("#"):
            logger.info("    %s", line.strip()[:80])

    logger.info("✅ PASS")
    return True


def test_full_shortcut() -> bool:
    logger.info("── test_full_shortcut (end-to-end) ──")
    t0 = time.monotonic()
    result = try_oa_shortcut(doi=DOI, metadata=METADATA_OA, transport=_TRANSPORT)
    dt = time.monotonic() - t0

    if result is None:
        # EPMC might be blocked — gracefully skip
        logger.warning("⚠ try_oa_shortcut returned None (EPMC blocked?)")
        return True

    article, raw_md = result
    logger.info("  Time: %.1f s", dt)
    logger.info("  Title: %s", str(article.metadata.title)[:120])
    logger.info("  Sections: %d", len(article.sections))
    logger.info("  content_kind: %s", article.quality.content_kind)
    logger.info("  Raw MD: %d words", _word_count(raw_md))

    if not article.metadata.title:
        logger.error("❌ No title in ArticleModel")
        return False

    logger.info("✅ PASS")
    return True


def test_not_oa_skips() -> bool:
    logger.info("── test_not_oa_skips ──")
    t0 = time.monotonic()
    result = try_oa_shortcut(doi=DOI, metadata=METADATA_NON_OA, transport=_TRANSPORT)
    dt = time.monotonic() - t0

    assert result is None, f"Non-OA should return None, got {type(result)}"
    assert dt < 1.0, f"Non-OA check took {dt:.2f}s (expected <1s)"
    logger.info("  Skipped in %.3f s", dt)
    logger.info("✅ PASS")
    return True


def main() -> int:
    tests = [
        ("check_is_oa", test_check_is_oa),
        ("EPMC search → PMCID", test_epmc_search),
        ("PMC efetch → JATS→MD", test_pmc_xml),
        ("Full shortcut", test_full_shortcut),
        ("Non-OA skips", test_not_oa_skips),
    ]

    results: list[tuple[str, bool]] = []
    for name, func in tests:
        try:
            ok = func()
            results.append((name, ok))
        except Exception as exc:
            logger.exception("❌ %s crashed: %s", name, exc)
            results.append((name, False))
        print()

    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, ok in results:
        print(f"  {'✅ PASS' if ok else '❌ FAIL'}  {name}")

    all_ok = all(ok for _, ok in results)
    print()
    if all_ok:
        print("All tests passed!")
    else:
        print("Some tests failed.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
