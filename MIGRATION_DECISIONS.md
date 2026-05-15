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
