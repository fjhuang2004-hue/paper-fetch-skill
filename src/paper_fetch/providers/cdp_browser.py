"""CDP Browser — 连接 Chrome DevTools Protocol 获取已登录浏览器中的页面 HTML。

替代 CloakBrowser，用于需要机构登录的出版社（T&F、ACS、Science 等）。
预留登录检测和自动登录接口。

用法：
    from paper_fetch.providers.cdp_browser import CdpBrowser, detect_paywall

    browser = CdpBrowser.connect()        # 连接 localhost:9222 的 Chrome
    html = browser.fetch_html(url, wait_for='.hlFld-Fulltext')
    if detect_paywall(html):
        raise NeedLogin("检测到 paywall，请通过 HZAU CARSI 登录")
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any
from playwright.sync_api import sync_playwright

# ── Paywall 检测关键词（跨出版商通用）──────────────────────────────────────

# 精确关键词：只有这些组合出现时才可能是真 paywall
PAYWALL_PATTERNS = [
    "Access through your institution",
    "Log in to access",
    "Subscribe to view",
    "Purchase article",
    "Check access",
    "You do not have access",
    "Institutional access",
    "access via your institution",
    "Log in to view",
    "Sign in to access",
    "Get access to this article",
]


def detect_paywall(html: str) -> bool:
    """检测 HTML 中是否真的包含 paywall 提示（而非页面 UI 噪音）。

    策略：
    1. 先检查是否有正文内容（hlFld-Fulltext 等）
    2. 如果有正文 → 肯定不是 paywall
    3. 如果没有正文 + 有关键词 → 可能是 paywall
    """
    has_body = (
        "hlFld-Fulltext" in html
        or "article-body" in html
        or "articleBody" in html
        or "fulltext" in html.lower()
    )

    if has_body:
        return False

    lower = html.lower()
    return any(pattern.lower() in lower for pattern in PAYWALL_PATTERNS)


# ── CDP 浏览器核心类 ─────────────────────────────────────────────────────

class CdpBrowser:
    """通过 CDP 连接到正在运行的 Chrome 浏览器。"""

    CDP_URL = "http://localhost:9222"

    def __init__(self, playwright, browser, context):
        self._playwright = playwright
        self._browser = browser
        self._context = context

    @classmethod
    def connect(cls) -> "CdpBrowser":
        """连接 localhost:9222 的 Chrome。如果 Chrome 未启动则报错。"""
        try:
            resp = urllib.request.urlopen(f"{cls.CDP_URL}/json/version", timeout=3)
            version = json.loads(resp.read())
        except Exception:
            raise RuntimeError(
                f"无法连接到 Chrome CDP ({cls.CDP_URL})。\n"
                "请先用以下命令启动 Chrome：\n"
                '  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\ChromeCDP'
            )

        p = sync_playwright().start()
        browser = p.chromium.connect_over_cdp(cls.CDP_URL)

        # 复用已有 context 和页面，或创建新的
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = browser.new_context()

        return cls(p, browser, context)

    def fetch_html(
        self,
        url: str,
        wait_for: str | None = None,
        timeout: int = 30000,
    ) -> str:
        """导航到 URL，等待指定元素（可选），返回完整 HTML。

        Args:
            url: 论文页面 URL
            wait_for: CSS 选择器，等待此元素出现（如 '.hlFld-Fulltext'）
            timeout: 页面加载超时（毫秒）
        """
        # 使用已有页面或创建新页面
        if self._context.pages:
            page = self._context.pages[0]
        else:
            page = self._context.new_page()

        page.goto(url, timeout=timeout, wait_until="domcontentloaded")

        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=15000)
            except Exception:
                pass  # 超时不致命，拿当前 HTML

        page.wait_for_timeout(2000)  # 等 JS 渲染完毕
        return page.content()

    def close(self):
        """关闭 CDP 连接。"""
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass


# ── 自动登录接口（预留）────────────────────────────────────────────────────

class NeedLogin(Exception):
    """检测到 paywall，需要登录。"""

    def __init__(self, message: str, provider: str | None = None):
        super().__init__(message)
        self.provider = provider


def auto_login(
    page,
    provider: str,
    account: str,
    password: str,
    institution: str = "华中农业大学",
) -> bool:
    """（预留）通过 CARSI 自动登录。

    provider: 出版社标识（'tandf', 'acs', 'science' 等）
    account: 学号/工号
    password: CAS 密码

    返回 True 表示登录成功。
    """
    raise NotImplementedError("自动登录尚未实现，请手动登录后重试。")
