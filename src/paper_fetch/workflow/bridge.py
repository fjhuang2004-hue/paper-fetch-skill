"""WSL → Windows bridge: delegates browser-dependent work to Windows side.

When running in WSL, nodriver / Chrome isn't available.  This module detects
WSL and calls a Windows-side script via ``cmd.exe /c`` for CF bypass + login
+ HTML extraction.  The results land on the shared filesystem (``/mnt/d/…``)
and are read back by the WSL process.

Flow::

    WSL: try_bridge_fetch()
      → subprocess.run(["cmd.exe", "/c", "python", bridge_script, ...])
      → Windows: CF bypass + login + HTML→MD
      → reads bridge_article.md
      → article_from_markdown() → ArticleModel
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from ..models import article_from_markdown

logger = logging.getLogger("paper_fetch.workflow.bridge")

_BRIDGE_SCRIPT_WIN = os.environ.get(
    "PAPER_FETCH_BRIDGE_SCRIPT",
    r"D:\git\paper-fetch-skill\tests\cf_bypass\bridge_windows.py",
)
_BRIDGE_PYTHON_WIN = os.environ.get(
    "PAPER_FETCH_BRIDGE_PYTHON",
    r"D:\python\python.exe",
)
_DEFAULT_OUT_ROOT = os.environ.get(
    "PAPER_FETCH_BRIDGE_OUT_DIR",
    r"D:\Temp\paper_fetch_bridge",
)


def _wsl_to_win_path(wsl_path: str) -> str:
    """Convert a WSL path to its Windows equivalent.

    ``/mnt/d/Temp/foo`` → ``D:\\Temp\\foo``.
    """
    if wsl_path.startswith("/mnt/"):
        parts = wsl_path[5:].split("/", 1)
        if parts:
            drive = parts[0].upper()
            rest = parts[1] if len(parts) > 1 else ""
            return f"{drive}:\\{rest.replace('/', chr(92))}"
    return wsl_path


def _win_to_wsl_path(win_path: str) -> str:
    """Convert a Windows path to its WSL equivalent.

    ``D:\\Temp\\foo`` → ``/mnt/d/Temp/foo``.
    """
    if len(win_path) >= 2 and win_path[1] == ":":
        drive = win_path[0].lower()
        rest = win_path[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return win_path


def _is_wsl() -> bool:
    """Return True if the current process is running in WSL."""
    try:
        with open("/proc/sys/fs/binfmt_misc/WSLInterop") as f:
            return f.read(1) != "0"
    except (FileNotFoundError, PermissionError):
        pass
    # Fallback: check kernel version for "microsoft" or "WSL"
    try:
        with open("/proc/version") as f:
            content = f.read().lower()
            if "microsoft" in content or "wsl" in content:
                return True
    except Exception:
        pass
    return False


def try_bridge_fetch(
    doi: str,
    publisher: str,
    url: str,
    *,
    metadata: Mapping[str, Any],
    timeout_seconds: float = 180,
) -> tuple[Any, str] | None:
    """Attempt to fetch full-text via the Windows bridge.

    Returns ``(ArticleModel, markdown_text)`` on success, ``None`` on failure.
    The caller should fall back to whatever the non-bridge path would do.
    """
    if not _is_wsl():
        return None

    slug = doi.replace("/", "_").replace(":", "_").replace(".", "_")

    # Convert Windows default path to WSL path when running in WSL
    out_root = _DEFAULT_OUT_ROOT
    if len(out_root) >= 2 and out_root[1] == ":":
        out_root = _win_to_wsl_path(out_root)

    out_dir = Path(out_root) / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write a minimal input file for debugging
    input_path = out_dir / "bridge_input.json"
    input_path.write_text(
        json.dumps({"doi": doi, "publisher": publisher, "url": url}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Extract journal name from Crossref metadata
    container_title = ""
    raw_container = metadata.get("container-title")
    if isinstance(raw_container, (list, tuple)):
        container_title = str(raw_container[0]) if raw_container else ""
    else:
        container_title = str(raw_container or "")

    article_title = ""
    raw_title = metadata.get("title")
    if isinstance(raw_title, (list, tuple)):
        article_title = str(raw_title[0]) if raw_title else ""
    else:
        article_title = str(raw_title or "")

    cmd = [
        "cmd.exe",
        "/c",
        _BRIDGE_PYTHON_WIN,
        _BRIDGE_SCRIPT_WIN,
        "--doi", doi,
        "--publisher", publisher,
        "--url", url,
        "--journal", container_title,
        "--title", article_title,
        "--out-dir", _wsl_to_win_path(out_dir.as_posix()),
    ]

    logger.info("bridge: calling Windows → %s", out_dir.as_posix())
    started_at = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning("bridge: Windows script timed out after %.0fs", timeout_seconds)
        return None
    except FileNotFoundError:
        logger.warning("bridge: cmd.exe not available (not WSL?)")
        return None
    except UnicodeError:
        logger.warning("bridge: encoding error decoding subprocess output")
        return None

    elapsed = time.monotonic() - started_at
    if proc.returncode != 0:
        stderr_tail = (
            (proc.stderr or "").strip()[-500:]
        )
        logger.warning(
            "bridge: Windows script exited %d after %.1fs (stderr: %s)",
            proc.returncode, elapsed, stderr_tail,
        )
        return None

    # Read results
    result_path = out_dir / "bridge_result.json"
    if not result_path.exists():
        logger.warning("bridge: no result file at %s", result_path)
        return None

    try:
        with open(result_path, encoding="utf-8") as f:
            result = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("bridge: failed to read %s", result_path)
        return None

    if not result.get("success"):
        logger.warning("bridge: Windows script reported failure: %s", result.get("error"))
        return None

    # Read markdown (convert Windows path → WSL path)
    md_path_str = result.get("md_path")
    md_path = Path(_win_to_wsl_path(md_path_str)) if md_path_str else None
    if not md_path or not md_path.exists():
        logger.warning("bridge: no markdown file at %s → %s", md_path_str, md_path)
        return None

    md_text = md_path.read_text(encoding="utf-8")

    if len(md_text.strip()) < 200:
        logger.warning("bridge: markdown too short (%d chars)", len(md_text))
        return None

    # Assemble ArticleModel
    article = article_from_markdown(
        source=publisher,
        metadata=dict(metadata),
        doi=doi,
        markdown_text=md_text,
    )

    logger.info(
        "bridge: success in %.1fs — %d sections, %d tokens",
        elapsed,
        len(article.sections),
        article.quality.token_estimate,
    )
    return article, md_text


__all__ = ["_is_wsl", "try_bridge_fetch"]
