# 新增 Provider 开发标准

这份文档是后续接入新出版社 provider 的工程标准。目标是让新 provider 一开始就接入当前架构边界，减少后续因为路由、typed payload、资产语义、测试夹具或文档事实来源不一致造成的返工。

本文只描述新增 provider 的开发流程和验收标准。当前已支持 provider 的能力矩阵、运行时行为和环境变量仍以 [`providers.md`](providers.md) 为准；系统分层、typed contract 和 owner 边界仍以 [`architecture/target-architecture.md`](architecture/target-architecture.md) 为准；用户可见提取 / 渲染规则仍以 [`extraction-rules.md`](extraction-rules.md) 为准。

## 核心原则

新增 provider 不是新增一段独立抓取脚本，而是接入已有的 provider-owned waterfall：

```text
resolve
-> metadata / routing
-> provider-owned fulltext waterfall
-> provider-managed abstract-only or metadata-only fallback
-> typed ArticleModel / FetchEnvelope rendering
```

必须遵守这些原则：

- Provider 身份、路由信号、默认资产策略、status 顺序和 registry factory 统一来自 `paper_fetch.provider_catalog.PROVIDER_CATALOG`。
- Provider 主链必须返回 typed payload：`ProviderContent`、`ProviderFetchResult`、`ProviderArtifacts`、`warnings`、`trace` 和 `merged_metadata`。
- 不允许通过 `raw_payload.metadata[...]` 读写结构化状态；它只是 legacy/read-only compatibility view。
- Provider 层只做 publisher adapter；通用 HTML、表格、公式、引用、资产验证、availability 判定优先挂到已有 canonical owner。
- HTML / XML / PDF / browser fallback 的顺序由 provider 自己明确声明，并用 `source_trail` 和 warnings 暴露可观测行为。
- 资产失败不能覆盖已成功的正文 Markdown；资产问题应进入 warnings、`article.quality.asset_failures` 和 download trace。
- 新增用户可见提取 / 渲染行为时，必须同步规则文档、fixtures 和测试。
- 新增 provider 的规则和核心测试默认必须基于真实 DOI 文献的 HTML / XML replay；这些文献样本统一放入 `tests/fixtures/golden_criteria/` 并登记 manifest。

## 1. 先写设计，再写 client

开发前先在 issue、TODO 或设计段落中写清楚：

- Provider 名称、公开 `source` 名称和是否 official。
- 路由信号：domain、Crossref publisher alias、DOI prefix，按 `domain > publisher > DOI fallback` 理解优先级。
- 主路径顺序：例如 `landing HTML -> XML -> cleaned HTML -> PDF text-only -> abstract-only`。
- 每一步成功和失败的判定条件，尤其是 access gate、abstract-only、空壳 HTML、非 PDF wrapper 和正文不足。
- 权限边界：是否需要 API key、机构授权、浏览器上下文、Playwright 或 FlareSolverr；不自动登录、不处理 CAPTCHA、不伪造授权。
- `asset_profile` 语义：`none` / `body` / `all` 下分别下载什么，PDF fallback 是否 text-only。
- `probe_status()` 只检查本地条件，不主动打远端 publisher 可用性探测。

如果 provider 是开放获取出版社，默认优先 direct HTTP / XML / HTML；不要为了“更稳”直接引入 browser runtime。只有明确存在动态渲染、CDN 对普通 HTTP 拦截或 challenge runtime 需求时，才考虑 Playwright 或 FlareSolverr。

## 2. 先接 Provider Catalog

新增 provider 的第一批代码改动应该从 `src/paper_fetch/provider_catalog.py` 开始：

- 在 `PROVIDER_CATALOG` 增加 `ProviderSpec`。
- 在 `SOURCE_PROVIDER_MAP` 增加所有公开 `source -> provider` 映射。
- 选择默认 `asset_default`：公开 HTML/XML 路线通常是 `body`；metadata-only 或没有资产能力的是 `none`。
- 选择 `abstract_only_policy`：provider 能可靠返回自己的摘要页时用 `provider_managed`；否则保留 metadata fallback。
- `client_factory_path` 指向最终 client，例如 `paper_fetch.providers.mdpi:MdpiClient`。
- `status_order` 插入稳定顺序，避免 UI / MCP status 抖动。

不要手写新的 provider 常量列表。`preferred_providers`、MCP provider status、registry clients、默认 asset profile 和 provider identity 都应该继续从 catalog 派生。新增后至少补 `tests/unit/test_provider_catalog.py` 的 DOI、domain、publisher 推断样例。

## 3. Client Contract

优先继承 `paper_fetch.providers.base.ProviderClient`，只覆盖必要 hook：

- `fetch_raw_fulltext(doi, metadata, *, context=None) -> RawFulltextPayload`
- `to_article_model(metadata, raw_payload, *, downloaded_assets=None, asset_failures=None, context=None)`
- `html_to_markdown(html_text, source_url, *, metadata, context)`，仅 HTML 路线需要
- `download_related_assets(...)`，仅有资产能力时实现
- `probe_status()`
- `describe_artifacts()`，仅 text-only fallback 或特殊 artifact 策略需要覆盖
- `maybe_recover_fetch_result_payload()`，仅 HTML 抽取后发现 abstract-only 还需要继续 PDF fallback 时覆盖

`ProviderClient.fetch_result()` 已经负责：

- 创建 / 复用 `RuntimeContext`
- 调用 raw payload、abstract-only recovery、HTML 自动 Markdown fallback
- 控制资产下载时机
- 调用 `to_article_model`
- 组装 `ProviderFetchResult`
- 合并 warnings、trace 和 artifacts

新 provider 不应绕开这条 template method 自己拼最终结果。

## 4. Fulltext Waterfall

provider 内部多步骤 fallback 应使用 `paper_fetch.providers._waterfall.run_provider_waterfall()`，而不是散落嵌套 `try/except`。

每个 step 要定义：

- `label`
- `run`
- `failure_marker`
- `success_markers`
- `continue_codes`
- `failure_warning`
- `success_warning`

错误类别使用稳定的 `ProviderFailure.code`：

- `no_result`：该路线没有可用全文，可继续 fallback。
- `no_access`：权限或 access gate 不满足，通常继续 provider-managed 降级。
- `rate_limited`：远端限流，保留 `retry_after_seconds`。
- `not_configured`：本地缺环境变量、API key、runtime。
- `not_supported`：该 provider 不支持该输入或能力。
- `error`：其它不可归类错误。

成功判定不能只看 HTTP `200`。必须校验 payload 形态和正文充分性，例如：

- XML 有正文 section，而不是只有 metadata。
- HTML 有 provider article container、章节或足够正文段落。
- PDF fallback 返回真实 PDF payload，而不是 HTML access page、JS wrapper 或错误页。
- Markdown 通过 `quality.html_availability` 或结构化 availability 判定，不把 abstract-only 当 fulltext。

## 5. Extraction Owner 复用规则

新增 provider 时优先复用这些 owner，不新增平行 helper：

- Landing HTML：`paper_fetch.extraction.html.landing.fetch_landing_html`
- Metadata parsing：`paper_fetch.extraction.html._metadata`
- HTML-to-Markdown 编排：`paper_fetch.extraction.html.renderer`
- Fulltext / abstract-only 判定：`paper_fetch.quality.html_availability`
- Section hints：`paper_fetch.extraction.section_hints`、`paper_fetch.extraction.html.semantics`
- HTML table：`paper_fetch.extraction.html.tables`
- Citation cleanup：`paper_fetch.markdown.citations`
- Formula rules：`paper_fetch.extraction.html.formula_rules`、`paper_fetch.providers._article_markdown_math`
- Image MIME / dimensions：`paper_fetch.extraction.image_payloads`
- Asset discovery / download：`paper_fetch.extraction.html.assets`
- Final rendering：`paper_fetch.models`

Provider-specific 代码只负责：

- 找到 publisher 的 article container 或 XML root。
- 把 publisher DOM/XML 映射成已有中间结构。
- 在 `paper_fetch.extraction.html.provider_rules` 注册 publisher cleanup profile、Markdown promo tokens、availability site rule、access-block tokens 和必要 alias；不要在 `_runtime.py` 或 `quality/html_profiles.py` 新增 provider 字典分支。
- Provider HTML availability signal 也通过 `paper_fetch.extraction.html.provider_rules` 注册，不再通过 `quality/html_profiles.py` 的 dict 分发。
- 定义 asset scope 和 fallback 候选。
- 把提取结果写入 `ProviderContent.diagnostics`，而不是塞进 legacy metadata。

如果确实需要新增共享能力，应优先放到 canonical owner 模块，并同步 `docs/architecture/target-architecture.md` 的阶段映射和 `docs/extraction-rules.md` 的规则说明。

## 6. 资产下载标准

`asset_profile` 的语义必须稳定：

- `none`：不下载资产；Markdown 保留正文 caption 或 captions-only 退化结果。
- `body`：只下载 provider-cleaned 正文 scope 中的 figure、正文表格原图和可识别公式图片 fallback。
- `all`：在 `body` 基础上额外下载明确 supplementary / supporting / multimedia scope 中的附件。

Supplementary discovery 必须来自明确附件 scope。不能在整篇正文里凭 `data`、`code`、`.csv`、`.zip`、`.mp4`、`.pdf` 等词面或后缀全局扫描并归为 supplementary。

资产输出和失败诊断必须保留：

- `kind`
- `section`
- `render_state`
- `download_tier`
- `download_url`
- `original_url`
- `preview_url`
- `full_size_url`
- `content_type`
- `downloaded_bytes`
- `width`
- `height`
- failure 的 `status`、`content_type`、`title_snippet`、`body_snippet`、`reason`

正文已内联消费的图表应设置 `render_state="inline"`，避免最终 Markdown 尾部重复追加 `Figures` / `Tables`。PDF fallback 如果只是 text-only，必须通过 `ProviderArtifacts` 标记跳过相关资产，并给出可见 warning。

## 7. Runtime 与请求策略

所有 provider 网络请求应走 `RuntimeContext.transport` / `HttpTransport`，不要直接用 `requests`、`urllib` 或临时 session。

建议规则：

- Fulltext 路线使用 `DEFAULT_FULLTEXT_TIMEOUT_SECONDS`。
- 可重试的 publisher GET 使用 `retry_on_transient=True`。
- API 或限流敏感路线根据现有 provider 模式启用 rate-limit retry。
- 请求头用 `build_user_agent(env)` 构造稳定 UA。
- `context.parse_cache` 用于同一次 fetch 内复用 XML root、HTML extraction payload、asset extraction payload。
- Playwright browser 只能通过 `RuntimeContext` 或现有 browser workflow helper 管理；并发资产 worker 不复用共享 browser context。
- 并发资产 worker 中创建的 thread-local Playwright page/context/browser/manager 必须在同一个 worker 线程内关闭；不能在主线程统一关闭 worker 线程创建的 sync Playwright 对象，否则容易残留 Chrome for Testing 子进程。

不要新增需要全局状态的缓存或隐藏环境变量。新环境变量必须写入 provider docs、status check、部署说明或 `.env.example`，并在 tests 中覆盖缺失和配置成功两种状态。

## 8. 测试标准

新增 provider 至少需要这些测试层：

- Catalog / identity：`tests/unit/test_provider_catalog.py` 覆盖 domain、publisher alias、DOI prefix、source 映射、默认 asset profile、registry client。
- Request options：覆盖 timeout、headers、retry、API key 或 browser runtime 配置。
- Waterfall：覆盖主路径成功、第一路径失败后 fallback 成功、全部失败后降级、`source_trail` 和 warnings。
- Extraction：用真实 replay 或最小 scenario 覆盖标题、作者、摘要、章节、表格、公式、references、availability 判定。
- Assets：覆盖 `none` / `body` / `all`、正文图、表格图、公式 fallback、supplementary scope、失败诊断和本地链接改写。
- Provider-managed fallback：确认该 provider 失败后不走通用 HTML fallback，而是 provider-managed abstract-only 或 metadata-only。
- Status：覆盖本地 ready、not_configured、partial 或 error。
- CLI / MCP：通常不需要新增专门列表测试，除非 provider 引入新公开参数；provider 名应由 catalog 自动进入 allow-list。

真实文献样本标准：

- 用户可见规则、provider markdown 抽取、availability、references、表格、公式和资产语义的核心测试，默认必须基于真实 DOI 文献的 `original.html` 或 `original.xml`。
- 这些真实文献 replay 必须放在 `tests/fixtures/golden_criteria/<doi_slug>/`，并在 `tests/fixtures/golden_criteria/manifest.json` 中登记 DOI、publisher、source URL、资产路径和用途。
- 文档中引用的代表性文献也必须指向 `tests/fixtures/golden_criteria/` 下的 canonical fixture，不能指向 `live-downloads/`、临时导出目录、开发者本机路径或散落 top-level 文件。
- 一个 provider 首次接入时，至少要有覆盖主成功路径的真实文献 fixture；复杂能力最好拆成多篇真实文献覆盖，例如一篇正文结构、一篇表格、一篇公式、一篇 supplementary / asset scope。
- `_scenarios/` 只用于最小结构 contract、边界条件或真实文献难以稳定复现的细分分支；它不能替代 provider 主路径的真实文献证据。
- `tests/fixtures/block/` 只用于 access gate、paywall、abstract-only、challenge 等负样本页面；负样本同样应尽量保留真实页面状态。
- 如果某条规则暂时只能用最小 scenario 覆盖，必须在 `docs/extraction-rules.md` 的“无稳定 DOI 样本规则汇总表”说明原因、后续补真实文献样本的触发条件和候选 fixture。

Fixtures 规则：

- DOI-backed replay 放在 `tests/fixtures/golden_criteria/<doi_slug>/`。
- 最小结构场景放在 `tests/fixtures/golden_criteria/_scenarios/<scenario_slug>/`。
- access gate、paywall、abstract-only 等负样本放在 `tests/fixtures/block/`。
- 新 fixture 必须同步 `tests/fixtures/golden_criteria/manifest.json` 和 fixture catalog。
- 不从 `live-downloads/`、临时目录或散落 top-level 文件读取测试样本。

Golden corpus 规则：

- provider 稳定后，补 representative fixture 和 `expected.json`。
- `expected.json` 应锁用户可见 summary，不锁无意义格式噪声。
- live-only 样本放入 live sample 集合，并受 `PAPER_FETCH_RUN_LIVE=1` 保护。
- 预期 metadata-only 或当前不支持的样本，要在 manifest 标注 expected outcome，避免进入 provider bug 队列。

常规验证命令：

```bash
PYTHONPATH=src python3 -m pytest tests/unit -q
```

如果改了 `docs/extraction-rules.md`，还必须运行：

```bash
python3 scripts/validate_extraction_rules.py
```

只有 live 测试、共享外部状态测试或排查顺序问题时才串行运行，并在结果中说明原因。

## 9. 文档同步标准

新增 provider 合并前必须同步：

- `docs/providers.md`
  - 能力矩阵
  - routing 信号
  - fulltext waterfall
  - fallback 语义
  - `asset_profile` 行为
  - 环境变量和 status 说明
- `docs/extraction-rules.md`
  - 任何用户可见提取 / 渲染新规则
  - 新 fixture、Owner、阶段、测试
- `docs/architecture/target-architecture.md`
  - 只有新增 canonical owner、阶段边界或 runtime contract 时才更新
- `docs/deployment.md` / `.env.example`
  - 只有新增用户必须配置的环境变量时更新
- `tests/provider_benchmark_samples.py` 或 live samples
  - 有稳定 live smoke 样本时更新
- `CHANGELOG.md`
  - 对用户可见的新 provider 能力和限制做简短记录

`references/api_notes.md` 和 `references/routing_rules.md` 只保留 API 约束或历史草图，不作为 provider/routing 事实来源。

## 10. 完成定义

新增 provider 只有同时满足以下条件，才算接入完成：

- Provider 已在 catalog 中声明，registry 能构建 client。
- `preferred_providers=["<provider>"]` 可限制进入该 provider 主链。
- 主路径、fallback、warnings、`source_trail` 和公开 `source` 稳定。
- `fetch_paper()` 成功时返回 `ArticleModel`，失败时按策略返回 provider-managed abstract-only 或 metadata-only。
- `asset_profile` 三种模式行为清楚，资产失败不破坏正文。
- `probe_status()` 能解释本地环境是否可用。
- 代表 fixtures、unit tests、必要 integration/golden tests 已补齐。
- 文档已同步，且不与 `providers.md` / catalog 的事实来源冲突。

## 反模式

避免这些实现方式：

- 只靠 DOI 字符串拼全文 URL，不从 landing page 或 Crossref 信号发现 publisher 暴露的链接。
- 把任意 HTML 页面交给通用 extractor，当作 public HTML fallback。
- 用 HTTP `200` 判断成功，不校验 fulltext marker、正文长度、access gate 或 PDF payload。
- 在 provider 里直接拼最终 `FetchEnvelope`，绕过 `ProviderClient.fetch_result()`。
- 把 `route`、`markdown_text`、`source_trail`、diagnostics 写进 `raw_payload.metadata`。
- 为单篇 DOI 写硬编码特判，而不是沉淀为行为规则、fixture 和测试。
- 为已有 canonical owner 再造一套 table、formula、citation、asset 或 availability helper。
- 全文扫描 supplementary 文件后缀，导致正文数据链接、reference PDF 或站点 chrome 被误下载。
- 资产下载失败后把已经成功的正文 Markdown 判为失败。
- 新增 provider 后只改代码，不更新 docs、fixtures 和 status surface。
