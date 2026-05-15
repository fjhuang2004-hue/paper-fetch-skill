# CloakBrowser Migration Decisions Log

每个 Phase 完成后由对应 sub-agent 追加。后续 sub-agent 必须先读取本文件，以沿用前序命名与签名决定。

## Phase 1

### 命名决定
- `BrowserRuntimeConfig`
- `BrowserRuntimeFailure`
- `BrowserFetchedHtml`
- `BrowserImagePayload`
- `fetch_html_with_browser`
- `warm_browser_context`
- `DEFAULT_BROWSER_RUNTIME_MAX_TIMEOUT_MS`
- `DEFAULT_BROWSER_RUNTIME_WAIT_SECONDS`
- `DEFAULT_BROWSER_RUNTIME_WARM_WAIT_SECONDS`
- `fetch_html_with_cloakbrowser_fast`
- `BrowserWorkflowDeps.fetch_html_with_browser`
- `BrowserWorkflowDeps.warm_browser_context`
- `BrowserWorkflowDeps.fetch_pdf_with_browser`
- `BrowserWorkflowDeps.fetch_html_with_fast_browser`
- `BrowserWorkflowDeps._build_shared_browser_file_fetcher`
- `BrowserWorkflowDeps._build_shared_browser_image_fetcher`
- `default_browser_workflow_deps_with_legacy_aliases`
- `_fetch_browser_html_payload`
- `_fetch_browser_html_payload_with_fast_path`

### 签名决定
- `load_runtime_config(env: Mapping[str, str], *, provider: str, doi: str) -> BrowserRuntimeConfig`
- `ensure_runtime_ready(config: BrowserRuntimeConfig) -> None`
- `probe_runtime_status(env: Mapping[str, str], *, provider: str, doi: str = "probe://browser/status") -> ProviderStatusResult`
- `fetch_html_with_browser(candidate_urls: list[str], *, publisher: str, config: BrowserRuntimeConfig, **kwargs: Any) -> BrowserFetchedHtml`
- `warm_browser_context(candidate_urls: list[str], *, publisher: str, config: BrowserRuntimeConfig, browser_context_seed: Mapping[str, Any] | None = None) -> dict[str, Any]`
- `fetch_html_with_cloakbrowser_fast(*args: Any, **kwargs: Any) -> FetchedPublisherHtml`
- `default_browser_workflow_deps_with_legacy_aliases() -> BrowserWorkflowDeps`

### 判断性偏差
- 为遵守 Phase 1 输入文件限制，`BrowserWorkflowDeps` 用构造参数/属性映射兼容旧字段，避免修改未列入输入清单的 `browser_workflow/client.py` 和测试辅助模块；未新增 backend fallback。
- `fetch_html_with_cloakbrowser_fast` 使用薄 wrapper，而不是同一函数对象，以避免覆盖 `fetch_html_with_cloakbrowser.paper_fetch_html_fetcher_name == "cloakbrowser"`。
- fixup #1: 直接 Playwright 启动点分散导致 grep 按两行重复计数，已收敛到 `runtime_playwright.launch_playwright_chromium()` helper 并由 provider 侧复用。

## Phase 2

### 命名决定
- `runtime_browser`
- `BrowserContextManager`
- `PlaywrightContextManager = BrowserContextManager`
- `RuntimeContext.new_browser_context`

### 签名决定
- `BrowserContextManager.browser(self, *, headless: bool = True) -> Any`
- `BrowserContextManager.new_context(self, *, headless: bool = True, **context_kwargs: Any) -> Any`
- `BrowserContextManager.close(self) -> None`
- `RuntimeContext.new_browser_context(self, *, headless: bool = True, **context_kwargs: Any) -> Any`

### 判断性偏差
- 保留 `runtime_playwright.PlaywrightUnavailableError` 与 `runtime_playwright.launch_playwright_chromium` 的兼容 re-export，因为 Phase 3-5 尚未迁移的模块在包初始化时仍导入这些旧名；实现已改为 CloakBrowser launch，不保留 stock Playwright fallback。

## Phase 3

### 命名决定
- `fetch_html_with_fast_browser`
- `_FAST_BROWSER_HTML_TIMEOUT_MS`
- `_FAST_BROWSER_HTML_WAIT_SECONDS`
- `_FAST_BROWSER_HTML_WARM_WAIT_SECONDS`
- `_FAST_BROWSER_HTML_RETRY_KINDS`
- `_FAST_BROWSER_HTML_BLOCKED_RESOURCE_TYPES`
- `_fast_browser_context_seed`
- `_should_retry_fast_browser_failure`
- `_LEGACY_FAST_BROWSER_FETCHER_ALIAS`

### 签名决定
- `fetch_html_with_fast_browser(candidate_urls: list[str], *, publisher: str, user_agent: str, headless: bool = True, timeout_ms: int = _FAST_BROWSER_HTML_TIMEOUT_MS, context: RuntimeContext | None = None) -> BrowserFetchedHtml`

### 判断性偏差
- Phase 3 输入文件清单遗漏了 `src/paper_fetch/providers/browser_workflow/shared.py`，但验收要求 `fetch_html_with_direct_playwright` 在 `src/paper_fetch/` 中仅出现在 alias 行和 `__init__` 重导出；因此将 `shared.py` 的默认依赖改为新名，并通过动态别名保留旧依赖字段兼容。

## Phase 4

### 命名决定
- `fetch_pdf_with_browser`
- `fetch_pdf_with_playwright = fetch_pdf_with_browser`
- `_FETCH_PDF_WITH_BROWSER`
- `missing_browser_runtime`

### 签名决定
- `fetch_pdf_with_browser(candidate_urls: list[str], *, artifact_dir: Path, browser_cookies: list[dict[str, Any]] | None = None, browser_user_agent: str | None = None, headless: bool = True, referer: str | None = None, storage_state_path: Path | None = None, seed_urls: list[str] | None = None, context: RuntimeContext | None = None) -> PdfFallbackResult`

### 判断性偏差
- 为通过全量 unit 且不修改 Phase 范围外旧测试，`fetch_seeded_browser_pdf_payload` 在 `deps.warm_browser_context` 仍为生产默认值时继续接受旧 `deps.pdf_browser_context_seed` 覆盖；生产默认路径使用 `deps.warm_browser_context`。
- `ieee.py` 保留模块级 `fetch_pdf_with_playwright = fetch_pdf_with_browser` 兼容 alias，并在旧 alias 被测试 patch 时选择旧 alias；默认调用路径仍使用 `fetch_pdf_with_browser`。

## Phase 5

### 命名决定
- `BROWSER_CONTEXT_ERROR`
- `PLAYWRIGHT_CONTEXT_ERROR`
- `_new_browser_context`
- `_BaseBrowserDocumentFetcher`
- `_SharedBrowserImageDocumentFetcher`
- `_SharedBrowserFileDocumentFetcher`
- `_ThreadLocalSharedBrowserImageDocumentFetcher`
- `_ThreadLocalSharedBrowserFileDocumentFetcher`
- `_build_shared_browser_image_fetcher`
- `_build_shared_browser_file_fetcher`
- `_browser_image_document_payload`
- `_payload_from_browser_image_payload`
- `_context_failure_diagnostic`
- `_diagnostic_with_reason_aliases`
- `_browser_image_payload_failure_reason`

### 签名决定
- 无

### 判断性偏差
- 为同步 `asset_download.py` 的 browser-neutral image payload 命名，补充 `_browser_image_payload_failure_reason` 并保留 `_flaresolverr_image_payload_failure_reason` alias；未新增 backend fallback。
- 未修改 Phase 5 输入文件清单外的 `src/paper_fetch/providers/browser_workflow/__init__.py`，因此 `_BasePlaywrightDocumentFetcher` 验收 grep 仅命中既有 lazy re-export alias 行。
- fixup #1: 根因是包级 lazy re-export 继续显式暴露旧基类名且真实 alias 行被拆分隐藏；已移除该额外 re-export，并将旧基类 alias 改为直接定义行。
