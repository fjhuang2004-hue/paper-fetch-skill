# Paper Fetch Skill 当前架构与业务流程

Date: 2026-05-12

## 状态说明

当前分支应视为这套架构的已落地基线。

- 代码主体位于 `src/paper_fetch/`
- `paper-fetch` 是稳定 CLI 入口
- `paper-fetch-mcp` 是稳定 stdio MCP server 入口
- `skills/paper-fetch-skill/` 是静态 thin skill bundle

公共变更历史统一记在 `CHANGELOG.md`。这份文档只描述当前系统如何工作、层次如何分工，以及后续扩展时应遵守的边界。

## Decision

这个仓库的最佳形态仍然是：

```text
可复用核心库 + CLI + MCP adapter + thin skill
```

原因很直接：

- 核心价值在于论文抓取与转换逻辑，而不是某一种 agent transport
- CLI 仍然是最直接的人工调试和 smoke 入口
- MCP 很适合作为结构化工具层，但不应该持有业务逻辑
- skill 应只负责引导 agent 使用工具，而不是承载运行时实现

## 这份文档解决什么，不解决什么

这份文档解决：

- 当前系统有哪些层
- 从输入到输出的端到端业务流程
- 关键数据契约各自扮演什么角色
- 哪些例外会影响调用方理解结果
- 新增能力时应该改哪一层

这份文档不解决：

- 每个 provider 的全部配置变量
- FlareSolverr 的操作细节
- 所有历史设计演进过程

## 当前系统分层

### 1. CLI 层

入口：`src/paper_fetch/cli.py`

职责：

- 解析命令行参数
- 组装 `FetchStrategy` 与 `RenderOptions`
- 通过 `FetchPipeline` 创建/关闭 `RuntimeContext` 并调用 service 层
- 控制 stdout / stderr / 输出文件 / 退出码

不负责：

- provider 选择
- 正文抓取策略
- MCP 序列化

### 2. MCP 层

入口：`src/paper_fetch/mcp/server.py`、`src/paper_fetch/mcp/tools.py`

职责：

- 暴露 MCP tools、prompts 与 resources
- 校验工具参数
- 把 service 结果序列化成 JSON-safe payload
- 通过 `FetchCache` 管理 fetch-envelope sidecar / cache resources
- 通过 `FetchPipeline` cache hooks 复用 CLI/MCP 共享的 fetch lifecycle
- 管理 progress、structured log、cancellation

实现边界：

- stdio transport 由 MCP 层包装成后台 stdin reader + async stream pump，避免同步 stdin 阻塞事件循环。
- `src/paper_fetch/mcp/tools.py` 是兼容 facade；结果封装、structured log bridge、cache payload、fetch payload 和 batch runner 分别拆到 `results.py`、`log_bridge.py`、`cache_payloads.py`、`fetch_tool.py`、`batch.py`。
- `fetch_paper` 和批量工具会把阻塞抓取工作放到有界 `ThreadPoolExecutor`，并在 MCP 事件循环里继续处理 progress、structured log 和 cancellation；批量工具保持输入顺序、rate limit 后停止提交新任务、已提交任务完成后返回已有结果。
- async `fetch_paper` 用 `RuntimeContext(cancel_check=...)` 创建 cancel-aware `HttpTransport`，service/workflow 只消费 transport，不直接依赖 MCP cancellation 机制。
- `server_compat.py` 集中封装 FastMCP private SDK surface（resource registry、initialization options、stdio run），让 SDK 私有字段变化时失败点可读且集中。

不负责：

- provider 路由决策
- 正文抓取瀑布
- Markdown 转换细节

### 3. Skill 层

入口：`skills/paper-fetch-skill/`

职责：

- 告诉 agent 什么时候调用哪些 MCP 工具
- 提供薄说明和引用文档

不负责：

- 安装依赖
- 实际抓取逻辑
- provider 配置

### 4. Service Facade 层

入口：`src/paper_fetch/service.py`

当前 `service.py` 只保留公共入口与兼容导出：

- 暴露 `FetchStrategy`、`PaperFetchFailure`
- 暴露 `RuntimeContext`
- 暴露 `resolve_paper()`、`probe_has_fulltext()`、`fetch_paper()`
- 兼容测试与外层调用方需要的 helper re-export

不再负责：

- provider route 细节判断
- `raw_payload.metadata[...]` 这种 magic key 协议
- 通用 HTML 提取细节
- provider payload、Springer HTML 或 MCP sidecar cache 的具体写盘策略

### 5. Workflow 编排层

入口：`src/paper_fetch/workflow/`

这是新的业务编排主脑，明确拆成 5 个子职责：

- `resolution`
  - 负责 resolve、歧义处理、DOI 归一化
- `metadata`
  - 负责 Crossref / publisher metadata merge；底层 Crossref HTTP lookup owner 是 `paper_fetch.metadata.crossref.CrossrefLookupClient`
- `routing`
  - 负责 provider 候选、probe、fallback eligibility
- `fulltext`
  - 负责 provider 主链与 abstract-only / metadata-only fallback，并通过 `ArtifactStore` 应用 provider artifact 写盘策略与诊断
- `rendering`
  - 负责 `FetchEnvelope`、`source_trail` 派生、最终结果组装
- `pipeline`
  - 负责 CLI/MCP 共享的 `RuntimeContext` 生命周期、service 调用、可选 cache hook 与 Markdown 保存 hook
- `request_builder`
  - 负责 CLI/MCP 共享的 `FetchPipelineRequest` 装配，统一 context 派生的 env、transport、clients 与 cancel_check 应用规则

`RuntimeContext` 是 service/workflow 的显式运行时依赖容器，持有 `env`、`transport`、`clients`、`download_dir`、`cancel_check`、`artifact_store`、adapter 可选 `fetch_cache`，以及单次 fetch 生命周期内的 `parse_cache`、`session_cache` 和 `stage_timings`。Playwright 生命周期由 `paper_fetch.runtime_playwright.PlaywrightContextManager` 管理；`RuntimeContext` 只保留 `playwright_browser()`、`new_playwright_context()` 和 `close_playwright()` 委托方法。PNAS direct HTML preflight、browser-workflow 资产 fetcher 与 PDF/ePDF fallback 可复用同一个 browser，但仍按阶段创建隔离 context/page。公开 service API 不再接受旧 `env` / `transport` / `clients` / `download_dir` keyword；调用方必须先构造 `RuntimeContext` 并通过 `context=` 传入。CLI、MCP 与 devtools 都在自己的 facade 层解析外部参数；CLI/MCP 的 fetch 入口通过 `paper_fetch.workflow.request_builder.build_fetch_pipeline_request()` 统一装配 request，再交给 `paper_fetch.workflow.pipeline.FetchPipeline` 创建运行时、调用 service、关闭运行时，并把 MCP sidecar cache 保留为 adapter hook。

### 6. Extraction 层

入口：`src/paper_fetch/extraction/html/`

职责：

- 暴露通用 HTML 解析与 metadata 提取接口
- 暴露 provider 可复用的 shared extraction helpers
- 为 resolve 层提供纯 extraction 依赖边界
- 通过 `paper_fetch.extraction.html.landing.fetch_landing_html()` 统一 DOI/URL landing HTML fetch、decode、metadata extraction、final URL、status/header 返回结构
- 通过 `paper_fetch.extraction.image_payloads` 统一图片 MIME 与 JPEG/PNG/GIF/WebP 尺寸识别

<a id="extraction-stage-module-map"></a>

#### Extraction 阶段映射

`docs/extraction-rules.md` 中的受控阶段 token 与 canonical owner 的映射如下。新增提取 / 渲染规则时，优先把行为挂到这里列出的 owner；provider 层只做 publisher adapter，不新增平行 helper 入口。

| 阶段 token | Canonical module / owner | 规则范围 |
| --- | --- | --- |
| `metadata` | `paper_fetch.extraction.html._metadata`、provider metadata adapters、`paper_fetch.metadata.crossref` | 标题、作者、摘要、provider-owned 信号和 redirect stub lookup metadata。 |
| `provider-html-or-xml-extraction` | `paper_fetch.extraction.html.renderer`、`paper_fetch.providers._article_markdown_elsevier_document`、`paper_fetch.providers._springer_html`、`paper_fetch.providers.html_springer_nature`、`paper_fetch.providers._science_html`、`paper_fetch.providers._pnas_html`、`paper_fetch.providers.atypon_browser_workflow`、`paper_fetch.providers._atypon_browser_workflow_profiles`、`paper_fetch.providers._wiley_html`、`paper_fetch.providers._ams_html`、`paper_fetch.providers.ieee` | publisher HTML/XML 到中间结构的提取；HTML provider 通过 renderer facade 复用 Markdown 渲染 / sidecar 编排，provider 层只保留 container/profile/postprocess 差异。 |
| `html-cleanup` | `paper_fetch.extraction.html.cleanup_policy.CleanupPolicy`、`paper_fetch.extraction.html._runtime`、`paper_fetch.extraction.html.inline`、provider cleanup policy | 站点 chrome、UI 噪声、caption fallback 和正文清洗；运行时通过 `CleanupPolicy` / `html_cleanup_rules()` 合成通用规则与 provider 扩展。 |
| `availability-quality` | `paper_fetch.extraction.html.availability_policy.AvailabilityPolicy`、`paper_fetch.quality.html_availability`、`paper_fetch.quality.html_signals` | fulltext / abstract-only 判定、正文充分性度量和 provider HTML availability signal。 |
| `section-classification` | `paper_fetch.extraction.section_hints`、`paper_fetch.extraction.html.semantics` | section kind、frontmatter、back matter、availability 与 section hints。 |
| `article-assembly` | `paper_fetch.models`、`paper_fetch.models.builders`、`paper_fetch.models.schema` | 中间结构合并成 `ArticleModel`。 |
| `asset-discovery` | `paper_fetch.extraction.html.assets`、`paper_fetch.providers._html_asset_engine`、`paper_fetch.extraction.html.figure_links`、`paper_fetch.extraction.html.provider_rules`、provider asset policies | figure、table、formula、supplementary 等资产候选识别；publisher-specific supplementary 文本 token 通过 `ProviderHtmlRules` 扩展。 |
| `asset-download` | `paper_fetch.extraction.html.assets.download`、`paper_fetch.extraction.html.assets.state`、`paper_fetch.extraction.html.assets.requester`、`paper_fetch.providers.browser_workflow.fetchers`、provider asset clients | 资产候选下载、candidate/attempt/resolution 状态机、cookie-aware opener/request 和 provider-owned 下载链路。 |
| `asset-validation` | `paper_fetch.extraction.image_payloads`、`paper_fetch.extraction.html.assets`、`paper_fetch.models.Quality` | 真实图片校验、尺寸阈值、preview acceptance 和失败诊断。 |
| `asset-link-rewrite` | `paper_fetch.extraction.html.figure_links`、CLI / model asset link rewrite helpers | 远程 / 绝对资产链接改写为本地 Markdown 可用链接。 |
| `table-rendering` | `paper_fetch.extraction.html.tables`、`paper_fetch.providers._article_markdown_elsevier_document` | HTML/XML 表格展平、降级和语义损失标记。 |
| `formula-rendering` | `paper_fetch.extraction.html.formula_rules`、`paper_fetch.extraction.html.provider_rules`、`paper_fetch.providers._article_markdown_math`、`paper_fetch.formula.convert` | MathML / LaTeX / 公式图片 fallback 渲染；publisher-specific formula class/selector 通过 `ProviderHtmlRules` 扩展。 |
| `markdown-normalization` | `paper_fetch.models.markdown`、`paper_fetch.providers._atypon_browser_workflow_postprocess`、`paper_fetch.providers._science_html`、`paper_fetch.providers._wiley_html`、`paper_fetch.extraction.html._runtime`、`paper_fetch.extraction.html.renderer` | Markdown 块边界、空白、行内语义和去重。 |
| `references-rendering` | `paper_fetch.providers._html_references`、`paper_fetch.providers._article_markdown_elsevier_document`、`paper_fetch.markdown.citations` | 参考文献抽取与渲染。 |
| `final-rendering` | `paper_fetch.models.render`、`paper_fetch.models.ArticleModel.to_ai_markdown`、`paper_fetch.mcp.schemas` | 最终 Markdown / MCP payload 输出。 |
| `artifact-storage` | `paper_fetch.artifacts.ArtifactStore`、`paper_fetch.mcp.fetch_cache` | 原始 payload、publisher HTML、下载资产和 fetch-envelope sidecar 落盘。 |

关键约束：

- `resolve/query.py` 不再 import `providers.*`
- HTML parsing / markdown extraction 不应再通过 provider 模块向上泄漏
- HTML-to-Markdown 的通用编排入口是 `paper_fetch.extraction.html.renderer`；provider-specific 模块只能传入已经选定的 HTML fragment、noise profile、renderer/postprocess hook 和 sidecar 策略
- provider-neutral HTML access signals、section semantics、language filtering 已固定在 `paper_fetch.extraction.html.signals`、`paper_fetch.extraction.html.semantics`、`paper_fetch.extraction.html.language`
- landing fetch helper 是 provider-neutral；Springer 仍在 provider 层定义自己的 redirect policy、headers 和 failure mapping，只复用 fetch/decode/metadata extraction
- 图片 payload helper 使用 `filetype` 做 MIME 识别，使用 `imagesize` 做 JPEG/PNG/GIF/WebP 尺寸读取；识别失败时继续表现为 unknown
- HTML table matrix 展开、rowspan/colspan 处理、Markdown table 渲染和列表降级位于 `paper_fetch.extraction.html.tables`；provider 层只负责传入 publisher table 节点和注入位置，不再保留或新增 `paper_fetch.providers._html_tables` 这类 table helper 兼容 re-export
- HTML-derived citation cleanup 位于 `paper_fetch.markdown.citations`；Springer/Nature inline link unwrap、Extended Data label / figure-line 清理必须由 provider 显式传入扩展 pattern，不进入通用默认规则或通用常量
- 通用 figure/table label core、Extended Data label 和 Extended Data prefix helper 位于 `paper_fetch.common_patterns`；provider-specific caption regex 只有在需要行首锚定、caption remainder、table-page named capture 或 ar5iv/Nature 兼容形态时保留在 provider 内。
- Formula rules 的默认 token/selector 只覆盖通用 HTML 结构；Springer/Nature、Wiley 等 publisher-specific class/selector 必须通过 `ProviderHtmlRules` 和调用方 `noise_profile` 注入
- HTML / Markdown full-text availability verdict 位于 `paper_fetch.quality.html_availability`
- availability verdict、abstract-only/access hint 和 provider/workflow failure/status 的运行时 reason code 分别集中在 `paper_fetch.quality.reason_codes` 与 `paper_fetch.reason_codes`；`models.schema.ContentKind` 保持显式 Literal，作为 public wire/schema contract，不从运行时常量生成。
- HTML `HTML_BLOCK_TAGS`、JATS `JATS_BLOCK_LOCAL_NAMES` 和 Elsevier XML block local names 服务三种不同输入标准；它们概念平行但不共享同一个 `BlockElementVocabulary`，除非后续新增 XML dialect 证明抽象能减少真实重复。
- `paper_fetch.extraction.html.semantics.ANCILLARY_HEADINGS` 负责 DOM section 分类，`MARKDOWN_AUXILIARY_HEADINGS` 负责渲染后 Markdown 清理；两者可有词面重叠但不能合并为同一集合。
- 旧 Nature `Methods Summary` / `Online Methods` 归一化只作为渲染后兼容逻辑保留在 section model 层，Springer/Nature provider 仍通过 `ProviderHtmlRules.heading_normalizations` 声明自己的 heading normalizer。
- Research Briefing 这类 authorless 质量豁免位于 `paper_fetch.quality.html_signals`，不挂在通用 HTML extraction rule registry 上
- publisher 私有的 availability override 必须挂在 provider `AvailabilityPolicy` / `ProviderHtmlRules.availability` facade；promo / front-matter token 扩展必须挂在 provider cleanup/front-matter 子规则，`html_availability` 只负责通用结构分析和驱动注册回调
- HTML container scoring / selection / cleanup 也位于 `paper_fetch.quality.html_availability`；provider-owned browser workflow 只能通过 selection policy 传入评分 profile、完整祖先优先、页面级 container 避让与 provider refine hook
- section hint heading key、dict/object coercion 与顺序匹配位于 `paper_fetch.extraction.section_hints`，HTML semantics 层复用该实现；`models.SectionHint` 只是 dataclass 适配层
- 旧的 `paper_fetch.providers._html_access_signals`、`_html_availability`、`_html_citations`、`_html_semantics`、`_html_tables` 与 `_language_filter` 兼容转发入口已移除；测试和新代码必须直接使用上述 canonical owner

### 7. Provider 层

入口：`src/paper_fetch/providers/`

职责：

- 各 provider 的 metadata / fulltext / asset 下载适配
- provider 自身格式到 `ArticleModel` 的转换
- provider 本地可用性诊断
- 返回 typed provider result，而不是依赖无类型 metadata 口袋回传内部状态

当前固定契约包括：

- `ProviderContent`
- `ProviderArtifacts`
- `ProviderFetchResult`

能力边界通过 `paper_fetch.providers.protocols` 表达：`MetadataProvider`、`FulltextProvider`、`RawFulltextProvider`、`StatusProvider` 和 `AssetProvider` 用于 workflow typing；`ProviderClient` 仍是 provider 可继承的 convenience base class，不是 registry/runtime 的唯一抽象边界。

Provider fulltext 内部链路统一接收同一个 `RuntimeContext`：workflow 调用 `FulltextProvider.fetch_result()` 时必须传入 `artifact_store=` 与 `context=`，不再做运行时签名反射或无 `artifact_store` 分支；`fetch_result` 会把 context 继续传给 raw fulltext、abstract-only recovery、related assets 和 `to_article_model`。provider 不再暴露旧 `fetch_fulltext()` dict 入口；需要原始 payload 时使用 `fetch_raw_fulltext()`，需要完整 provider 结果时使用 `fetch_result()`。这样 Elsevier XML root、Springer HTML extraction payload、Wiley/Science/PNAS/AMS browser-workflow Markdown extraction、IEEE dynamic HTML 清洗结果以及资产抽取结果可以在同一次 fetch 内 memo；browser workflow 也能复用 runtime browser。缓存只保存派生 payload 或只读 XML root，不跨阶段共享可变 BeautifulSoup tree。

`RawFulltextPayload.metadata` 只保留为 legacy/read-only compatibility view：`route`、`markdown_text`、`warnings`、`source_trail`、diagnostics、browser seed 等结构化字段必须由 `ProviderContent`、`warnings`、`trace`、`merged_metadata` 等 typed fields 传入。新生产路径不得把结构化字段写入 legacy metadata pocket，也不得通过 `raw_payload.metadata[...]` 读取 typed 状态。构造 `RawFulltextPayload(metadata={...})` 不再把 legacy magic keys 注入结构化字段，只允许非结构化 passthrough metadata 留在导出里。

Provider 身份与能力配置统一来自 `paper_fetch.provider_catalog.PROVIDER_CATALOG`。新增 provider 时，应先补 `ProviderSpec`，再接入 provider client；routing、默认资产策略、MCP status 顺序和 registry 都从 catalog 派生。

Crossref 的 provider adapter 位于 `paper_fetch.providers.crossref.CrossrefClient`，继续保留 public import path；resolve 与 provider adapter 共同依赖 `paper_fetch.metadata.crossref.CrossrefLookupClient`，避免 resolution 层反向复用 provider 层。

架构测试会阻止已删除的 legacy surface 回流：service 不得重新接收旧 runtime keyword，provider 不得重新定义 `fetch_fulltext()` dict 入口，生产代码不得读取 `raw_payload.metadata[...]` magic keys，provider-neutral 层不得 import `paper_fetch.providers._*`，测试不得重新 import 旧 `_html_*`、`_language_filter`、`_atypon_browser_workflow`、`_atypon_browser_workflow_html`、`paper_fetch.extraction.html._assets`、`paper_fetch.providers.html_assets`、`paper_fetch.providers.science_html`、`paper_fetch.providers.pnas_html`、`paper_fetch.providers.springer_html` 或 `paper_fetch.providers.wiley_html` compatibility modules。provider catalog 仍是 provider 身份、状态顺序和 registry client factory 的单一事实来源。

### 8. Runtime / Artifact / Cache 边界

入口：`src/paper_fetch/runtime.py`、`src/paper_fetch/artifacts.py`、`src/paper_fetch/mcp/fetch_cache.py`

职责：

- `RuntimeContext` 显式承载 env、transport、clients、download_dir、cancel_check 等运行时依赖。
- `RuntimeContext.parse_cache` 是进程内、单 context 生命周期的解析 memo：key 包含 provider、role、source、body sha256、parser 和配置指纹；dict/list 读取时返回拷贝，XML root 仅作为只读对象复用。
- MCP `fetch_paper` 和 batch 工具必须复用同一个 `RuntimeContext` 派生出的 env、transport、provider clients、download_dir 与 cancel_check；调用 service 时只传入完整 context，不再向 service 传旧 `transport` / `env` / `clients` / `download_dir` 回退参数。
- `ArtifactStore` / `DownloadPolicy` 管理 provider PDF/binary local copy、Springer HTML `original.html` copy、Markdown 保存、provider asset warning/source-trail 诊断，以及 fetch-envelope/cache-index JSON 的原子写入。
- `FetchCache` 管理 MCP fetch-envelope sidecar reuse/write 语义和 cache index refresh；sidecar version、`EXTRACTION_REVISION` 校验、resource URI 与 scoped cache resource 语义保持稳定，实际 JSON materialization 委托给 `ArtifactStore`。

### 9. Transport 层

入口：`src/paper_fetch/http/__init__.py`

职责：

- HTTP 请求
- 连接复用与同 host 有界并发
- 进程内短 TTL GET 缓存与可选磁盘 textual GET 缓存
- 响应体大小限制
- 有限短重试
- 协作式取消检查

`HttpTransport` 仍以本地 request loop 保持 public request options、structured logs、cancel checks、`Retry-After` 最大等待和 `RequestFailure` 形状；瞬时错误与 429 retry policy 由 `urllib3.util.Retry` 表达。连接池通过 `PoolManager(num_pools, maxsize, block=True)` 配置，同 host 由 bounded semaphore 控制；磁盘 textual GET 缓存使用脱敏 cache key，敏感 query 参数继续折叠复用，敏感 header 则用短 SHA-256 digest 区分不同凭据且不落原文，并在 stale 时带 `ETag` / `Last-Modified` 条件请求。磁盘 textual GET 缓存默认按 `4096` 条、`512 MiB`、`30` 天清理，三项上限都可用环境变量独立关闭或覆盖。`cache_stats_snapshot()` 提供线程安全累计计数；live review 的 sample 结果写入执行前后 delta，最终汇总日志保留累计快照。

`paper_fetch.http` 是兼容 facade，继续导出 `HttpTransport`、`RequestFailure`、`RequestCancelledError`、`DEFAULT_*` 常量、cache/text helper 和旧测试使用的私有 dataclass 名称；生产调用方不直接依赖内部子模块。内部边界为：`http/transport.py` 承载 request loop、PoolManager、同 host semaphore 和 structured log；`http/cache.py` 承载 cache key、敏感 header digest、memory/disk textual GET cache、stats 和 prune；`http/retry.py` 承载 429 / transient retry policy、`Retry-After` 解析和 backoff；`http/body.py` 承载 body 读取、gzip 解压、content-type 判断和 preview；`http/errors.py` 承载异常类型、network error detail 和 HTTP error message。

### 10. CI / 回归验证边界

`.github/workflows/ci.yml` 是 CI 命令事实来源。`unit`、`integration` 和手动 `full-golden` job 都不传 `-n 0`，默认复用 `pyproject.toml` 的 `pytest-xdist` 并行配置；本地完整 unit / integration 也应使用同一策略。只有 live MCP、FlareSolverr/browser provider smoke、共享真实 publisher/API 状态或专门排查顺序问题的测试可以串行运行，并且命令旁必须说明原因。

文档中的验证命令也遵守同一边界：常规 unit / integration 示例保持并行；`tests/live` 或真实外部状态 smoke 示例如果使用 `-n 0`，必须明确它依赖真实站点、secrets、本地 FlareSolverr 服务或外部限流状态。更新提取规则文档后仍先运行 `python3 scripts/validate_extraction_rules.py`，再按变更范围运行并行 unit / integration。

## 端到端业务流程

统一主线如下：

```text
service facade
-> workflow.resolution
-> workflow.metadata (uses workflow.routing for route signals and probes)
-> workflow.fulltext
-> workflow.rendering
-> CLI / MCP / cache
```

### 1. resolve

`resolve_paper()` 负责把输入标准化成 `ResolvedQuery`。

支持三类输入：

- DOI
- URL
- 标题

DOI cleanup 保留现有宽松输入清理，再用 `idutils` 做 DOI 校验/规范化辅助；失败时保留清理结果，不收紧召回。标题候选评分继续使用 token Jaccard 权重、confidence threshold 和 ambiguity margin，只把字符串 ratio component 换成 `rapidfuzz.fuzz.ratio`。

它会产出这些关键信息：

- `query_kind`
- `doi`
- `landing_url`
- `provider_hint`
- `candidates`
- `title`

如果标题查询候选不够确定，系统会保留 `candidates`，并由上层返回 `ambiguous`，而不是猜测性继续抓取。

### 2. routing signal

路由优先级固定是：

```text
domain > publisher > DOI fallback
```

信号来源包括：

- URL 域名
- Crossref `landing_page_url`
- Crossref `publisher`
- DOI 前缀

`provider_hint` 表示最优提示，而不是最终来源承诺。

### 3. metadata merge

workflow 会尽可能拿到两类元数据：

- Crossref metadata
- publisher metadata

其中：

- `elsevier` 仍会参与 publisher metadata probe
- `springer`、`wiley`、`science`、`pnas`、`ams`、`ieee`、`copernicus` 不再做 publisher metadata probe

然后执行 primary / secondary merge，得到后续正文抓取所需的统一 metadata 视图。

这一步的结果同时决定：

- 更准确的 `landing_page_url`
- 更稳定的 provider 选择
- metadata-only 结果的最终内容

### 4. provider fulltext

如果选中了 provider，workflow.fulltext 会先尝试 provider 主路径。

典型行为：

- `elsevier`
  - 继续走 `官方 XML/API -> 官方 API PDF fallback`
- `springer`
  - 走 provider 自管 `direct HTML -> direct HTTP PDF`
- `wiley`
  - 走 provider 自管混合工作流；完整顺序见 [`providers.md` 的 Wiley / Science / PNAS / AMS 小节](../providers.md#wiley-science-pnas-browser-workflow)
  - HTML 与 seeded-browser PDF/ePDF 共用浏览器工作流基座；`WILEY_TDM_CLIENT_TOKEN` 可让官方 TDM API PDF lane 在 browser PDF/ePDF fallback 失败或 browser runtime 不可用时继续尝试
- `science`
  - 走 provider 自管浏览器工作流；完整顺序见 [`providers.md`](../providers.md#wiley-science-pnas-browser-workflow)
  - 与 `wiley` 的 HTML / browser PDF/ePDF 路径共用浏览器工作流基座
- `pnas`
  - 走 provider 自管浏览器工作流；完整顺序见 [`providers.md`](../providers.md#wiley-science-pnas-browser-workflow)
  - 与 `wiley` / `science` 的 HTML / browser PDF/ePDF 路径共用浏览器工作流基座
  - 当前只剩 provider-owned 单栈；不再保留额外的 Science-only live harness 或第二套 browser-PDF 实现
- `ieee`
  - 走 provider 自管 `landing metadata / article number -> dynamic HTML endpoint -> direct HTTP PDF fallback -> seeded-browser PDF fallback`
  - dynamic HTML 成功公开为 `ieee_html`；无可用 HTML 但 PDF payload 成功时公开为 `ieee_pdf`
- `arxiv`
  - 走 provider 自管 `arXiv ID 解析 -> arXiv official HTML -> direct HTTP PDF fallback`
  - arXiv API / HTML metadata merge 只作为 enrichment，不是全文主路径节点
  - official HTML 成功公开为 `arxiv_html`；HTML 不可用、返回非 HTML、正文不足或质量检测失败时直接进入 text-only PDF fallback 并公开为 `arxiv_pdf`
- `copernicus`
  - 走 provider 自管 `landing HTML / DOI-derived URL -> NLM/JATS XML -> direct HTTP PDF fallback`
  - XML 成功公开为 `copernicus_xml`；PDF fallback 成功公开为 `copernicus_pdf`
- `ams`
  - 走 provider 自管 `Crossref/DOI landing -> FlareSolverr HTML -> seeded-browser PDF fallback`
  - HTML 成功公开为 `ams_html`；PDF fallback 成功公开为 `ams_pdf`
  - AMS 显式忽略 `citation_xml_url`，不请求 `/doc/...xml`，不走 JATS renderer

`paper_fetch.providers.browser_workflow` 是 Wiley / Science / PNAS / AMS 的 canonical browser workflow facade。它保留 `ProviderBrowserProfile`、`BrowserWorkflowClient`、bootstrap、seeded-browser PDF fallback、article conversion 和 related asset download orchestration 的稳定入口。

底层职责已拆到独立包。`profile`、`bootstrap`、`pdf_fallback`、`article`、`assets`、`client`、`shared`、`html_extraction`、`fetchers` 分别承载 profile、HTML bootstrap、PDF/ePDF fallback、article assembly、asset retry helper、client 基类、URL/signal helper、HTML payload/cache helper 和 Playwright fetcher helper。

旧兼容入口已删除，包括 `paper_fetch.providers.browser_workflow_fetchers.*`、`_browser_workflow_html_extraction.py`、`_browser_workflow_shared.py`、`_browser_workflow_fetchers.py`、`paper_fetch.providers.science_html`、`paper_fetch.providers.pnas_html` 和 `paper_fetch.providers.wiley_html`。新代码只能从 `paper_fetch.providers.browser_workflow.*` 引入 browser workflow orchestration，从 `paper_fetch.providers._science_html` / `_pnas_html` / `_wiley_html` / `_ams_html` 引入 provider-owned HTML 作者提取和 blocking fallback 信号。

`paper_fetch.providers._atypon_browser_workflow_profiles` 是 Atypon-only candidate routing/profile dispatch helper。它支持 provider catalog 中的 `science` / `pnas` / `wiley` / `ams`；候选 URL 模板来自 `ProviderSpec`，provider-owned callback 模块按 `ATYPON_BROWSER_WORKFLOW_PROVIDER_NAMES` 动态导入。`paper_fetch.providers.atypon_browser_workflow` 承载 Atypon browser HTML markdown、asset scopes、normalization 和 postprocess entrypoint，publisher 差异通过 profile callback 分派。

facade 继续 re-export 测试和 provider 已依赖的 patch 点。例如 `load_runtime_config`、`fetch_html_with_flaresolverr`、`fetch_html_with_direct_playwright`、`fetch_pdf_with_playwright`、`extract_atypon_browser_workflow_markdown` 与 shared Playwright fetcher 构造器。

`wiley` / `science` / `pnas` / `ams` 的 HTML 正文图片资产下载也属于这套 provider-owned browser workflow：每个 asset download attempt 内，单个 worker 线程会复用自己的 seeded Playwright browser context，先尝试 full-size/original，全部失败后再用同一线程私有 context 尝试 preview；并发 worker 之间不复用 `RuntimeContext` 持有的共享 browser。PNAS direct Playwright HTML preflight 和 PDF/ePDF fallback 同样通过 `RuntimeContext` 复用 browser，但这只适用于非 threaded 的主流程 Playwright 步骤。通用 HTTP-first 资产下载仍保留给非目标 provider，并由 `paper_fetch.extraction.html.assets.download` 的私有 candidate/attempt/resolution 模型和共享 executor 统一处理 figure、table/formula image 与 supplementary 的 resolve/fallback 流程；网络解析阶段进入 bounded worker pool，文件写入、文件名去重、`source_data/` 分流和失败诊断收集仍按原 asset 顺序串行执行。

这些 provider-owned waterfall 由 `paper_fetch.providers._waterfall` 做轻量编排：runner 只负责按 step 顺序执行、累积 warnings、保留失败 label、组合失败并写入成功/失败 source markers；每个 provider 自己定义 XML、HTML、TDM、PDF 或 browser PDF step 的 payload 和错误映射。`ProviderClient.fetch_result` 是 template-method：base 统一完成 raw payload、local-copy flag、related assets、`to_article_model`、artifacts 和 trace/warning 尾部组装，Browser workflow / Springer 只覆盖 abstract-only recovery 与 provider-managed abstract-only finalize。内部 workflow 调用 `fetch_result` 时总是传入 `artifact_store=`；`fetch_result` 仍保留旧 `output_dir` 位置参数和未传 `artifact_store` 时从 `output_dir` 构造默认 store 的直接调用兼容。

如果正文足够可用，流程在这里结束。

### 5. abstract-only / metadata-only fallback

如果命中了 `elsevier`、`springer`、`wiley`、`science`、`pnas`、`ams`、`ieee`、`arxiv`、`copernicus` 之一：

- workflow.fulltext 只执行该 provider 自己管理的 HTML/PDF waterfall
- provider 返回 `None` 后直接进入 metadata-only fallback
- `springer` / `wiley` / `science` / `pnas` / `ams` / `ieee` 如果只能确认摘要级内容，会直接返回 provider `abstract_only` 结果
- `arxiv`、`copernicus` 与 `elsevier` 保持一致，HTML/XML/PDF 都不可用时进入 metadata-only fallback

如果没有命中这些 official provider：

- 系统仍允许 DOI / Crossref metadata 解析
- 不再尝试任何通用 HTML 正文提取
- `strategy.allow_metadata_only_fallback=true` 时返回 metadata-only 结果
- 否则抛 `PaperFetchFailure`

如果 provider 已经返回 fulltext HTML payload，`ProviderClient.fetch_result()` 会在资产下载和 `ArticleModel` 构造前确保 `content.markdown_text` 已自动生成。provider 可以覆盖 `html_to_markdown()` 使用自己的解析器；默认实现只作为已取得 fulltext HTML 后的兜底转换，不参与未知网页正文发现。

如果没有可返回的 provider `fulltext` / `abstract_only` 结果，并且 `strategy.allow_metadata_only_fallback=true`：

- service 返回 metadata fallback 文章
- `has_fulltext=false`
- `warnings` 中明确提示已降级
- `source_trail` 中带 `fallback:metadata_only`
- public `source` 通常表现为 `metadata_only`；如果 metadata 中有摘要，质量层 `content_kind` 可能是 `abstract_only`

如果关闭这个开关，则抛 `PaperFetchFailure`。

### 6. render / envelope / cache / MCP 暴露

拿到最终 `ArticleModel` 后，workflow.rendering 会构造 `FetchEnvelope`。

当前对外结果新增：

- `trace: list[TraceEvent]`
- `source_trail`
  - 作为兼容字段保留，并与 `trace` 保持同步；旧路径仍可能先写 marker 再转换成 trace
- `warnings`
  - provider、workflow、CLI / MCP 可能在各自阶段追加，最终聚合到 `ArticleModel.quality` 与 `FetchEnvelope`

随后：

- `ArtifactStore` 已在 workflow 阶段处理 provider payload、Springer HTML copy 和 provider asset 诊断
- CLI 仍决定是否请求写 Markdown 文件、是否改写相对资源链接；实际保存动作通过 `FetchPipeline` 的 `MarkdownSaveSpec` 执行
- MCP 通过 `FetchCache` hooks 决定是否复用/写入 fetch-envelope sidecar、是否暴露 resources、是否附带 inline images

## 数据契约与角色边界

### `ResolvedQuery`

作用：

- 表达“输入已经被解析成什么论文候选”
- 为后续 routing 与 metadata 拉取提供标准化入口

不作用于：

- 最终输出格式
- 正文抓取成功与否

### `FetchStrategy`

作用：

- 表达“怎么抓”

当前最重要的字段：

- `allow_metadata_only_fallback`
- `preferred_providers`
- `asset_profile`

它不决定返回哪些 payload；那是 `modes` 的职责。

### `FetchEnvelope`

作用：

- 固定返回形状的公开抓取结果

它始终承载：

- `doi`
- `source`
- `has_fulltext`
- `warnings`
- `source_trail`
- `token_estimate`
- `token_estimate_breakdown`

按 `modes` 决定是否附带：

- `article`
- `markdown`
- `metadata`

### `ArticleModel`

作用：

- 表达 provider 已经转换好的文章正文、资产、references 和质量诊断。
- 统一负责最终 Markdown 渲染中的 token budget、资产附录、references 输出和质量 warnings。

当前重要边界：

- `assets[*].render_state` 决定资产是否可追加到尾部附录；`inline` / `suppressed` 不追加，`appendix` 可追加。
- 正文已内联图片会按 URL、相对路径、后缀和 basename 与资产做等价比较，避免重复渲染。
- 文章组装会先用已下载资产把正文里的远程 figure / table / formula image 链接改写成本地路径，再做 Markdown 图片块边界归一化，避免图片和标题、正文句子或 display math 粘连。
- 结构化 metadata 在进入 front matter 前会解开 HTML entity，避免 `&amp;` 这类站点编码泄漏到标题、作者、期刊或摘要。
- `assets[*].download_tier`、`download_url`、`content_type`、`downloaded_bytes`、`width`、`height` 是下载诊断，不应被下游丢弃。
- 图片 payload MIME/尺寸来自 `filetype` / `imagesize` helper；不能识别时继续不写入宽高，preview acceptance threshold 仍是既有策略。
- `quality.semantic_losses.table_layout_degraded_count` 表示版式降级，`table_semantic_loss_count` 才表示语义内容丢失。

### `provider_status`

作用：

- 在真正抓取前报告本地环境是否就绪

边界：

- 本地检查边界与各 provider check 名称以 [`providers.md`](../providers.md#provider-status-local-boundary) 为准
- IEEE 当前返回 `html_route` 与 `pdf_fallback` 两条 check；具体 mode 写在各 check 的 `details.mode`

### `has_fulltext`

这里要区分两个层面：

1. `fetch_paper().has_fulltext`
   - 完整抓取瀑布之后的最终 verdict
2. `has_fulltext()`
   - MCP 暴露的廉价 probe
   - 只使用更便宜、更弱的信号

这两个值不要求逐案完全一致。

## 关键例外与调用方容易误解的点

### `elsevier` / `springer` / `wiley` / `science` / `pnas` / `ams` / `ieee` / `arxiv` / `copernicus` 不走通用 HTML fallback

这些 provider 的 HTML 逻辑由 provider 内部管理，因此：

- 不存在 public HTML fallback 开关；是否尝试这些主路径由 provider 路由和 `preferred_providers` 控制
- `elsevier` 成功时公开为 `elsevier_xml` 或 `elsevier_pdf`
- `springer` 成功时公开为 `springer_html`
- `wiley` 成功时公开为 `wiley_browser`
- `science` / `pnas` 仍然公开为 `science` / `pnas`
- `ieee` 成功时公开为 `ieee_html` 或 `ieee_pdf`
- `arxiv` 成功时公开为 `arxiv_html` 或 `arxiv_pdf`
- `copernicus` 成功时公开为 `copernicus_xml` 或 `copernicus_pdf`
- 更细的成功细节要看 `source_trail`

### `crossref` 既可能是 source，也可能只是 signal

- 作为 signal 时，用来路由，不代表最终结果来自 Crossref
- 作为底层文章来源时，`ArticleModel.source` 可表现成 `crossref_meta`
- 如果 fulltext 失败后走 metadata fallback，`FetchEnvelope.source` 会映射为 `metadata_only`

### `warnings` 与 `source_trail` 都是契约的一部分

- `warnings` 用于告诉调用方发生了什么降级或限制
- `source_trail` 用于告诉维护者和高级调用方每一步是怎么走的

如果只看正文内容而忽略它们，会误读结果质量。

## 输出与可观测性

### `warnings`

常见内容包括：

- abstract-only / metadata-only 降级
- HTML / provider fallback 提示
- 资产部分下载失败
- preview 资产可接受降级或不可接受 fallback
- formula-only preview fallback
- 表格版式降级 / 语义丢失
- 公式 fallback / missing
- token 截断

### `source_trail`

常见轨迹包括：

- `resolve:*`
- `route:*`
- `metadata:*`
- `fulltext:*`
- `fallback:*`
- `download:*`

### `token_estimate_breakdown`

当前拆成三段：

- `abstract`
- `body`
- `refs`

它帮助 host 决定：

- 要不要截断
- 哪一段最占预算
- 是否要改成 metadata-only / summary-first 策略

### MCP cache resources

MCP 层会把缓存暴露成 resources：

- 默认共享缓存索引
- 默认共享缓存条目
- 显式 `download_dir` 时的 scoped cache resources

`FetchCache` 负责匹配 `prefer_cache=true` 的请求：先 resolve DOI，再按 request modes、strategy、`include_refs`、`max_tokens`、sidecar version 和 `EXTRACTION_REVISION` 复用本地 fetch-envelope；写入时只负责 sidecar payload 语义和 index refresh，文件 materialization 走 `ArtifactStore` 的原子 JSON writer。资源 URI、sidecar JSON shape 和 scoped download_dir entries 保持兼容，让 host 不需要重复抓取相同论文。

MCP `prefer_cache`、`no_download` 与 `save_markdown` 的行为以 [`providers.md`](../providers.md#mcp-download-and-markdown-save) 为准。

MCP resource sync 只在 fetch 实际使用下载目录，或 Markdown 保存步骤成功落盘后执行；没有新落盘产物的 fetch 不刷新 cache resources。

## 扩展点：新增能力时应改哪一层

### 新增 provider

应该主要改：

- `src/paper_fetch/providers/`
- `src/paper_fetch/provider_catalog.py`
- 必要时更新 provider-specific extraction / metadata adapter

不应该把 provider 逻辑塞进 CLI 或 MCP 层。

### 新增 MCP surface

应该主要改：

- `src/paper_fetch/mcp/schemas.py`
- `src/paper_fetch/mcp/tools.py`
- `src/paper_fetch/mcp/server.py`

如果需要真正的新抓取逻辑，应先落到 service 层。

### 新增渲染能力

如果是正文渲染或资产展示能力，应优先改：

- `src/paper_fetch/models/`
- provider 到 `ArticleModel` 的转换逻辑

而不是让 CLI 或 MCP 自己拼装业务结果。

## 相关文档

- [`../../README.md`](../../README.md)
- [`../providers.md`](../providers.md)
- [`../deployment.md`](../deployment.md)
- [`probe-semantics.md`](probe-semantics.md)
