# Provider 能力与运行时行为

这份文档解决：

- 各 provider 能做什么、不能做什么
- 运行时如何做路由和回退
- 默认输出策略与下载行为
- 配置项、环境变量、限速与缓存护栏

这份文档不解决：

- agent runtime 的安装与 MCP 注册
- Wiley / Science / PNAS 的具体启动脚本与运维排障
- 架构分层和数据契约的完整背景

部署入口见 [`deployment.md`](deployment.md)，Wiley / Science / PNAS 运维细节见 [`flaresolverr.md`](flaresolverr.md)，架构说明见 [`architecture/target-architecture.md`](architecture/target-architecture.md)。

<a id="provider-canonical-sources"></a>
`references/api_notes.md` 和 `references/routing_rules.md` 只保留 API 约束或历史草图；provider/routing/waterfall 的 canonical 事实来源是本文档和 `paper_fetch.provider_catalog.PROVIDER_CATALOG`。

## Provider 能力矩阵

| Provider | 元数据 | 全文主路径 | 资产下载 | Markdown 能力 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `crossref` | 支持 | 不负责 publisher fulltext | 不支持 | 不适用 | 负责 resolve、routing signal、metadata merge 与 metadata-only fallback |
| `elsevier` | 官方 API | `官方 XML/API -> 官方 API PDF fallback` | XML 路线支持 `none` / `body` / `all`；PDF fallback 当前 text-only | 强 | XML 成功时公开为 `elsevier_xml`；PDF fallback 成功时公开为 `elsevier_pdf` |
| `springer` | 依赖 Crossref merge | `direct HTML -> direct HTTP PDF` | HTML 路线支持 `none` / `body` / `all`；PDF fallback 当前 text-only | 强 | `nature.com` 继续挂在 `springer` provider 下；HTML 成功公开 `springer_html`，PDF fallback 成功公开 `springer_pdf`；必要时可返回 provider `abstract_only` |
| `wiley` | 依赖 Crossref merge | `FlareSolverr HTML -> seeded-browser publisher PDF/ePDF -> Wiley TDM API PDF` | HTML 路线支持 `none` / `body` / `all`；PDF/ePDF fallback 当前 text-only | 中 | HTML 与 browser PDF/ePDF 依赖 repo-local FlareSolverr；`WILEY_TDM_CLIENT_TOKEN` 可在 browser PDF/ePDF fallback 失败或 browser runtime 不可用时继续尝试官方 TDM PDF lane；必要时可返回 provider `abstract_only` |
| `science` | 依赖 Crossref | `FlareSolverr HTML -> seeded-browser publisher PDF/ePDF` | HTML 路线支持 `none` / `body` / `all`；PDF/ePDF fallback 当前 text-only | 中 | 与 `wiley` 的 HTML / browser PDF/ePDF 路径共用浏览器工作流基座；AAAS access gate / entitlement 不满足时会停在 provider 内部并降级 `abstract_only` / `metadata_only` |
| `pnas` | 依赖 Crossref | `direct Playwright HTML preflight -> FlareSolverr HTML -> seeded-browser publisher PDF/ePDF` | HTML 路线支持 `none` / `body` / `all`；PDF/ePDF fallback 当前 text-only | 中 | direct Playwright preflight 成功时跳过 FlareSolverr；失败、challenge、正文不足或抽取失败时保持原 FlareSolverr/PDF 瀑布；较老文献常见 HTML 仅摘要，再继续走 provider 内部 PDF/ePDF fallback，必要时可返回 `abstract_only` |
| `ieee` | 依赖 Crossref merge + landing metadata | `landing/article number -> direct REST HTML -> clean-browser HTML -> direct HTTP PDF -> seeded-browser PDF -> abstract/metadata fallback` | HTML 路线支持 `none` / `body` / `all`；PDF fallback 当前 text-only | 中 | 现代 IEEE Xplore 文章优先公开为 `ieee_html`；REST 直连不可用时会用干净 Playwright context 捕获同一全文 HTML；无动态 HTML 的老文献可经真实 PDF payload 返回 `ieee_pdf`；不处理 CAPTCHA、登录自动化或权限绕过 |
| `arxiv` | arXiv ID + optional API enrichment | `ID 解析 -> arXiv official HTML -> direct HTTP PDF -> metadata fallback` | HTML 路线支持正文 figure 资产下载；PDF fallback 当前 text-only | 中 | HTML front matter 在主路径内合并；arXiv API enrichment 在 HTML/PDF 主链结束后才运行，失败只追加 warning、不影响已得到的 fulltext payload；HTML 成功公开为 `arxiv_html`，PDF fallback 公开为 `arxiv_pdf`；可识别的 ID 形态（含 `vN` 版本、`10.48550/arXiv.*` 等）见后文 arXiv 小节 |
| `copernicus` | 依赖 Crossref merge + landing metadata | `landing HTML / DOI-derived URL -> NLM/JATS XML -> direct HTTP PDF -> metadata fallback` | XML 路线支持 `none` / `body` / `all`；PDF fallback 当前 text-only | 强 | 开放获取 direct HTTP 路线，不需要登录态、FlareSolverr 或 Playwright；XML 成功公开为 `copernicus_xml`，PDF fallback 公开为 `copernicus_pdf` |

说明：

- 这张矩阵描述的是“当前代码里已经实现的 provider-owned waterfall”，不是“任意 DOI、任意运行环境都必然能拿到 publisher 全文”的承诺。
- 尤其 `wiley` / `science` / `pnas` 的浏览器与 PDF/ePDF 路径，仍受 publisher 访问权限、paywall/challenge 与远端站点行为影响。
- `mdpi` 仍是待接入设计，不在 provider catalog、router、registry、status surface 或测试矩阵中；计划语义只维护在本文档，不在 `references/` 中重复。
- Provider/source/domain/API/fallback marker、候选 URL 模板、HTML artifact 持久化、XML provider 推断与正文阈值的事实来源是 `paper_fetch.provider_catalog.ProviderSpec`。`SOURCE_PROVIDER_MAP` 登记实际 envelope / `ArticleModel.source` 值；例如 Springer HTML / PDF fallback 分别公开 `springer_html` / `springer_pdf`，二者都映射到 `springer` provider。
- `wiley` / `science` / `pnas` 只保留一套 provider-owned 浏览器栈，canonical runtime 是 `paper_fetch.providers.browser_workflow` 包入口。
- browser workflow 的 bootstrap、PDF/ePDF fallback、article assembly、asset retry helper、client 基类和 Playwright fetchers 已收敛到 `browser_workflow/` 子包；profile 只面向 provider catalog 中的 `science` / `pnas` / `wiley`。
- publisher 差异通过各 provider 模块 callback 下沉；旧 compatibility aliases、`_browser_workflow_*` 与 `browser_workflow_fetchers/` 兼容入口已移除，browser-PDF executor 继续共享 `_pdf_fallback`。
- browser-workflow 的 asset download Playwright fallback 在并发 worker 中使用线程私有 browser/context，不复用 `RuntimeContext` 持有的共享 browser；`RuntimeContext` 共享 browser 仍只保留给非 threaded 的主流程 Playwright 场景。
- 2020+ live / regression 基准样本集中维护在 [`../tests/provider_benchmark_samples.py`](../tests/provider_benchmark_samples.py)。
- 自然地理学 live-only 候选集中维护在 [`../tests/live/geography_samples.py`](../tests/live/geography_samples.py)，默认每家尝试前 `10` 条，并通过 [`../scripts/run_geography_live_report.py`](../scripts/run_geography_live_report.py) 产出 JSON/Markdown 报告。
- `geography` live runner 默认按 provider 轮转执行，保持单家样本顺序不变。
- `run_geography_live_report.py`、`export_geography_issue_artifacts.py`、`group_geography_issue_artifacts.py` 都属于 repo-local internal tooling：不新增 console script，不作为 MCP surface，对外产品面不变。
- geography live/report/export/group 仍受 `PAPER_FETCH_RUN_LIVE=1` 的 opt-in 边界保护；未启用 live 环境时，对应测试应稳定 skip。
- golden criteria live review 产物写入 `live-downloads/golden-criteria-review/`，由 [`../scripts/run_golden_criteria_live_review.py`](../scripts/run_golden_criteria_live_review.py) 生成；每条结果保留兼容的 `elapsed_seconds`，并新增 `stage_timings.fetch_seconds` / `materialize_seconds` / `total_seconds` / `resolve_seconds` / `metadata_seconds` / `fulltext_seconds` / `asset_seconds` / `formula_seconds` / `render_seconds`，同时在 `http_cache_stats` 中记录该 sample 相对执行前的 cache delta。`elsevier`、`springer`、`wiley`、`science`、`pnas`、`ieee` 和 `arxiv` 都纳入 supported provider 轮转，`provider-status.json` 会包含这些 provider 的本地诊断。`10.1016/S1575-1813(18)30261-4` 这类预期 metadata-only 样本，以及当前不支持的 TandF / Sage 样本，应通过 manifest 的 expected outcome 标记为 `skipped`，不进入 provider bug 修复队列。IEEE golden live 样本面向具备合法 IEEE Xplore 授权上下文的机器，预期为 `fulltext`；降级成 metadata-only、blocked fetch 或非 PDF payload 应作为 `live_fetch_blocked` 问题进入修复队列。

### Copernicus

`copernicus` 已接入当前 runtime，默认语义是 `fulltext_first`。Copernicus Publications 是开放获取出版社，正常情况下不需要登录态、机构授权、本地浏览器运行时或 FlareSolverr。

固定主路径：

```text
resolve DOI / landing URL
-> direct landing HTML, or DOI-derived XML/PDF candidates if landing is unavailable
-> discover citation_xml_url / article XML link
-> NLM/JATS XML -> Markdown
-> direct HTTP PDF text-only fallback
-> metadata-only fallback
```

实现细节：

- 路由信号来自 `ProviderSpec.domain_suffixes=("copernicus.org",)`、Crossref publisher alias `Copernicus Publications`，以及 DOI prefix `10.5194/`。
- 优先从 landing HTML 的 `citation_xml_url` 或正文下载链接发现 XML；如果 landing 抓取失败，会记录 warning 并继续尝试 DOI 形态拼出的 XML/PDF URL。PDF fallback 也优先使用 landing 暴露的 `citation_pdf_url` / `.pdf` 链接，最后再尝试 DOI 形态拼出的 `.pdf` URL，以覆盖早期 landing 缺少 PDF meta 的文章。
- XML 必须校验 NLM/JATS article root、`front/article-meta`、正文 `body/sec`、非空摘要，以及至少一个含 `<p>` 的正文 section 和足够正文字符数，不能只按 HTTP 200 判定成功；正文字符阈值来自 `ProviderSpec.body_text_thresholds`，Copernicus 只覆盖 `min_chars=500`。
- 早期 Copernicus XML 可能返回 `200 application/xml` 且有 `front/article-meta`，但 `body` 为空、没有 `sec`，实际只包含摘要级内容；这类 XML 必须失败并继续 PDF fallback，不经过 HTML 全文 fallback。
- XML 成功时公开 `source="copernicus_xml"`，source trail 为 `fulltext:copernicus_xml_ok`；PDF fallback 成功公开 `copernicus_pdf`。
- XML renderer 复用 `paper_fetch.providers._article_markdown_jats` 的通用 JATS 层覆盖标题、作者、摘要、正文 section、图表 caption、OASIS/HTML 表格、MathML display formula、references、data/code availability 和 supplementary links；Copernicus 模块只保留该路线的 provider 适配入口。
- Copernicus 没有 provider-owned HTML fallback，也不注册 HTML cleanup / availability hook；XML 不可用时直接进入 PDF fallback，再失败才进入 metadata-only fallback。
- `asset_profile=body` 默认保留正文 figure / table / formula 资产；`asset_profile=all` 额外允许明确 supplementary scope 的附件。PDF fallback 只返回 text-only Markdown，并通过 artifact warning 与 `download:copernicus_assets_skipped_text_only` 标记跳过资产。
- Golden corpus 覆盖 8 篇现代 XML 主路径样本，以及 4 篇早期 abstract-only XML 落到 PDF text-only fallback 的样本。
- `probe_status()` 只做本地能力说明，返回 direct XML/PDF fallback ready，不探测远端 Copernicus 站点。
- Copernicus 同时提供 OAI-PMH；它适合批量或补充发现，不作为单篇 DOI 的首个必需网络步骤。

### 待接入设计：MDPI

`mdpi` 还不是当前 runtime 已接入的 provider；本段记录预计技术栈。

后续接入时，MDPI 的默认语义应是 `fulltext_first`。MDPI 文章通常公开提供 HTML、PDF 和 XML 版本，但实际请求可能受 CDN 策略影响，因此实现要区分“公开内容的传输失败”和“无全文权限”。

建议主路径：

```text
resolve DOI / landing URL
-> direct landing HTML
-> discover article XML link or /xml route
-> MDPI XML -> Markdown
-> article HTML fallback
-> direct Playwright HTML fallback when public page is CDN-blocked for plain HTTP
-> PDF text-only fallback
-> abstract-only / metadata-only fallback
```

实现细节：

- 路由信号应来自 `mdpi.com` 域名、Crossref publisher alias `MDPI` / `MDPI AG`，以及 DOI prefix `10.3390/`。
- 优先使用 landing page 或 article notes 暴露的 `/xml` 链接；不要只依赖固定 URL 拼接，固定拼接只能作为发现失败后的候选。
- XML 成功时应走 provider-owned XML -> Markdown；HTML fallback 用 provider-specific cleanup 去掉页面导航、推荐文章、菜单、评论入口和引用弹层。
- MDPI PDF fallback 当前只应承诺 text-only；资产下载应以 XML/HTML 中的正文图片、表格图片和 supplementary links 为准。
- 如果 direct HTTP 返回 CDN 拦截或 `403`，可用 direct Playwright 读取公开页面作为 provider fallback；这不是 access-gate 绕过，也不应引入 FlareSolverr。
- `asset_profile=body|all` 应支持正文 figure / table / formula 图片和 supplementary 文件，资产失败不应覆盖已成功的正文 Markdown。

### IEEE

`ieee` 已接入当前 runtime，默认语义是 `fulltext_first`：

- 默认尝试获取全文，而不是默认停在摘要或元数据。
- 该默认行为假设操作者运行环境已经具备 IEEE Xplore 的合法访问权限，例如机构 IP、VPN、已登录浏览器态或个人订阅。
- 默认尝试不等于保证全文；如果授权、网络、站点状态或返回内容不满足全文条件，必须自动降级到 provider-managed `abstract_only` 或通用 `metadata_only` fallback。
- 不绕过 IEEE access gate，不处理验证码，不伪造授权状态；只能使用操作者已经具备的访问上下文。

固定主路径：

```text
resolve DOI / landing URL
-> extract IEEE article number
-> GET https://ieeexplore.ieee.org/rest/document/{article_number}/?logAccess=true
-> validate dynamic full-text HTML
-> if direct REST HTML is not usable, open the Xplore document page in a clean Playwright context and capture REST/DOM HTML
-> validate browser-captured full-text HTML
-> provider-owned IEEE HTML -> Markdown
-> direct HTTP PDF text-only fallback
-> seeded-browser PDF text-only fallback
-> abstract-only / metadata-only fallback
```

实现细节：

- 路由信号应来自 `ieeexplore.ieee.org` 域名、Crossref publisher alias `IEEE` / `Institute of Electrical and Electronics Engineers`，以及 DOI prefix `10.1109/`。
- article number 可从 IEEE landing URL、DOI 落地页中的页面元数据或 Crossref landing URL 推导；URL 解析只接受 `https://ieeexplore.ieee.org/document/{article_number}/` 这类 landing path，`/rest/document/...`、`stamp.jsp?arnumber=...` 等内部 route 不作为 landing URL contract。
- 动态全文端点返回的是 HTML fragment，常见 `content-type` 是 `text/html;charset=utf-8`，不能按 JSON API 处理。
- 请求头至少应保留 publisher 页面上下文，例如 `Accept: application/json, text/plain, */*`、对应 document URL 的 `Referer`、`x-security-request: required` 和浏览器 UA。
- 成功判定不能只看 HTTP `200`；需要校验返回体包含 `#article`、章节节点、足够正文段落或其他 IEEE full-text marker，并排除登录页、拦截页、摘要页、空壳和错误 HTML。
- 动态 HTML 成功时公开 `source="ieee_html"`；PDF fallback 成功时公开 `source="ieee_pdf"`。
- PDF fallback 先保留 direct HTTP 尝试；如果 IEEE `stamp.jsp` / `pdfPath` 返回 HTML/JS wrapper、502、redirect loop 或 access page，会再用 document landing page 作为 seed 进入 Playwright PDF fallback。
- seeded-browser PDF fallback 只复用操作者当前运行环境可合法取得的页面上下文和 cookies；不会处理 CAPTCHA、登录自动化或权限绕过。
- PDF fallback 只接受真实 PDF payload；如果 browser route 仍返回 access gate、challenge、APM/temporary unavailable 页面或非 PDF wrapper，会被拒绝并继续降级。失败诊断会记录 candidate URL、final URL、status、content-type、title/body 摘要；配置了 `download_dir` 时会在 `ieee_pdf_fallback/pdf.failure.html` 留下最后的非 PDF HTML 产物。
- 动态 HTML 的正文清洗会删除裸露 `SECTION I.` 这类 Xplore section marker；`div.section` / `div.section_2` 按嵌套层级输出 Markdown heading，主节为 `##`，`A.` / `B.` 子节为 `###`，`1)` 子节为 `####`。
- IEEE `tex-math` / `disp-formula` 会复用共享公式规则输出 LaTeX，不应退化成 `[Formula unavailable]`；如果仍然缺公式，`article.quality.semantic_losses.formula_missing_count` 会反映 Markdown 中的缺失占位数量。
- IEEE `ref-type="bibr"` 数字引用会进入共享 citation sentinel/normalize 链路，清理后不应遗留 `,,`、`(e.g., and)` 这类标点残留。
- 动态 HTML 中 IEEE `figure-full` / `figure-full table` 块里的 `/mediastore/IEEE/content/media/...` 正文图片和表格图片会先按 Xplore 域名绝对化，作为内联图片锚定在首次 caption 位置，并统一用 `https://ieeexplore.ieee.org/document/{article_number}/` 作为 seed 与 mediastore `Referer` 下载正文资产；full-size 候选失败或返回非图片时会刷新 seed/opener 后重试一次，再降级 preview。已内联图表通过 `render_state=inline` 避免在尾部 Figures / Tables 附录重复追加。`/assets/img/icon.support.gif` 这类 Xplore UI / 占位图标会在 HTML 清洗和资产列表中被过滤，不作为论文资产下载。
- IEEE 资产去重以 Xplore 页面结构为更强语义信号；当同一 mediastore URL 同时被识别为 table / figure 和通用 formula 图片时，保留 table / figure，并把下载结果回填到高优先级资产上。
- IEEE landing metadata 中的 Index Terms / Author Keywords / IEEE Keywords 会合并到 `metadata.keywords`；references 优先从 IEEE `/rest/document/{article_number}/references` 的可见 citation text 构建。该 route 成功返回非空 references 时会完全覆盖 Crossref / metadata fallback，不追加未匹配的 DOI-only 或 title-only 条目；只有该 route 不可用或返回空 references 时才保留 fallback references。
- 动态 HTML 中的正文图片、表格图片和公式节点按普通 `asset_profile=body|all` 语义接入；`asset_profile=all` 会额外下载明确 Supplementary / Supporting Material / Multimedia 附件区域中的文件，或 landing metadata 明确暴露 `sections.multimedia=true` 后从 `/rest/document/{article_number}/multimedia` payload 识别出的文件，且不局限于图片 content-type；普通正文里的 `data` / `dataset` / `code` / `media` 链接不会仅凭文本或后缀被归类为 supplementary。
- IEEE PDF fallback 仍然是 text-only；资产下载失败不应把已成功的正文 Markdown 判为失败。

## 路由规则

当前 provider 决策统一按更强信号优先：

```text
domain > publisher > DOI fallback
```

具体含义：

- `domain`
  - 由落地页 URL 或 Crossref metadata 的 `landing_page_url` 推导。
- `publisher`
  - 由 Crossref metadata 的 `publisher` 推导。
- `DOI fallback`
  - 在前两类信号都不够时，才使用 DOI 前缀兜底。

这些 provider 身份与能力配置统一来自 `paper_fetch.provider_catalog.PROVIDER_CATALOG`。Catalog 固定记录 provider 名称、展示名、official 标记、domain / DOI prefix / publisher alias、默认 asset 策略、probe 能力、abstract-only 策略、client factory 路径和 MCP status 顺序；`publisher_identity`、workflow routing、默认 asset profile、registry 与 provider status 列表都从这里派生。

### `provider_hint` 的含义

- `resolve_paper().provider_hint` 表示“当前最可信的 provider 提示”。
- 它来自 domain、publisher、DOI 信号综合判断。
- 它不是“保证最终一定由该 provider 成功返回”的承诺。

### `crossref` 作为 signal 与 source 的区别

`crossref` 有两种角色：

1. 作为 routing signal
   - 用于拿 `publisher`、`landing_page_url`、`license`、`fulltext_links` 等信号。
   - 此时不会自动把最终结果的 `source` 变成 `crossref_meta`。
2. 作为 public source
   - 当调用方显式收敛到 Crossref-only 且没有进入 metadata fallback 时，底层文章来源可保持 `crossref_meta`。
   - 当 fulltext waterfall 失败并进入 metadata fallback 时，`FetchEnvelope.source` 会公开表现为 `metadata_only`；底层 `ArticleModel.source` 仍可能是 `crossref_meta`。

实现边界上，Crossref HTTP lookup 的底层 owner 是 `paper_fetch.metadata.crossref.CrossrefLookupClient`；`paper_fetch.providers.crossref.CrossrefClient` 只是 provider adapter，并继续保留 public import path。

### `preferred_providers` 的语义

- 它限制最终允许进入的 provider fulltext 主链候选。
- 它不阻止系统内部调用 `crossref` 做路由判断或 metadata-only fallback。
- 如果显式设为 `["crossref"]`，行为会收敛成 Crossref-only。
- 当前可显式指定的 provider 名包括：
  - `elsevier`
  - `springer`
  - `wiley`
  - `science`
  - `pnas`
  - `ieee`
  - `arxiv`
  - `copernicus`
  - `crossref`

## 抓取瀑布与回退语义

统一主线如下：

```text
resolve
-> metadata / routing
-> provider fulltext
-> abstract-only / metadata-only fallback
```

### 1. resolve

- 输入可以是 DOI、URL 或标题。
- 标题查询会走 Crossref 候选打分。
- 如果标题候选不够确定，会返回 `ambiguous`，而不是直接抓取错误论文。
- DOI cleanup 保留原宽松规则，再用 `idutils` 做校验/规范化辅助；标题候选仍用 token Jaccard 权重、既有 confidence threshold 和 ambiguity margin，字符串 ratio component 由 `rapidfuzz.fuzz.ratio` 提供。

### 2. metadata 与路由

- 系统会先尽可能拿到 Crossref metadata。
- `elsevier` 和 `arxiv` 会参加 provider metadata probe；`arxiv` 通过项目内部 Atom API client 调用官方 arXiv API，获取 title、authors、abstract、published、categories、arXiv DOI、abs URL 和 PDF URL。
- `springer`、`wiley`、`science`、`pnas`、`ieee`、`copernicus` 在 `probe_official_provider()` 和 `has_fulltext()` 中都只依赖 Crossref / landing-page / DOI 信号，不再调用 publisher metadata API。
- 最终会合并 primary / secondary metadata，统一生成正文抓取需要的元数据。

### 3. provider 全文主路径

- `elsevier`
  - 固定顺序是 `官方 XML/API -> 官方 API PDF fallback -> metadata-only`。
  - XML/API 成功时公开 `source="elsevier_xml"`。
  - 官方 PDF fallback 成功时公开 `source="elsevier_pdf"`。
- `springer`
  - 固定顺序是 `direct HTML -> direct HTTP PDF -> abstract-only / metadata-only`。
  - 优先抓取 publisher landing HTML，不足正文时再走 direct HTTP PDF。
  - 优先使用 merged metadata 中的 `landing_page_url`，缺失时回退 DOI 解析。
  - HTML 成功时公开 `source="springer_html"`；PDF fallback 成功时公开 `source="springer_pdf"`。
- `wiley`
  - 使用 provider 自管 HTML + 官方 API PDF + publisher PDF/ePDF waterfall。
  - 固定顺序是 `FlareSolverr HTML -> seeded-browser publisher PDF/ePDF -> Wiley TDM API PDF -> abstract-only / metadata-only`。
  - 不做 direct Playwright HTML preflight，避免低成功率路径增加固定开销。
  - FlareSolverr HTML 正文首轮使用 `waitInSeconds=0` + `disableMedia=true` 的快速路径；challenge、访问拦截、摘要页或正文抽取不足时回退到原保守等待参数。
  - `WILEY_TDM_CLIENT_TOKEN` 是官方 TDM API PDF lane；缺失时仍可继续尝试 browser PDF/ePDF，配置后会在 browser PDF/ePDF fallback 失败或 browser runtime 不可用时继续尝试 TDM PDF。TDM URL template 声明在 `ProviderSpec.api_url_templates`，provider 只负责填充 DOI。
  - 成功时公开 `source="wiley_browser"`。
- `science`
  - 固定顺序是 `FlareSolverr HTML -> seeded-browser publisher PDF/ePDF -> abstract-only / metadata-only`。
  - 与 `wiley` 的 HTML / browser PDF/ePDF 路径共享同一套浏览器工作流基座。
  - 不做 direct Playwright HTML preflight，避免低成功率路径增加固定开销。
  - FlareSolverr HTML 正文首轮使用同一快速路径，并在 challenge、访问拦截、摘要页或正文抽取不足时保守重试。
  - 如果落到 AAAS 的 `Check access` / paywall 页面，应优先解读为 `institution not entitled / no access`，而不是 generic HTML fallback 缺失。
  - 成功时公开 `source="science"`。
- `pnas`
  - 固定顺序是 `direct Playwright HTML preflight -> FlareSolverr HTML -> seeded-browser publisher PDF/ePDF -> abstract-only / metadata-only`。
  - direct Playwright preflight 使用 `domcontentloaded` 并阻断 image/font/stylesheet/media；成功 payload 会标记 `html_fetcher="playwright_direct"`。
  - preflight 失败、遇到 challenge、正文不足或抽取失败时不改变旧语义，继续走 FlareSolverr HTML；FlareSolverr HTML 自身先尝试快速路径，再在失败或抽取不足时保守重试；成功 payload 标记 `html_fetcher="flaresolverr"`。
  - 较老文献常见 HTML 只到摘要页，此时 provider 会继续尝试 publisher PDF/ePDF fallback。
  - 成功时公开 `source="pnas"`。
- `ieee`
  - 固定顺序是 `landing metadata / article number -> direct REST HTML -> clean-browser HTML -> direct HTTP PDF fallback -> seeded-browser PDF fallback -> abstract-only / metadata-only`。
  - dynamic HTML 请求使用 document `Referer`、浏览器 UA、`x-security-request: required` 和兼容 `Accept`。
  - clean-browser HTML 使用干净 Playwright context 打开 document 页并捕获同一个 REST full-text 响应，失败时才继续 PDF fallback。
  - HTML 成功必须包含 `#article`、章节/段落结构，并通过正文充分性诊断；登录页、418/unable page、access gate、验证码、摘要页和空壳 HTML 都会被拒绝。
  - PDF fallback 只返回 text-only Markdown。
  - 成功时公开 `source="ieee_html"` 或 `source="ieee_pdf"`。
- `arxiv`
  - 固定顺序是 `arXiv ID 解析 -> arXiv official HTML -> direct HTTP PDF fallback -> metadata-only`。
  - resolve 支持 `https://arxiv.org/abs/{id}`、`/html/{id}`、`/pdf/{id}`、`arXiv:{id}`、裸 `{id}` / `{id}vN`，以及 `10.48550/arXiv.{id}`。
  - DOI、URL、裸 ID 或已有 metadata 中能可靠推导 arXiv ID 时，会先构造最小 metadata：`doi`、`arxiv_id`、`landing_page_url`、`html_url`、`pdf_url`、`provider=arxiv`，并立即执行 HTML -> PDF waterfall；arXiv API 只作为可选 metadata enrichment，失败或 429 只记录 warning/diagnostic，不会阻塞全文获取。
  - official HTML front matter 会补齐 `title`、`authors`、`abstract`、`published`、`primary_category`、canonical DOI、HTML/PDF URL；合并优先级是 arXiv API metadata > HTML front matter > derived arXiv URLs，因此 API 不可用时也不应出现 `Untitled Article` 或 authorless arXiv fulltext。
  - official HTML 是主路径，直接请求 `https://arxiv.org/html/{id}`，抽取 Markdown、官方 bibliography references 和正文 figure 资产候选；可匹配到下载 URL 的正文 figure 会在原 caption 附近先以内联图片 Markdown 表达，下载后改写为 `body_assets/...` 本地链接，尾部 `## Figures` 只作为未消费图片 fallback；HTML 正文不足、非 HTML、不可访问或质量门控失败时直接继续 PDF fallback。
  - official HTML 渲染前会做 arXiv/LaTeXML 专用语义块预处理：`figure.ltx_table` 和裸 `table.ltx_tabular` 复用共享 HTML table renderer 输出 Markdown 表格或 key-value 行，单个全宽 `colspan` 标题行会提升为表格前普通文本，`ltx_listing` / algorithm block 输出标题和 fenced pseudo-code，并用 placeholder 保持原文位置；无法插回的位置会追加到文末并记录 warning。
  - official HTML 的 section kind 由清洗后的 `article.ltx_document` DOM 结构 hint 驱动：`References` / `Bibliography` 与 Data / Code Availability 继续按共享语义分类，其它由正文渲染链路输出的 article 标题默认作为正文；页面外部 metrics / citation chrome 不进入 arXiv HTML 解析范围。
  - official HTML 会清理仅表示未定义宏的 `.ltx_ERROR.undefined` 节点（例如 `\addsec`）、图片 `alt="Refer to caption"` 占位噪声和 TeX annotation 内部嵌套 `$...$` 定界符；普通段落、list item 和 caption 的源 HTML 硬换行会折叠为空格，但 display math、Markdown 表格、列表边界、代码块和独立图片块仍保留必要换行。正常 caption、图片 URL 和正文 figure 下载链路不受影响。语义块渲染失败会写入 `semantic_losses.table_semantic_loss_count` / `table_fallback_count`，便于质量诊断。
  - PDF fallback 只返回 text-only Markdown，并通过 `download:arxiv_assets_skipped_text_only` 标记跳过资产。
  - 成功时公开 `source="arxiv_html"` 或 `source="arxiv_pdf"`；HTML route 使用项目自研 HTML Markdown 渲染链路和全文质量检测，不依赖本机转换器。
- `copernicus`
  - 固定顺序是 `landing HTML -> citation_xml_url / XML link -> NLM/JATS XML -> direct HTTP PDF fallback -> metadata-only`。
  - landing HTML 和 XML/PDF 下载都走 direct HTTP，不需要 FlareSolverr、Playwright 或登录态。
  - XML 成功必须通过 JATS 结构、摘要和正文充分性校验；失败后才进入 PDF fallback。早期 abstract-only XML 不会被标记成成功全文，会继续尝试 PDF。
  - PDF 候选优先来自 landing meta/link，最后使用 DOI 形态推导的 `.pdf` URL；如果 PDF payload 不是可抽取文本的真实全文，继续降级 metadata-only。
  - PDF fallback 只返回 text-only Markdown。
  - 成功时公开 `source="copernicus_xml"` 或 `source="copernicus_pdf"`。

### 4. abstract-only / metadata-only fallback

如果命中了 `elsevier`、`springer`、`wiley`、`science`、`pnas`、`ieee`、`arxiv`、`copernicus` 之一：

- 系统只会走该 provider 自己管理的 fulltext waterfall
- provider 主链不可用或返回 `None` 后直接进入 metadata-only fallback
- `springer` / `wiley` / `science` / `pnas` / `ieee` 如果只能确认摘要级内容，会返回 provider 自己的 `abstract_only` 结果，而不是再绕去通用 HTML；`arxiv`、`copernicus` 与 `elsevier` 保持一致，HTML/XML/PDF 都不可用时进入通用 metadata-only fallback

如果没有命中这些 official provider：

- 系统仍会继续做 DOI / Crossref metadata 解析
- 不再尝试任何通用 HTML 正文提取
- `strategy.allow_metadata_only_fallback=true` 时返回 metadata + abstract
- 否则直接抛错

如果 provider 主链已经拿到 fulltext HTML：

- provider fetch result 组装层会在构造 `ArticleModel` 前自动触发 HTML -> Markdown
- `springer`、`wiley`、`science`、`pnas`、`ieee`、`arxiv` 会优先复用各自 provider 专用的 HTML 解析器；`copernicus` 只在 XML 主路径使用专用 XML 解析器
- 通用 HTML 转换只作为“已确认 fulltext HTML 但 provider 没有提供 Markdown”的兜底，不会变成任意 URL 的全文 fallback

如果没有可返回的 provider `abstract_only` 结果，而 `strategy.allow_metadata_only_fallback=true`：

- 返回 metadata + abstract
- `has_fulltext=false`
- `warnings` 中显式说明已降级
- `source_trail` 中会带 `fallback:metadata_only`
- public `source` 通常会表现为 `metadata_only`；如果元数据里有摘要，模型质量层的 `content_kind` 可能归类为 `abstract_only`

如果关闭这个开关，正文不可得会直接抛错。

## Elsevier / Springer / Wiley / Science / PNAS / IEEE / arXiv / Copernicus 的特殊语义

这些 provider 的共同点是：

- metadata 先尽量来自 Crossref；`elsevier` 可能用 publisher metadata probe 作为 primary 覆盖 / 补充，`arxiv` 先用 ID 构造可抓取 HTML 的最小 metadata，HTML 成功后再按 arXiv API metadata > HTML front matter > derived URLs 合并
- fulltext 主路径由 provider 自己控制
- 主链不可用时不走通用 HTML；不可用 / `None` 结果进入 metadata-only fallback，provider-managed `abstract_only` 结果可直接返回
- XML / HTML / PDF / TDM / browser PDF fallback 的顺序由内部 `paper_fetch.providers._waterfall` runner 编排；各 provider step 仍保留自己的 payload 结构、warning 文案和 `fulltext:*` source trail marker
- `ProviderClient.fetch_result` 负责通用 raw payload、本地副本标记、资产下载、warning/trace 和 artifact 组装；workflow 内部调用时必须传入 `artifact_store=` 与 `context=`，Browser workflow 与 Springer 只通过 hook 处理 abstract-only 后 PDF recovery 或 provider-managed abstract-only 返回

但它们的 fulltext 形态不同：

- `elsevier`
  - provider 自管 `官方 XML/API -> 官方 API PDF fallback`
  - XML article document builder 通过 provider dispatch table 进入 Elsevier renderer；未知 provider 不会落入半成品分支
  - XML attachment MIME 优先使用 publisher 响应/节点声明；缺失时用 Python `mimetypes.guess_type` 按文件扩展推断
  - XML/PDF 官方 representation 的 `404/406/415` 统一经 `providers.base.map_request_failure` 映射为 `no_result`
  - 进入 PDF lane 时会组合 `fulltext:elsevier_xml_fail`、`fulltext:elsevier_pdf_api_ok`、`fulltext:elsevier_pdf_fallback_ok`
  - PDF lane 失败时会带 `fulltext:elsevier_pdf_api_fail`
- `springer`
  - provider 自管 `direct HTML -> direct HTTP PDF`
  - Springer/Nature chrome 清理以结构信号为主：AI alt disclaimer 只按 `ai-alt-disclaimer` ID/ARIA 关系删除，license 段落以 `creativecommons.org/licenses/*` 链接为主、短文本阈值为辅助
  - Nature heading cosmetic normalization 注册在 provider rule profile；例如 `Online Methods` 规范为 `Methods`
  - 成功轨迹是 `fulltext:springer_html_*`，PDF fallback 成功时会带 `fulltext:springer_pdf_fallback_ok`
- `wiley`
  - provider 自管 FlareSolverr HTML + Wiley TDM API PDF + seeded-browser publisher PDF/ePDF waterfall
  - 成功轨迹是 `fulltext:wiley_html_*` / `fulltext:wiley_pdf_api_ok` / `fulltext:wiley_pdf_browser_ok` / `fulltext:wiley_pdf_fallback_ok`
  - 失败时若 API lane 未产出 PDF，会保留 `fulltext:wiley_pdf_api_fail`；若 browser PDF/ePDF lane 已实际尝试但失败，会再带 `fulltext:wiley_pdf_browser_fail`
- `science`
  - provider 自管 `FlareSolverr HTML + seeded-browser publisher PDF/ePDF`
  - `fulltext:science_html_fail` / `fulltext:science_pdf_fallback_ok` 只描述 provider 主链的阶段切换；如果页面本身就是 access gate，更准确的业务解释应是 `institution not entitled / no access`
  - 继续保持现有 `science` 风格的公开来源与轨迹命名
- `pnas`
  - provider 自管 `direct Playwright HTML preflight + FlareSolverr HTML + seeded-browser publisher PDF/ePDF`
  - 较老文献可能先表现为 `fulltext:pnas_html_fail`，再进入 `fulltext:pnas_pdf_fallback_ok`
  - 继续保持现有 `pnas` 风格的公开来源与轨迹命名
- `ieee`
  - provider 自管 `landing metadata / article number -> direct REST HTML -> clean-browser HTML -> direct HTTP PDF fallback -> seeded-browser PDF fallback -> abstract/metadata fallback`
  - article number URL parser 只承诺 IEEE Xplore `/document/{article_number}/` landing URL；REST、stamp 和 query-string 形态由 metadata 或 route builder 处理
  - 支持图标过滤优先使用 DOM/资产结构、尺寸和 alt/title 语义，历史 `/assets/img/icon.support.gif` 路径只保留为兜底
  - 裸 `SECTION I` / `Section 1.` 等 Xplore marker 变体会在 leaf/kicker 节点中清除，不作为正文标题输出
  - 现代文章成功轨迹是 `fulltext:ieee_html_ok`
  - REST HTML 被 401/403 或 challenge 拒绝时，会先用干净 Playwright context 打开 Xplore document 页并捕获同一个 REST full-text 响应；不会读取本机浏览器 profile、复用用户登录态、自动登录、处理验证码或绕过权限
  - 老文献、无动态 HTML 或 clean-browser HTML 仍不可用时，可能先表现为 `fulltext:ieee_html_fail` / `fulltext:ieee_browser_html_fail`，再进入 `fulltext:ieee_pdf_fallback_ok`
  - PDF fallback 公开为 `ieee_pdf`，HTML 公开为 `ieee_html`
- `arxiv`
  - provider 自管 `arXiv ID 解析 -> arXiv official HTML -> direct HTTP PDF fallback -> metadata fallback`
  - optional arXiv API / HTML metadata merge 只做 enrichment，详见 [arXiv](#arxiv)
  - HTML 成功轨迹是 `fulltext:arxiv_html_ok`
  - HTML 不可用、非 HTML、正文不足或质量门控失败时先保留 `fulltext:arxiv_html_fail`，再尝试 `fulltext:arxiv_pdf_fallback_ok`
  - PDF fallback 公开为 `arxiv_pdf`，HTML 公开为 `arxiv_html`
- `copernicus`
  - provider 自管 `landing HTML -> NLM/JATS XML -> direct HTTP PDF -> metadata fallback`
  - XML 成功轨迹是 `fulltext:copernicus_xml_ok`
  - XML 不可用时先保留 `fulltext:copernicus_xml_fail`，再尝试 `fulltext:copernicus_pdf_fallback_ok`
  - PDF fallback 公开为 `copernicus_pdf`，XML 主路径公开为 `copernicus_xml`

因此：

- 不再存在 public HTML fallback 开关
- 对 `elsevier` 来说，系统始终按内部 `官方 XML/API -> 官方 API PDF fallback` waterfall 执行
- 对 `springer` 来说，系统始终按内部 `direct HTML -> direct HTTP PDF` waterfall 执行
- 对 `wiley` / `science` / `pnas` 来说，系统始终按上文声明的 provider-owned browser workflow 执行。
- `pnas` preflight 只做快速成功路径，不改变 FlareSolverr/PDF 回退语义。
- 对 `ieee` 来说，系统始终按内部 `landing metadata / article number -> direct REST HTML -> clean-browser HTML -> direct HTTP PDF fallback -> seeded-browser PDF fallback -> abstract/metadata fallback` waterfall 执行
- 对 `arxiv` 来说，系统始终按内部 `arXiv ID 解析 -> arXiv official HTML -> direct HTTP PDF fallback -> metadata fallback` waterfall 执行；metadata enrichment 只在主链外补充字段
- 对 `copernicus` 来说，系统始终按内部 `landing HTML -> NLM/JATS XML -> direct HTTP PDF fallback -> metadata fallback` waterfall 执行

## 默认输出策略

CLI、Python API、MCP 当前统一采用这些默认值：

- `asset_profile=null (provider default)`
- `max_tokens="full_text"`
- `include_refs=null`
- MCP `modes=["article", "markdown"]`
- MCP `prefer_cache=false`
- MCP `no_download=false`
- MCP `save_markdown=false`

### `asset_profile`

- `null` / omitted
  - 使用 provider default
  - `springer` / `wiley` / `science` / `pnas` / `ieee` / `copernicus` 默认等价于 `body`
  - 其他默认等价于 `none`
- `none`
  - 不下载资产
  - Markdown 保留 figure caption
  - 不输出 supplementary 链接
- `body`
  - 只从 provider-cleaned 正文 fragment 下载正文 figure
  - 下载正文表格原图
  - 下载可识别的正文公式图片 fallback
  - 不包含 supplementary
- `all`
  - 下载当前 provider 已识别的全部相关资产
  - 在 `body` 基础上额外下载 supplementary 文件附件
  - 包含 appendix / supplementary 等非正文资产；正文已经内联消费的图表仍会通过 `render_state` 从尾部重复附录中过滤

#### PDF fallback 的 text-only 边界

- PDF fallback 当前不下载资产。
- 适用 provider：`elsevier`、`springer`、`ieee`、`arxiv`、`copernicus`、`wiley`、`science`、`pnas`。
- 即使 `asset_profile=body|all`，这些 PDF / ePDF fallback 也只返回 text-only Markdown。
- 共享 PDF Markdown 转换会拒绝明显过短或主要由 IEEE 授权页脚组成的结果。
- PDF 内有大量透明文本层时，会用 PyMuPDF legacy transparent-text 路径二次转换。
- 二次转换仍不足时，继续走候选重试或 provider 降级。

#### Provider HTML 资产语义（wiley / science / pnas / arxiv / ieee / copernicus / springer / elsevier）

- `wiley` / `science` / `pnas` 的 FlareSolverr HTML 成功路径支持正文图、表和公式图片资产。
- 这些 provider 以 shared Playwright browser context 为主链路，不再先走普通 HTTP 直连。
- 图片候选优先 full-size/original；全部失败后才尝试 preview。
- preview 也通过同一个 seeded browser context 下载。
- `arxiv` HTML 成功路径会从 official HTML 正文抽取 figure 资产候选。
- `arxiv` 正文图片先插在原 figure caption 附近，下载后改写到 `body_assets/...`。
- 已原位消费的 `arxiv` body figure 不会再进入尾部 `Figures`。
- `arxiv` 图片下载用 direct `HttpTransport` 和图片友好的 `Accept` header。
- `arxiv` 不使用 official HTML URL 触发 cookie-seeded opener。
- `arxiv` 正文图片并发上限是 `min(PAPER_FETCH_ASSET_DOWNLOAD_CONCURRENCY, 2)`。
- `arxiv` 对网络异常类失败顺序重试一次，不重试 404 或非图片 payload。
- `download_tier=preview` 只有满足最小宽高才视为可接受 preview。
- 宽扁但面积足够的真实论文图可标记为 `preview_accepted`。
- `preview_accepted` 只保留 source trail / asset diagnostics，不写普通 warning。
- 小图标和占位图仍会作为 preview fallback 失败或降级信号。
- IEEE dynamic HTML 成功路径从 cleaned `#article` fragment 抽取正文图、表和公式资产。
- IEEE `asset_profile=all` 会额外下载明确附件区域或 landing multimedia payload。
- Copernicus XML 成功路径会从 JATS/XML 抽取正文图、表、公式和明确 supplementary links。
- Springer HTML 成功路径只从 cleaned body/content scope 抽取正文图片。
- Elsevier XML 的 `body` 只下载 `image` / `table_asset`。
- Elsevier XML 的 `all` 额外下载 `supplementary` references。
- Elsevier supplementary 统一映射到 `kind="supplementary"`、`section="supplementary"` 和 `download_tier="supplementary_file"`。
- Elsevier 正文资产遇到 timeout、TLS、DNS、connection reset/closed 等网络失败时，只对失败项串行重试一轮。
- 明确 HTTP status、权限/认证类或非 HTTP scheme 失败不自动重试。

#### Supplementary 范围与命名

- `wiley` / `science` / `pnas` 的 `asset_profile=all` 会把 supplementary 作为独立文件附件下载。
- 这条链路不因 supplementary 失败重新下载已成功的正文 figure。
- `wiley` supplementary 只从 `Supporting Information` 区块抽取。
- `wiley` 只接受 `/action/downloadSupplement`、结构化 supplementary link 属性或 `sup-*` supporting file 链接。
- 正文 `<figure>` 里的 `/cms/asset/...fig-*.jpg|png|webp` 只保留为 figure 资产。
- `downloadSupplement` query 中的 `file`、`filename`、`attachment`、`download` 优先作为真实文件名。
- 布尔型 `download=true` 不作为文件名。
- `science` / `pnas` supplementary 只从真实 supplementary / supporting section 子树抽取。
- `science` / `pnas` 只保留 publisher `/doi/suppl/.../suppl_file/...` 附件。
- Data Availability 普通数据链接、页内导航和 section 内引用文献 PDF 不归 supplementary。
- Springer supplementary 只允许来自明确 supplementary、supporting 或 extended data section 子树。
- Springer `Source Data` 独立落到 `source_data/` 子目录。
- Springer `Peer Review File` / `Peer reviewer reports` 不归 supplementary。

#### 资产去重与诊断前置约束

- 通用 HTML figure 与 supplementary 下载使用 `paper_fetch.extraction.html.assets.state` 状态机。
- cookie-aware opener/request 统一在 `paper_fetch.extraction.html.assets.requester` 中处理。
- 网络、opener 或浏览器 document fallback resolve 阶段可并发执行。
- 文件写入、文件名去重、`source_data/` 分流和失败诊断收集仍串行执行。
- 输出顺序、fallback 候选顺序、`article.assets[*]` 与 `quality.asset_failures` shape 保持稳定。
- Elsevier XML object references 也使用“网络并发、写入串行”约束。
- 并发 worker 上限由 `PAPER_FETCH_ASSET_DOWNLOAD_CONCURRENCY` 控制，默认 `4`，最小 `1`。
- 普通 HTTP 单资产下载仍可在调用线程解析。
- Provider fulltext 公开契约是 `fetch_result()` / `fetch_raw_fulltext()`。
- 旧 `fetch_fulltext()` dict 入口已经删除。
- 同一次 provider fetch 内会复用 `RuntimeContext.parse_cache`。
- `parse_cache` 避免 Elsevier XML、Springer HTML、browser-workflow Markdown 和 HTML asset 重复解析。
- IEEE dynamic HTML block-page token 判定也按 payload 缓存。
- 同一个 `RuntimeContext` 生命周期内还会复用 `session_cache`。
- `has_fulltext` 与 `fetch_paper` 可共享 query resolution、Crossref DOI metadata、Elsevier metadata probe 和 landing page probe。
- fetch 阶段命中 landing probe 时，会把 citation PDF URL 合并到 metadata `fulltext_links`。
- `PlaywrightContextManager` 会在同一 `RuntimeContext` 内 lazy 复用 Chromium browser。
- PNAS preflight、正文图片/文件 fetcher 与 PDF/ePDF fallback 仍按阶段创建独立 browser context/page。
- `RawFulltextPayload.metadata` 只是 legacy/read-only compatibility view。
- provider 新逻辑应读写 `ProviderContent.route_kind`、`markdown_text`、`diagnostics`、`fetcher`、`browser_context_seed`、`warnings`、`trace` 和 `merged_metadata`。

### 资产去重与诊断

- `render_state="inline"` 的资产表示正文已经渲染过，不会进入文末 `Figures` / `Tables`。
- `render_state="appendix"` 的资产仍可进入尾部兜底块；当同类资产全是 appendix 状态时，标题会显示为 `Additional Figures` / `Additional Tables`。
- 正文 Markdown 图片链接和资产路径会按 URL、路径、相对 `body_assets/...` 后缀和 basename 做等价比较。
- 保存 Markdown 时也会按 `full_size_url`、`preview_url`、`download_url`、`original_url`、`source_url` 和最终 `path` 改写远端图片链接。
- 这可以避免正文图在尾部重复，或导出残留可本地化远端图。
- 文章组装阶段也会用 `article.assets[*]` 把正文里的远程 figure / table / formula image 链接改写为已下载本地路径，再做 Markdown 图片块边界归一化，避免图片和标题、正文句子或公式块粘连。
- 下载资产会保留 `download_tier`、`download_url`、`original_url`、`preview_url`、`full_size_url`、`content_type`、`downloaded_bytes`、`width`、`height`。
- 下载失败的资产会保留到 `article.quality.asset_failures` 与顶层 `quality.asset_failures`。
- 失败诊断包含 `status`、`content_type`、`title_snippet`、`body_snippet`、`reason` 和 asset-level recovery 轨迹。
- 图片 payload MIME 识别由 `filetype` 负责，JPEG/PNG/GIF/WebP 尺寸读取由 `imagesize` 负责；无法识别时仍按 unknown/空宽高处理，不引入 Pillow。
- `wiley` / `science` / `pnas` 正文图片主链路只输出 `download_tier="full_size"` 或 `download_tier="preview"`。
- supplementary 文件链路输出 `download_tier="supplementary_file"`。
- 旧的 `playwright_canvas_fallback` tier 只可能来自仍保留 HTTP-first 语义的旧通用图片下载路径。
- `wiley` / `science` / `pnas` 正文图片下载会缓存重复的 figure page / 图片候选 URL。
- 这条链路按 `PAPER_FETCH_ASSET_DOWNLOAD_CONCURRENCY` 控制 worker 上限，默认 `4`。
- 使用 Playwright image document fetcher 时，单个正文图片也会在 worker 线程执行 resolver。
- 这样可以避免主线程已有 Playwright sync context 时再次启动独立 sync Playwright。
- 最终输出顺序仍与输入资产顺序一致。
- supplementary 文件下载失败时，`article.quality.asset_failures` 会保留失败诊断。
- 诊断字段包括 `status`、`content_type`、`title_snippet`、`body_snippet`、`reason` 和 recovery 轨迹。
- 浏览器工作流的重试按 `heading`、`caption` 和 URL 字段匹配失败诊断。
- 重试只重跑失败的 body 或 supplementary 资产。
- `download_tier="preview"` 只有在宽高满足当前阈值 `300x200` 时才会标记为可接受 preview；否则仍会进入 preview fallback / asset issue 诊断。
- Live review 规则：公式图片是公式语义的 fallback，因此 formula-only preview fallback 不自动归类为 `asset_download_failure`；figure/table preview fallback 仍按资产问题处理，除非已有 accepted 诊断。
- Live review 规则：相关资产下载 warning 会归类为 `asset_download_failure`。
- 这些 warning 包括 `related assets could not be downloaded`、`assets were only partially downloaded` 和 `partially downloaded`。
- `asset_failures` trail 或 `quality.asset_failures` 也会归类为 `asset_download_failure`。
- Live review 规则：golden criteria live review 产物 `extracted.md` 属于内部检查输出。
- 生成脚本见 [`../scripts/run_golden_criteria_live_review.py`](../scripts/run_golden_criteria_live_review.py)。
- 若该文件仍残留 IEEE mediastore 图片链接，且对应资产已经本地下载，会归类为 `asset_download_failure`。
- 即使 preview 被 accepted，上述残留远端链接仍按资产下载失败处理。

### `include_refs`

- `max_tokens="full_text"` 时，默认等价于 `all`
- `max_tokens=<整数>` 时，默认等价于 `top10`

<a id="mcp-download-and-markdown-save"></a>
### 下载行为

- 对 provider artifact 来说，`--no-download` 或 `download_dir=None` 优先级最高
- CLI/MCP 通过 `workflow.request_builder.build_fetch_pipeline_request()` 统一装配 `FetchPipelineRequest`。
- `FetchPipeline` 负责创建 `RuntimeContext`。
- Provider payload、Springer HTML local copy、Markdown 保存和 asset 诊断仍由 `ArtifactStore` 应用。
- CLI 的 `--output-dir` 仍是 provider HTML/PDF/图片等 artifact 目录；额外地，当用户显式传入 `--format`、保留 `--output -` 且指定 `--output-dir` 时，CLI 会把同格式主输出副本写入该目录，文件名为 `<doi>.md`、`<doi>.json` 或 `<doi>.both.json`。
- 既有 warning 与 `download:*` source trail marker 保持不变。
- MCP fetch-envelope sidecar/cache-index 的 JSON 写入也复用 `ArtifactStore` 的原子 writer。
- 即使 `asset_profile` 是 `body` / `all`，也不会落盘
- 没有本地文件时，Markdown 会自动退回 captions-only 或不展示本地资源链接
- MCP `no_download=true` 会让 service/provider 阶段使用 `RuntimeContext(download_dir=None)`，因此不会写 provider payload、PDF、HTML、资产或 fetch-envelope sidecar；`prefer_cache=true` 仍可显式读取已存在的 fetch-envelope sidecar。
- MCP `save_markdown=true` 是独立的 Markdown 保存步骤：成功时写 `.md` 并返回 `saved_markdown_path`，追加 `download:markdown_saved`；没有 fulltext Markdown 时不写文件，追加 `download:markdown_skipped_no_fulltext`。
- MCP `save_markdown=true` 的工具响应默认是紧凑结果：`markdown=null`、`article=null`，不把全文正文或 article sections 放入当前上下文；响应仍保留 `saved_markdown_path`、`metadata`、`quality`、`warnings`、`source_trail`、`trace` 和 `token_estimate_breakdown` 等诊断字段。
- MCP `save_markdown=true` 时，即使 `strategy.asset_profile=body|all`，工具结果也不会额外附带 inline `ImageContent`；图片资源仍可按资产策略下载到本地，并由保存的 Markdown 引用。
- `no_download=true` 与 `save_markdown=true` 同时使用时，只允许 Markdown 保存步骤落盘；provider payload、资产和 fetch-envelope sidecar 仍保持关闭。

<a id="provider-原始-html-artifact"></a>
### Provider 原始 HTML artifact

- 当声明了 `ProviderSpec.persist_provider_html=True` 的 provider 抓取链拿到 publisher article HTML 时，`ArtifactStore` 会把可信的原始正文 HTML 单独落盘；当前由 Springer 和 arXiv 声明。
- 如果 `download_dir` 本身就是 DOI slug 文章目录，文件名是 `original.html`；否则文件名是 `<doi_slug>_original.html`。
- `*_assets/` 目录仍可以包含 figure page、table page、redirect page 或辅助 HTML；这些文件不能被当成可信的正文原文源文件。
- 该行为由 [`../tests/unit/test_springer_html_regressions.py`](../tests/unit/test_springer_html_regressions.py) 中的 `test_springer_html_route_saves_original_html_in_article_dir` 锁定。

<a id="public-output-fields"></a>
## 公开输出里最重要的字段

这些字段最适合拿来判断结果质量和来源：

- `source`
  - 粗粒度公开来源，如 `elsevier_xml`、`elsevier_pdf`、`springer_html`、`springer_pdf`、`wiley_browser`、`science`、`pnas`、`ieee_html`、`ieee_pdf`、`arxiv_html`、`arxiv_pdf`、`copernicus_xml`、`copernicus_pdf`、`crossref_meta`、`metadata_only`
- `has_fulltext`
  - 最终抓取瀑布后的 verdict
- `warnings`
  - 降级、截断、资产部分失败等信息
- `source_trail`
  - 更细粒度的路由、probe、fallback、下载轨迹
- `token_estimate_breakdown`
  - `abstract`、`body`、`refs` 的 token 估算
- `article.assets[*]`
  - 对下载资产保留 `render_state`、`anchor_key`、`download_tier`、`download_url`、`original_url`、`content_type`、`downloaded_bytes`、`width`、`height` 等诊断字段
- `article.quality.semantic_losses`
  - 表格现在区分 `table_layout_degraded_count` 和 `table_semantic_loss_count`；前者表示 Markdown 版式降级，后者才表示语义内容丢失
- `article.quality.asset_failures`
  - 对失败资产保留 `status`、`content_type`、`title_snippet`、`body_snippet`、`reason` 与 `recovery_attempts`

### Markdown 与语义 normalize

- 公式输出会在公共公式 normalize 层处理 publisher-specific LaTeX 宏。
- `\updelta` 等 upright Greek 宏会改写成普通 KaTeX 可渲染宏；`\mspace{Nmu}` 会改写成 `\mkernNmu`，其它单位不改。
- HTML 公式如果能从 MathML 转成 LaTeX，会按行内或 display 语境渲染；如果只有站点提供的公式图片 fallback，会保留为 `![Formula](...)` 并进入资产下载/改写流程。
- HTML references 会去除 publisher 链接 chrome，如 `Google Scholar`、`Crossref`、相关链接和隐藏文本，并优先保留用户可见 citation body。
- 默认 reference 组装规则是：fulltext provider 已经从 HTML / XML / 出版社 REST 显式提供非空 references 时，最终 `ArticleModel.references` 和 Markdown references 以这些全文/出版社 references 为准；metadata / Crossref references 只在 provider references 为空、失败或不可用时兜底，不允许追加未匹配的 metadata-only 条目。
- Elsevier XML references 优先从结构化 bibliography 构建，保留编号、作者、题名、来源、页码、年份和 DOI；缺字段时保留原始 citation text 或显式 `[Reference text unavailable]` 占位，Crossref references 只作为兜底。

## 配置文件与环境变量入口

默认主配置文件：

```text
~/.config/paper-fetch/.env
```

该默认位置由 `platformdirs` 解析；上面是常见 Linux/XDG 布局。仓库内 `.env` 不会自动加载。

如果你在开发场景里要使用仓库外的某个配置文件，显式设置：

```bash
PAPER_FETCH_ENV_FILE=/path/to/.env
```

### 通用环境变量

#### `PAPER_FETCH_SKILL_USER_AGENT`

- 自定义请求用 `User-Agent`。
- 建议配置为稳定项目标识。

#### `CROSSREF_MAILTO`

- Crossref polite pool 建议携带的联系邮箱。
- 会被拼入 Crossref 请求参数。

#### `PAPER_FETCH_DOWNLOAD_DIR`

- 覆盖默认下载目录。
- CLI 与 MCP 都会优先使用它。

#### `XDG_DATA_HOME`

- 在未配置 `PAPER_FETCH_DOWNLOAD_DIR` 时，用来推导用户数据目录。
- CLI / MCP 的用户数据下载目录会落在 `<XDG_DATA_HOME>/paper-fetch/downloads`。
- 未设置时使用 `platformdirs` 提供的平台默认用户数据目录。
- CLI 只有在用户数据下载目录创建失败时才回退仓库相对的 `live-downloads`。

### 公式后端

#### `PAPER_FETCH_FORMULA_TOOLS_DIR`

- 可选。
- 覆盖运行时查找外部公式工具的目录。
- 未配置时，运行时会依次考虑 repo-local `.formula-tools` 和用户数据目录下的 `formula-tools`。

#### `MATHML_CONVERTER_BACKEND`

- 可选。
- 支持 `texmath`、`mathml-to-latex`、`mml2tex`、`auto`。
- `legacy` 是代码仍能识别的历史值，但当前会直接报不可用，不应在新配置中使用；未来版本可能彻底移除。
- 默认是 `texmath`；未显式指定时，如果 `texmath` 失败，会尝试 `mathml-to-latex` fallback。
- 显式指定某个 backend 时，失败会按该 backend 返回，不会自动隐藏错误。
- 内部后端清单由 registry 声明，`auto` 与 benchmark 顺序仍保持 `texmath` → `mathml-to-latex` → `mml2tex` 的既有约定。

#### `TEXMATH_BIN`

- 可选。
- 指定 `texmath` 可执行文件；未配置时先查找公式工具目录，再查找 `PATH`。

#### `MATHML_TO_LATEX_NODE_BIN`

- 可选。
- 指定 Node 可执行文件；默认是 `node`。

#### `MATHML_TO_LATEX_SCRIPT`

- 可选。
- 指定 `mathml-to-latex` wrapper 脚本；未配置时会查找公式工具目录、打包资源和仓库脚本。

#### `MATHML_TO_LATEX_WORKER`

- 可选。
- 默认启用；设为 `0` / `false` / `no` / `off` 时禁用常驻 Node worker，回到每次调用 wrapper CLI。
- worker 使用 JSONL stdin/stdout 协议，失败或超时时会回退到单次 CLI。

#### `MATHML_TO_LATEX_WORKER_SCRIPT`

- 可选。
- 指定 `mathml-to-latex` worker 脚本；未配置时会查找公式工具目录、打包资源和仓库 `scripts/mathml_to_latex_worker.mjs`。

#### `MATHML_CONVERSION_CACHE_SIZE`

- 可选。
- 公式转换 LRU 大小；默认 `1024`，设为 `0` 可禁用结果缓存。
- 缓存 key 包含 backend、原始 MathML、display mode 和关键 converter 配置。

#### `MML2TEX_*`

- 高级可选。
- 代码支持 `MML2TEX_JAVA_BIN`、`MML2TEX_CLASSPATH`、`MML2TEX_SAXON_JAR`、`MML2TEX_XMLRESOLVER_JAR`、`MML2TEX_XMLRESOLVER_DATA_JAR`、`MML2TEX_STYLESHEET`、`MML2TEX_CATALOG`。
- 默认安装脚本不准备这套 Java/XSLT 工具链；只有显式提供这些资产并选择 `MATHML_CONVERTER_BACKEND=mml2tex` 时才使用。

### Elsevier

#### `ELSEVIER_API_KEY`

- 必填。
- Elsevier metadata 和全文 API 的核心凭证。

#### `ELSEVIER_INSTTOKEN`

- 可选。
- 机构授权场景补充凭证。

#### `ELSEVIER_AUTHTOKEN`

- 可选。
- Bearer token 形式的补充凭证。

#### `ELSEVIER_CLICKTHROUGH_TOKEN`

- 可选。
- clickthrough 场景补充凭证。

### Springer

Springer direct HTML / direct HTTP PDF 路线当前没有额外必填 publisher env：

- `provider_status()` 中会稳定表现为本地 `html_route` 已就绪
- 不再需要任何 Springer publisher 凭证

<a id="arxiv"></a>
### arXiv

arXiv 路线当前不需要 publisher 凭证；official HTML 主路径不依赖本机转换器：

- `provider_status()` 中 `metadata_api`、`html_route` 与 `pdf_fallback` 不依赖额外 env。
- `html_route` 固定标为 `ok`，表示可直接请求 arXiv official HTML 主路径。
- HTML 不可用、非 HTML、正文不足或质量门控失败时，直接进入 text-only PDF fallback。
- metadata enrichment 使用项目内部 Atom API client 调用 `https://export.arxiv.org/api/query` 的 `id_list` 精确查询，不依赖 PyPI `arxiv` / `feedparser` 包，也不实现关键词搜索、作者搜索或分页搜索。
- 当前不会下载 arXiv TeX 源码做本地 TeX / LaTeX 转换；只消费 arXiv official HTML。若 official HTML 缺失或质量不过关，即使 TeX 源码可能存在，也会直接进入 text-only PDF fallback。
- arXiv official HTML 仍兼容 ar5iv/LaTeXML 的 `ltx_*` DOM contract；这些 selector 集中在 provider 数据表中，并为普通 `article > section > h*/p` 标题、摘要和参考文献结构保留 fallback。
- ar5iv 服务端转换失败页的固定 fatal 文案集中在 provider contract 常量中；命中后该 HTML 被视为不可用并继续 fallback。
- HTML 资产下载失败会优先读取 transport 层 `RequestErrorCategory` 判定是否可重试；历史 substring 只作为旧诊断 payload 的兼容 fallback。

### IEEE

IEEE direct REST HTML / clean-browser HTML / direct HTTP PDF / seeded-browser PDF 路线当前没有额外必填 publisher env：

- `provider_status()` 中会稳定表现为本地 `html_route` 与 `pdf_fallback` 已就绪
- 不需要 IEEE API key
- 是否能拿到全文仍取决于 IEEE Xplore 当前对操作者运行环境的合法访问上下文，以及 endpoint/browser route 是否返回真实 full-text HTML 或 PDF

<a id="wiley-science-pnas-browser-workflow"></a>
### Wiley / Science / PNAS

#### `WILEY_TDM_CLIENT_TOKEN`

- 可选。
- 仅用于 `wiley` 的官方 TDM API PDF lane。
- 未配置时，`wiley` 仍可在 FlareSolverr / Playwright runtime 就绪时尝试 HTML 与 seeded-browser PDF/ePDF；已配置时，即使 browser runtime 不就绪，也可单独尝试 TDM PDF fallback。

#### `FLARESOLVERR_URL`

- 本地 FlareSolverr 服务地址。
- 默认 `http://127.0.0.1:8191/v1`。

#### `FLARESOLVERR_ENV_FILE`

- 对 `science` / `pnas` 必填。
- 对 `wiley` 的 FlareSolverr HTML 与 seeded-browser PDF/ePDF 路径必填；只使用 `WILEY_TDM_CLIENT_TOKEN` 的官方 TDM API PDF lane 时不需要。
- 必须显式指向当前仓库 `vendor/flaresolverr/` 下的 preset。

#### `FLARESOLVERR_SOURCE_DIR`

- 可选。
- 覆盖 repo-local FlareSolverr workflow 根目录。

#### `PAPER_FETCH_FLARESOLVERR_KEEP_SESSION`

- 可选。
- 默认未设置时，每次 `FlareSolverr HTML` 抓取结束都会调用 `sessions.destroy` 销毁本次 browser session。
- 设为 `1` / `true` / `yes` / `on` 时，会跨请求复用 FlareSolverr session、cookies 和 warm wait；这可能让浏览器进程保留到 Python 进程退出的 `atexit` 清理或手动清理。
- 这个变量只控制 FlareSolverr browser session 生命周期，不停止本地 FlareSolverr 服务；停止服务仍使用 `flaresolverr-down`。

<a id="flaresolverr-rate-limit-removal"></a>
本地 FlareSolverr 限速变量与账本已移除；browser workflow 不再读取 `FLARESOLVERR_MIN_INTERVAL_SECONDS`、`FLARESOLVERR_MAX_REQUESTS_PER_HOUR` 或 `FLARESOLVERR_MAX_REQUESTS_PER_DAY`。

更具体的启动与排障步骤见 [`flaresolverr.md`](flaresolverr.md)。

## 运行时护栏

### HTTP 连接池与缓存

`HttpTransport` 带短 TTL 的进程内 GET 缓存和可选磁盘 textual GET 缓存：

- 同一 DOI 的重复 Crossref / metadata 请求可直接命中缓存
- 只有小体积文本响应会入缓存
- PDF 和其他大体积二进制正文不会缓存
- 缓存 key 会脱敏 `api_key`、token、`mailto` 等敏感 query 字段；`Authorization`、`X-ELS-APIKey`、Wiley / Elsevier token header 等敏感 header 会用短 SHA-256 digest 区分不同凭据，不把原文写入 cache key、磁盘路径或 structured log
- `RuntimeContext(download_dir=...)` 会默认启用磁盘 textual GET 缓存，位置是 `<download_dir>/.paper-fetch-http-cache/`
- 磁盘缓存支持 `ETag` / `Last-Modified` 条件请求；stale 条目收到 `304` 时复用本地 body
- `PAPER_FETCH_HTTP_DISK_CACHE_DIR` 可显式指定磁盘 HTTP 缓存目录
- `PAPER_FETCH_HTTP_DISK_CACHE=1` 且未设置下载目录时，会使用用户数据目录下的 `http-cache`
- `PAPER_FETCH_HTTP_METADATA_CACHE_TTL` 控制磁盘缓存 freshness 秒数，默认 `86400`（1 day）；普通进程内 GET TTL 仍默认 `30` 秒
- `PAPER_FETCH_HTTP_DISK_CACHE_MAX_ENTRIES` 控制磁盘 textual GET cache 最大条目数，默认 `4096`；设为 `0` 表示不限制条目数
- `PAPER_FETCH_HTTP_DISK_CACHE_MAX_BYTES` 控制磁盘 textual GET cache 最大总字节数，默认 `536870912`（512 MiB）；设为 `0` 表示不限制总大小
- `PAPER_FETCH_HTTP_DISK_CACHE_MAX_AGE_DAYS` 控制磁盘 textual GET cache 最大保留天数，默认 `30`；设为 `0` 表示不按年龄清理
- `HttpTransport.cache_stats_snapshot()` 返回线程安全的累计计数：`memory_hit`、`disk_fresh_hit`、`disk_stale_revalidate`、`disk_304_refresh`、`miss`、`store`、`bypass`；golden criteria live review 的 sample 结果写入相对执行前的 delta，最终汇总日志保留累计快照

连接池与同 host 并发默认较保守：

- `PAPER_FETCH_HTTP_POOL_NUM_POOLS`：默认 `16`
- `PAPER_FETCH_HTTP_POOL_MAXSIZE`：默认 `4`
- `PAPER_FETCH_HTTP_PER_HOST_CONCURRENCY`：默认 `4`
- `PAPER_FETCH_ASSET_DOWNLOAD_CONCURRENCY`：默认 `4`，最小 `1`，控制 HTML / browser workflow / Elsevier body asset 下载 worker 上限

### HTTP 重试与大小限制

默认护栏包括：

- `max_response_bytes=32 MiB`
- 对 `5xx` 和 timeout 级网络错误做有限短重试
- `429` 只按 `Retry-After` 处理，不混进瞬时错误重试
- 底层使用 `urllib3.PoolManager` 复用连接
- Retry policy 使用 `urllib3.util.Retry` 表达；本地 wrapper 继续保留 public request options、structured logs、cancel checks、最大等待时间和 `RequestFailure` 形状

<a id="provider-status-local-boundary"></a>
### `provider_status()`

`provider_status()` 只检查本地条件，不主动探测远端 publisher API 连通性。

当前 provider 语义大致是：

- `elsevier`
  - 只检查官方全文 API key；`ELSEVIER_API_KEY` 配好即 `ready`，否则 `not_configured`。
- `springer`
  - 返回本地 direct HTML route 就绪状态；不依赖 FlareSolverr。
- `ieee`
  - 返回两条本地 check：`html_route` 覆盖 direct REST HTML 与 clean-browser HTML fallback 两种 mode，`pdf_fallback` 覆盖 direct HTTP PDF 与 seeded-browser PDF fallback 两种 mode；具体 mode 名在各 check 的 `details.mode` 中体现。不依赖 FlareSolverr 或 IEEE API key。
- `wiley`
  - 统一检查 `runtime_env`、`repo_local_workflow`、`flaresolverr_health`，以及可选的 `tdm_api_token`。
  - browser runtime ready 时，即使 `WILEY_TDM_CLIENT_TOKEN` 缺失，也应表现为 `ready`。
  - browser runtime 未配置但 `WILEY_TDM_CLIENT_TOKEN` 已配置时，通常表现为 `partial`，仍可尝试官方 TDM API PDF lane；如果 browser 检查本身报 `error`，provider 状态仍会反映该错误。
- `science` / `pnas`
  - 统一检查 `runtime_env`、`repo_local_workflow`、`flaresolverr_health`。
