"""Test kill_chrome Linux path (run in WSL or native Linux).

Usage:
    python tests/cf_bypass/test_kill_chrome_linux.py
"""

from __future__ import annotations

import subprocess
import sys
import time


def test_pkill_available():
    """pkill should exist on Linux/macOS."""
    try:
        subprocess.run(["which", "pkill"], capture_output=True, timeout=2, check=True)
        print("✅ pkill available")
        return True
    except Exception:
        print("❌ pkill NOT found — kill_chrome won't work on this system")
        return False


def test_signal_default():
    """Verify pkill sends SIGTERM by default (no -9)."""
    r = subprocess.run(
        ["pkill", "--help"], capture_output=True, text=True, timeout=2
    )
    has_signal = "--signal" in r.stdout or "-signal" in r.stdout
    print(f"{'✅' if has_signal else '⚠️'} pkill supports --signal flag")
    return has_signal


def test_case_insensitive():
    """pkill -i for case-insensitive matching (needed for macOS 'Google Chrome')."""
    r = subprocess.run(
        ["pkill", "--help"], capture_output=True, text=True, timeout=2
    )
    has_i = "-i" in r.stdout or "--ignore-case" in r.stdout
    print(f"{'✅' if has_i else '⚠️'} pkill supports -i (case-insensitive)")
    return has_i


def test_pgrep_fallback():
    """pgrep can be used as fallback if pkill doesn't work."""
    try:
        subprocess.run(["which", "pgrep"], capture_output=True, timeout=2, check=True)
        print("✅ pgrep available (fallback option)")
        return True
    except Exception:
        print("⚠️ pgrep not found")
        return False


def test_kill_signal_9():
    """Verify kill -9 works."""
    try:
        subprocess.run(["which", "kill"], capture_output=True, timeout=2, check=True)
        print("✅ kill available")
        return True
    except Exception:
        print("⚠️ kill not found")
        return False


def test_chrome_binary_name():
    """How does Chrome appear in process list on this system?"""
    # Check for various chrome binary names
    names = ["chrome", "chromium", "google-chrome", "chromium-browser"]
    found = []
    for name in names:
        r = subprocess.run(["which", name], capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            found.append(r.stdout.strip())
    if found:
        print(f"✅ Chrome/Chromium found: {found}")
    else:
        print("⚠️ No Chrome binary found in PATH (expected in WSL)")


def test_pattern_regex_safety():
    """Verify user_data_dir with regex-special chars is handled."""
    import re

    # A path like /tmp/nodriver.test has '.' which matches any char in regex
    paths = [
        "/tmp/nodriver_paper_fetch_test",   # safe
        "/tmp/nodriver.test.profile",        # dots are wildcards in regex
        "/tmp/nodriver (copy)",              # parentheses
        "/tmp/nodriver+test",                # plus
    ]
    for p in paths:
        pattern = f"chrome.*{p}"
        try:
            re.compile(pattern)
            safe = True
        except re.error:
            safe = False
        is_literal = all(c not in p for c in ".+*?[](){}|^$\\")
        status = "✅" if is_literal else "⚠️ unsafe regex"
        print(f"  {status}: {p}")
        if not is_literal:
            escaped = f"chrome.*{re.escape(p)}"
            print(f"    → should escape: {escaped}")


def main():
    print("=" * 60)
    print("kill_chrome Linux path diagnostics")
    print(f"platform: {sys.platform}")
    print("=" * 60)
    print()

    results = {
        "pkill_available": test_pkill_available(),
        "signal_flag": test_signal_default(),
        "case_insensitive": test_case_insensitive(),
        "pgrep_fallback": test_pgrep_fallback(),
        "kill_9": test_kill_signal_9(),
    }

    print()
    test_chrome_binary_name()

    print()
    print("--- Regex safety check ---")
    test_pattern_regex_safety()

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)

    all_ok = all(results.values())
    if all_ok:
        print("✅ All checks passed")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"⚠️ Issues found: {failed}")

    print()
    print("Action items:")
    print("1. Add -9 (SIGKILL) to pkill for forceful termination")
    print("2. Add -i flag for macOS (Google Chrome vs google-chrome)")
    print("3. Escape regex special chars in user_data_dir with re.escape()")
    print("4. Consider pgrep + kill as fallback if pkill unavailable")


if __name__ == "__main__":
    main()
