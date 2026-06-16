"""Process-local nodriver (CDP-based Chrome) integration helpers."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("paper_fetch.providers.nodriver_runtime")

_NODRIVER_LOCK = threading.Lock()
_UC_MODULE: Any = None


def import_nodriver() -> Any:
    """Lazy-import nodriver so the rest of paper-fetch is importable without it."""
    global _UC_MODULE
    if _UC_MODULE is None:
        import nodriver as uc  # type: ignore[no-redef]
        _UC_MODULE = uc
    return _UC_MODULE


# ── Chrome process management ────────────────────────────────────────

def kill_chrome(user_data_dir: str | None = None) -> None:
    """Kill Chrome processes, optionally scoped to a specific user-data-dir.

    When *user_data_dir* is provided, only Chrome instances whose command
    line includes that directory path are killed.  Otherwise the legacy
    behaviour (kill every ``chrome.exe`` / ``chrome`` process) applies and
    a warning is logged.
    """
    if user_data_dir:
        _kill_chrome_scoped(user_data_dir)
    else:
        logger.warning(
            "kill_chrome() called without user_data_dir — "
            "killing ALL Chrome processes (legacy fallback)"
        )
        _kill_all_chrome()


def _kill_chrome_scoped(user_data_dir: str) -> None:
    """Kill only Chrome processes whose command line includes *user_data_dir*."""
    import sys as _sys

    with _NODRIVER_LOCK:
        for attempt in range(3):
            try:
                if _sys.platform == "win32":
                    _kill_chrome_scoped_win(user_data_dir)
                else:
                    subprocess.run(
                        ["pkill", "-9", "-i", "-f", f"chrome.*{re.escape(user_data_dir)}"],
                        capture_output=True,
                        timeout=3,
                    )
                break
            except Exception:
                if attempt < 2:
                    time.sleep(0.5)


def _kill_chrome_scoped_win(user_data_dir: str) -> None:
    """Windows: use PowerShell to find Chrome PIDs by command-line match."""
    # Double single-quotes for PowerShell escaping
    safe_dir = user_data_dir.replace("'", "''")

    ps_script = (
        "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" |"
        " ForEach-Object {"
        f" if ($_.CommandLine -like '*{safe_dir}*') {{ Write-Output $_.ProcessId }}"
        " }"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("PowerShell lookup for Chrome PIDs failed", exc_info=True)
        return

    if result.returncode != 0:
        return

    pids = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().isdigit()
    ]

    if not pids:
        logger.debug(
            "No Chrome processes found with user_data_dir=%s", user_data_dir
        )
        return

    logger.info(
        "Killing %d Chrome process(es) scoped to %s", len(pids), user_data_dir
    )

    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                capture_output=True,
                timeout=3,
            )
        except Exception:
            pass


def _kill_all_chrome() -> None:
    """Legacy: kill all Chrome processes indiscriminately."""
    import sys as _sys

    with _NODRIVER_LOCK:
        for _ in range(3):
            try:
                if _sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/IM", "chrome.exe"],
                        capture_output=True,
                        timeout=3,
                    )
                else:
                    subprocess.run(
                        ["pkill", "-9", "-i", "-f", "chrome"],
                        capture_output=True,
                        timeout=3,
                    )
            except Exception:
                pass


# ── Profile helpers ───────────────────────────────────────────────────

def copy_profile(real_profile: str, temp_profile: str) -> str | None:
    """Copy the real Chrome profile to a temp directory (avoids lock
    conflicts with the user's everyday Chrome instance).

    Skips caches, history, and other heavy/volatile data.
    """
    real = Path(real_profile)
    if not real.exists() or not (real / "Default").exists():
        return None

    temp = Path(temp_profile)
    if temp.exists():
        shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True, exist_ok=True)

    for sub in ["Default", "Local State", "Preferences"]:
        src, dst = real / sub, temp / sub
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "Cache",
                    "Code Cache",
                    "GPUCache",
                    "Service Worker",
                    "IndexedDB",
                    "WebStorage",
                    "shared_proto_db",
                    "History",
                    "Favicons",
                    "Top Sites",
                    "Media History",
                    # Chrome locks these while running
                    "Cookies",
                    "Cookies-journal",
                    "Safe Browsing*",
                    "Network",
                    "Sessions",
                    "Tabs_*",
                    "Session_*",
                    "TransportSecurity",
                    "Reporting and NEL",
                    "Trust Tokens",
                    "TrustToken*",
                    "Site Characteristics Database",
                    "segmentation_platform",
                    "MediaFoundationWidevineCdm*",
                ),
                dirs_exist_ok=True,
            )
        elif src.is_file():
            shutil.copy2(src, dst)
    return str(temp)


__all__ = [
    "copy_profile",
    "import_nodriver",
    "kill_chrome",
    "uc",
]
