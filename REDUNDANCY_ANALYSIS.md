# 代码库冗余分析

> 分析日期：2026-05-16
> 分支：`cloakbrowser-migration`（FlareSolverr → CloakBrowser 迁移，已完成至 Phase 9）
> 范围：`src/` 约 58k 行、`tests/` 约 46k 行
> 测试基线：`PYTHONPATH=src python -m pytest tests/unit tests/integration` → 1367 passed / 82 skipped / 264 subtests passed

## 概述

迁移整体很干净：源码中已无 `flaresolverr` 引用（仅迁移文档与 `CHANGELOG.md` 还提及）。
迁移残留的兼容垫片是一类冗余（§1-§2）；此外深度扫描还发现一批与迁移无关、
重构后遗留的死代码——模块级死函数（§6，约 312 行）与类内死方法（§7，约 32 行）。
第一轮并行调用 4 个 subagent 分区扫描（源码、MCP/运行时、测试、文档/脚本/本地产物），
补充了测试支撑的 facade、活跃代码里的旧命名、重复测试 helper、tracked 资源副本和 ignored
本地产物清理入口（§8-§11）。
第二轮又并行调用 4 个 subagent 按 provider、模型/抽取公共层、fixture/资源、CLI/CI/devtools
继续扫描，新增 provider 重复实现、公共 helper 重复、fixture manifest 漂移和脚本/CI/docs 漂移
（§12-§15）。
第三轮继续并行调用 4 个 subagent，并用本地 AST 重复体扫描复核，新增 split-module 聚合垫片、
provider/helper 小块完全重复、MCP/schema/cache 双维护、测试 stub 重复和文档/安装器漂移
（§16-§20）。
下文大致按"可立即删除"到"仅需关注"排序。

| 分类 | 影响 | 处置 |
|---|---|---|
| §1 死代码 | 约 25 行源码 + 1 个测试文件 | 可立即删除 |
| §2 兼容别名层 | `browser_workflow` 双命名 | 改 3 个测试后连带清除 |
| §3 本地体积 | `legacy/` 69MB + `build/`+`dist/` 3.4MB | 本地 `rm` 即可 |
| §4 迁移文档 | 根目录 4 份大文档 | 迁移收尾后归档 |
| §5 环境/命名 | 非冗余 | 仅需关注 |
| §6 跨文件死函数 | 32 个未用函数/类，约 312 行 | 可删除（建议逐项复核） |
| §7 类内死方法 | 9 个未用方法/property，约 32 行 | 可删除（已逐项核实） |
| §8 兼容 facade | MCP 同步 wrapper、runtime/http/provider registry facade | 先改测试/调用方再删 |
| §9 活跃命名/抽象冗余 | browser workflow 旧命名、Markdown IR 半迁移、公式 helper 别名 | 小步重命名或收敛 |
| §10 测试 helper 重复 | 多处重复 fixture/helper/清单 | 抽共享 helper |
| §11 本地产物/资源副本 | 约 800MB ignored 产物 + tracked 公式资源副本 | 分层清理/选定单一事实源 |
| §12 provider 重复实现 | Atypon/IEEE/Springer/Wiley 多处重复 orchestration/hook | 抽共享 hook/factory，保留高风险路径测试 |
| §13 公共层 helper 重复 | image/table/section/body metrics/provider key helper | 收敛到 neutral helper / typed snapshot |
| §14 fixture/资源漂移 | `body_assets` 未入 manifest、重复 expected、raw provenance | manifest 化或归档，勿按目录直接删 |
| §15 脚本/CI/docs 漂移 | 过期 CI import、no-op 参数、旧 Playwright env、孤立脚本 | 修正入口、删除或 deprecate |
| §16 聚合/compat 垫片 | `_core.py` 聚合副本、`ProviderWaterfallStep`、quality facade | 改内部调用后删除或 deprecate |
| §17 源码级重复小工具 | headers/cookie/xml/srcset/retry/reference DOI/response helper | 抽 provider-neutral helper |
| §18 MCP/workflow 双维护 | output TypedDict、cache decoder、allowed set、metadata merge/probe | 单一 schema/decoder/probe 编排 |
| §19 测试冗余 | service 数据 builder、logging handler、fake fetch/response、fixture path | 抽测试 support helper |
| §20 文档/脚本漂移 | AMS/Playwright/UA/skill installer/onboarding manifest 多处双维护 | 从 catalog/schema/manifest 生成或收敛 |

## 串行 Subagent 推进索引

本节是给 `/goal follow REDUNDANCY_GOAL.md` 使用的执行索引。原则是一次只让一个 subagent
拥有一个互不重叠的写入范围；主 agent 等待、审查、整合并运行针对性测试后，才进入下一个任务包。
每个任务完成后应在本节把状态从 `pending` 改成 `done` 或 `deferred: 原因`，并在对应章节补充处理说明。

| ID | 状态 | 对应章节 | 写入范围 | 目标 | 验证 |
|---|---|---|---|---|---|
| R00 | done | §5、全局 | 无或仅文档 | 确认 worktree、测试环境、当前基线；记录不能触碰的用户改动 | `git status --short` |
| R01 | done | §1 | `src/paper_fetch/runtime_playwright.py`、`src/paper_fetch/providers/_html_structure.py`、`browser_workflow/html_extraction.py`、对应测试 | 删除零消费者垫片和旧常量 alias | `PYTHONPATH=src python3 -m pytest tests/unit/test_runtime_browser.py tests/unit/test_browser_workflow_deps.py -q` |
| R02 | done | §16 | `models/_core.py`、`providers/atypon_browser_workflow/_core.py`、`extraction/html/assets/_core.py`、相关测试 | 已删除 split-module `_core.py` 聚合副本；测试改用具体 `assets.download` 模块入口 | `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_request_options.py tests/unit/test_models_render.py -q` |
| R03 | partial | §6、§7、§18 | 死函数/死方法所在模块；`workflow/types.py` | 删除高置信死代码和未用派生常量；脚本仍引用的 devtools/benchmark 项 deferred | `PYTHONPATH=src python3 -m pytest tests/unit -q` |
| R04 | partial | §2、§8、§9 | `browser_workflow/**`、`runtime_browser.py`、相关 browser/runtime 测试 | 收口 `direct_playwright` / Playwright 旧命名和 browser workflow alias；越界项 deferred | `PYTHONPATH=src python3 -m pytest tests/unit/test_browser_workflow_deps.py tests/unit/test_provider_waterfalls.py tests/unit/test_runtime_browser.py -q` |
| R05 | done | §8、§12、§18 | `mcp/**`、`workflow/request_builder.py`、`workflow/pipeline.py` | MCP facade 和低风险 schema 去重：`validate_query`、list coercer、allowed sets、`no_download` 单一解释点 | `PYTHONPATH=src python3 -m pytest tests/unit/test_mcp_payload_cache.py tests/unit/test_mcp_batch_resolve_payloads.py tests/unit/test_fetch_pipeline.py -q` |
| R06 | done | §12、§17 | provider shared helper、`providers/**`、`extraction/html/assets/requester.py` | 已抽低风险 provider/helper 重复：PDF headers、PDF fallback cookie、response adapter、XML local-name、reference DOI、retry 判断、IEEE diagnostics；高风险 provider orchestration 保持 deferred | 相关 provider 单测 + `PYTHONPATH=src python3 -m pytest tests/unit/test_arxiv_provider.py tests/unit/test_copernicus_provider.py tests/unit/test_pdf_fallback_helpers.py -q` |
| R07 | partial | §13、§17 | `models/render.py`、`workflow/rendering.py`、`extraction/**`、`quality/**` | 已收敛图片引用 helper、inline body asset filter、section hint category、image dimensions 薄 alias、extraction 内 image payload 直接导入、provider key normalization；Markdown table renderer、BodyQualityMetrics snapshot/coercer、section hint typed/dict 大重构、workflow provider key 收敛及越界 provider 调用方 deferred | `PYTHONPATH=src python3 -m pytest tests/unit/test_models_render.py tests/unit/test_html_shared_helpers.py tests/unit/test_html_availability.py -q` |
| R08 | partial | §10、§19 | `tests/**` support helper 和对应测试文件 | 已抽低风险测试 helper：`load_yaml`、scaffold runner、installer executable writer、HTTP response factory、`RecordCaptureHandler`、arXiv/IEEE golden path；deferred：`_extract_fixture_markdown`、provider workflow helper、CLI `fake_fetch`、service builders 等中风险/越界项 | `PYTHONPATH=src python3 -m pytest tests/unit -q` |
| R09 | done | §15、§20 | `.github/**`、`scripts/**`、`install*.sh`、`docs/**`、`skills/**`、installer 测试 | 已修正文档/脚本/CI 漂移：Windows CI import 改到 `fetch_tool`、CI Playwright smoke 改 CloakBrowser package smoke、删除 `--skip-playwright-install` no-op、Codex wrapper 去掉 Playwright env 分支，并收口 AMS/UA/failure signal 文档 | `PYTHONPATH=src python3 -m pytest tests/unit/test_ci_release_workflow.py tests/unit/test_offline_install.py tests/integration/test_skill_template.py -q` |
| R10 | done | §14 | `tests/fixtures/**`、fixture catalog/manifest、`figures/**`、`references/**` | 已将测试隐式依赖的 Wiley/Science `body_assets` 显式登记到 golden manifest，并加 provenance 保护；未删除资源 | `PYTHONPATH=src python3 -m pytest tests/unit/test_atypon_browser_workflow_provider_html.py tests/integration/test_fixture_provenance.py -q` |
| R11 | done | §11、§3 | 清理脚本/文档；本地 ignored 目录只在明确确认后处理 | 已确认 `scripts/clean-local-artifacts.sh` 是统一 dry-run/clean 入口，且删除前通过 `git check-ignore` 限定 ignored 本地产物 | `scripts/clean-local-artifacts.sh --dry-run` |
| R12 | deferred | §12、§18 | Wiley/Springer/provider orchestration、metadata probe/merge | 已只读审查；Wiley orchestration、Springer 双模块、metadata merge/probe bundle 均因行为顺序和 fixture 覆盖缺口 deferred，并记录最小拆分计划 | `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_waterfalls.py tests/unit/test_provider_request_options.py tests/unit/test_springer_html_regressions.py tests/unit/test_wiley_provider.py tests/unit/test_service_metadata_routing.py tests/unit/test_service_probe_and_assets.py tests/unit/test_service_pdf_and_provider_fallbacks.py tests/unit/test_metadata_layer_merge.py tests/integration/test_golden_corpus.py -q` |
| R13 | done | 全部 | `REDUNDANCY_ANALYSIS.md`、必要 docs | 已同步最终状态、R03/R12 deferred 清单、文档漂移和完整测试结果 | `PYTHONPATH=src python3 -m pytest tests/unit tests/integration -q` |

**串行交接格式**：每个 subagent 最终必须列出 `changed files`、`tests run`、`remaining risks`、
`deferred items`。主 agent 审查 diff 后才能进入下一项；如果发现任务需要改到下一个任务包的文件，
应停止并把该项拆分或 deferred，避免跨包冲突。

**R00 执行记录（2026-05-16）**：已读取 `AGENTS.md`、`REDUNDANCY_GOAL.md` 和本分析文档。
`git status --short` 显示当前 worktree 已包含多处迁移/清理改动和未跟踪的
`REDUNDANCY_ANALYSIS.md`、`REDUNDANCY_GOAL.md`、`docs/legacy-browser-runtime.md`；后续任务包只触碰
各自允许的写入范围，不回滚或覆盖无关既有改动。验证命令统一使用
`PYTHONPATH=src python3 -m pytest ... -q` 并复用项目 pytest 配置。

**R09 执行记录（2026-05-16）**：已完成 §15、§20 的低风险漂移修正，未触碰生产 provider/extraction/workflow
代码。验证命令
`PYTHONPATH=src python3 -m pytest tests/unit/test_ci_release_workflow.py tests/unit/test_offline_install.py tests/integration/test_skill_template.py -q`
通过。

**R10 执行记录（2026-05-16）**：已把
`tests/fixtures/golden_criteria/10.1029_2004gb002273/body_assets/*.png` 和
`tests/fixtures/golden_criteria/10.1126_sciadv.adl6155/body_assets/*.jpg` 全部登记到
`tests/fixtures/golden_criteria/manifest.json` 的对应 sample `assets` 中；同时新增 provenance
测试，要求 `golden_criteria/*/body_assets` 下的文件必须出现在 manifest assets 里，并更新
golden criteria fixture README。未删除任何 fixture 资源。验证命令
`PYTHONPATH=src python3 -m pytest tests/unit/test_atypon_browser_workflow_provider_html.py tests/integration/test_fixture_provenance.py -q`
通过（18 passed, 3 subtests passed）。

**R11 执行记录（2026-05-16）**：已复核 `scripts/clean-local-artifacts.sh` 和
`docs/deployment.md`。脚本默认只面向 `.pytest_cache`、`.ruff_cache`、`.mypy_cache`、`build`、
`dist`、`.paper-fetch-runs`、`live-downloads`、`rollout-*.jsonl` 等 ignored 本地产物，且删除前逐项
执行 `git check-ignore`；本轮只运行 `scripts/clean-local-artifacts.sh --dry-run`，输出将移除
`.pytest_cache`、`.ruff_cache`、`build`、`dist`、`.paper-fetch-runs`、`live-downloads`，未删除任何文件。

**R12 执行记录（2026-05-16）**：已按 §12、§18 做只读审查，未修改 provider/workflow 代码。
Wiley orchestration deferred：`WileyClient.fetch_raw_fulltext()` 中 TDM API 插入、runtime 缺失容忍、
marker/warning 与 missing env 组合覆写属于 provider 行为顺序，不能作为小改安全收敛。Springer HTML
双模块 deferred：`html_springer_nature` 与 `_springer_html` 仍同时维护 root/abstract/body 选择逻辑，
但 full golden expected-summary 覆盖默认有 82 个 skipped。metadata merge/probe deferred：现有
`merge_metadata_layers()` 还不能无损表达 explicit blank scalar、scalarize list/mapping、
public landing URL 优先级、语义作者去重；`fetch_metadata_for_resolved_query()` 与
`probe_has_fulltext()` 的并发 probe 相似但不等价。最小拆分计划：先补 Wiley/Springer/metadata
characterization tests，再分别抽 provider hook、Springer root/body context、metadata merge 规则能力和
只读 `MetadataProbeBundle`。验证命令
`PYTHONPATH=src python3 -m pytest tests/unit/test_provider_waterfalls.py tests/unit/test_provider_request_options.py tests/unit/test_springer_html_regressions.py tests/unit/test_wiley_provider.py tests/unit/test_service_metadata_routing.py tests/unit/test_service_probe_and_assets.py tests/unit/test_service_pdf_and_provider_fallbacks.py tests/unit/test_metadata_layer_merge.py tests/integration/test_golden_corpus.py -q`
通过（231 passed, 82 skipped, 7 subtests passed）。

**R13 执行记录（2026-05-16）**：已同步最终状态、剩余 deferred 清单和文档漂移。额外修正：
`docs/architecture/target-architecture.md` 移除已删 `StatusProvider` 的架构说明；
`tests/integration/test_architecture_closeout.py` 将已删 `_core.py` 聚合垫片移入“保持删除”断言而不再纳入
cycle graph；`docs/extraction-rules.md` 将 Wiley 资产下载测试名从旧 `shared_playwright` 同步为
`shared_browser`。第一次完整验证暴露上述两个静态/文档漂移后已修复。最终验证命令
`PYTHONPATH=src python3 -m pytest tests/unit tests/integration -q`
通过（1371 passed, 82 skipped, 1 warning, 264 subtests passed）。`git diff --check` 通过。

**R01 执行记录（2026-05-16）**：已删除 `runtime_playwright.py`、
`test_runtime_playwright.py`、`providers/_html_structure.py`，并移除
`browser_workflow/html_extraction.py` 中零消费者的
`_DIRECT_PLAYWRIGHT_HTML_TIMEOUT_MS`、`_DIRECT_PLAYWRIGHT_HTML_BLOCKED_RESOURCE_TYPES`
和 `_direct_playwright_browser_context_seed`。`fetch_html_with_direct_playwright` 仍按 R04
保留。

**R03 执行记录（2026-05-16）**：已按 §6、§7、§18 对目标符号运行 `rg` 复核，并删除无真实代码
消费者的高置信死函数、私有死方法/property、`StatusProvider` protocol、以及
`HTML_BODY_ASSET_DEFAULT_PROVIDERS` / `HTML_BODY_ASSET_DEFAULT_SOURCES` 两个未用派生常量；
同时清理了仅服务已删 `language.py` XML/非英文剥离路径的私有 helper 和必要 import。
`providers/browser_workflow/fetchers/diagnostics.py` 已有其他任务留下的 legacy alias 删除，本轮只删除
`_looks_like_cloudflare_challenge_failure`。验证命令
`PYTHONPATH=src python3 -m pytest tests/unit -q` 通过：`1239 passed, 1 warning, 262 subtests passed`。

R03 deferred：`collect_formula_samples` 被 `scripts/benchmark_formula_converters.py` 导入和调用；
`export_geography_issue_artifacts` 被 `scripts/export_geography_issue_artifacts.py` 导入和调用；
`default_report_paths` 被 `scripts/run_geography_live_report.py` 导入和调用。这三个引用方均不在 R03
写入范围内，需在脚本/devtools 任务中一并处理。`docs/architecture/target-architecture.md` 中过期的
`StatusProvider` 说明已在 R13 文档同步中移除。

**R04 执行记录（2026-05-16）**：已删除 `fetch_html_with_direct_playwright` 及
`browser_workflow.__init__` 重导出；`BrowserWorkflowDeps` 已移除 `_LEGACY_DEP_ALIASES`、
旧字段 constructor 兼容、旧 property 和 `__getattr__` alias；`client.py`、`pdf_fallback.py`
和默认 deps 已改用 `_build_shared_browser_*`、`_choose_browser_seed_url`、
`fetch_html_with_fast_browser`、`fetch_pdf_with_browser`。`fetchers` 子包已删除
`_BasePlaywrightDocumentFetcher`、`_SharedPlaywright*`、`_ThreadLocalSharedPlaywright*`、
`_build_shared_playwright_*`、`_choose_playwright_seed_url`、`fetch_image_document_with_playwright`
等旧 alias，并保留新名 `fetch_image_document_with_browser`。`runtime_browser.py` 已删除
`PlaywrightContextManager`、`PlaywrightUnavailableError`、`launch_playwright_chromium`。
相关 browser/runtime 测试已迁到新名。验证：
`PYTHONPATH=src python3 -m pytest tests/unit/test_browser_workflow_deps.py tests/unit/test_provider_waterfalls.py tests/unit/test_runtime_browser.py -q`
通过（34 passed, 3 subtests passed）；
`PYTHONPATH=src python3 -m pytest tests/unit/test_browser_workflow_namespace.py tests/unit/test_browser_workflow_fetchers.py tests/unit/test_atypon_browser_workflow_provider_retries.py tests/unit/test_atypon_browser_workflow_candidates.py tests/unit/test_provider_request_options.py tests/unit/test_ams_provider.py tests/unit/test_atypon_browser_workflow_provider_html.py -q`
通过（105 passed, 42 subtests passed）。

R04 deferred：`direct_playwright_html_preflight` 到 `fast_browser_html_preflight`
需要同步修改 `src/paper_fetch/providers/pnas.py` 和 `docs/providers.md`，超出本任务写入范围；
建议后续以 provider/docs 联动小任务处理。`RuntimeContext.playwright_browser`、
`RuntimeContext.new_playwright_context`、`RuntimeContext.close_playwright` 和
`providers/_ieee_browser_html.py` 的调用也超出本任务写入范围；建议后续先把 IEEE 调用迁到
`new_browser_context`，再删除 `runtime.py` 旧 runtime alias。`_pdf_fallback.fetch_pdf_with_playwright`
及 provider 模块中的同名兼容项按本任务边界保留。

**R05 执行记录（2026-05-16）**：已将 MCP schema 中的 `OutputMode`、`AssetProfile`、
`ArtifactMode` allowed set 改为从 canonical Literal 类型派生；`HasFulltextRequest` 和
`FetchPaperRequest` 共用必填 query validator，`authors` / `preferred_providers` 共用单值 list
coercer。`build_fetch_pipeline_request()` 不再因 `no_download` 改写 `artifact_mode`，该语义保留在
`FetchPipeline.runtime_context()` 统一解释。已用 `rg` 复核同步 `batch_resolve_tool`、
`batch_check_tool`、`fetch_paper_tool` 仅仓库测试引用；相关测试迁到 async tool 或 payload 层后，
删除这些同步 tool wrapper 和测试 support 中的对应白盒入口。未触碰 output TypedDict、cache decoder、
metadata merge/probe 等中高风险项。验证命令
`PYTHONPATH=src python3 -m pytest tests/unit/test_mcp_payload_cache.py tests/unit/test_mcp_batch_resolve_payloads.py tests/unit/test_fetch_pipeline.py -q`
通过（61 passed, 2 subtests passed）。

## 1. 确定可删除的死代码

| 项目 | 位置 | 状态 |
|---|---|---|
| `runtime_playwright.py` | `src/paper_fetch/runtime_playwright.py` | 3 行纯重导出垫片。`src/` 内无任何导入方，未注册到 `paper_fetch/__init__.py` 公开 API |
| `test_runtime_playwright.py` | `tests/unit/test_runtime_playwright.py` | 8 行，仅断言 `PlaywrightContextManager is BrowserContextManager`，即只测试上面那个垫片 |
| `_html_structure.py` | `src/paper_fetch/providers/_html_structure.py` | 17 行 `extraction.html.semantics` 重导出垫片。**导入方为零**（grep `_html_structure` 仅命中无关符号 `_analyze_html_structure`） |
| `_DIRECT_PLAYWRIGHT_HTML_TIMEOUT_MS` | `browser_workflow/html_extraction.py:62`（并在 `:66` 的 `__all__`） | legacy alias，无任何消费者 |
| `_DIRECT_PLAYWRIGHT_HTML_BLOCKED_RESOURCE_TYPES` | `browser_workflow/html_extraction.py:63` | legacy alias，无任何消费者 |
| `_direct_playwright_browser_context_seed` | `browser_workflow/html_extraction.py:301` | legacy alias，无任何消费者 |

删除 `runtime_playwright.py` 时应同时删除 `test_runtime_playwright.py`（该测试存在的唯一目的就是验证这个垫片）。

## 2. 仅靠测试存活的兼容别名层

迁移在 `browser_workflow` 中保留了一套 `_playwright` / `direct_playwright` 旧命名别名，与新名（`_browser` / `fast_browser`）并存。入口：

- `browser_workflow/shared.py:45-52` — `_LEGACY_FAST_BROWSER_FETCHER_ALIAS`、`_LEGACY_DEP_ALIASES`（4 条旧→新映射）
- `browser_workflow/fetchers/__init__.py:40-44` — 通过 `globals()` 注入 `_BasePlaywrightDocumentFetcher`
- `browser_workflow/html_extraction.py:300-301` — `fetch_html_with_direct_playwright` 等别名

其中 `fetch_html_with_direct_playwright`（新名 `fetch_html_with_fast_browser`）：

- 生产代码不再使用；
- 只有 `browser_workflow/__init__.py:54` 的重导出映射和 3 个测试引用旧名：
  `tests/unit/test_browser_workflow_deps.py`、`tests/unit/test_browser_workflow_namespace.py`、`tests/unit/test_provider_waterfalls.py`。

即"别名为测试而留、测试又测别名"的循环冗余。代码里甚至用字符串拼接
（`"fetch_html_with_direct" "_playwright"`、`"_Base" "PlaywrightDocumentFetcher"`）来规避 grep。

**建议**：把上述 3 个测试改用新名，再删除别名机制。
注意 `_BasePlaywrightDocumentFetcher` 仍被 `browser_workflow/fetchers/context.py` 实际使用，
需连同 `context.py` 一起改名，不能单独删除——这部分需逐符号核实。

## 3. 本地体积膨胀

`legacy/flaresolverr/` 在磁盘上残留 **69MB / 3330 个文件**，含一个完整的 `.venv-flaresolverr` 虚拟环境。

- 已脱离 git 跟踪（删除已暂存）；
- 已加入 `.gitignore:40`（`legacy/flaresolverr/`）。

属纯本地残留，`rm -rf legacy/` 即可回收空间，不影响仓库。

此外 `build/`（1.8MB）与 `dist/`（1.6MB）是构建产生的旧版包副本，未被 git 跟踪、
已在 `.gitignore:18-19` 登记。注意：它们里面有过期的 `paper_fetch` 源码副本，
做死代码扫描或 `grep` 时**必须排除**，否则旧副本里的引用会把死代码误判为"在用"。

## 4. 迁移文档的潜在冗余

仓库根目录有 4 份迁移文档，迁移期间有用，收尾后建议归并/归档：

| 文件 | 大小 |
|---|---|
| `CLOAKBROWSER_FULL_MIGRATION_PLAN.md` | 58KB |
| `CLOAKBROWSER_MIGRATION_RUNBOOK.md` | 27KB |
| `CLOAKBROWSER_MIGRATION_EVALUATION.md` | 24KB |
| `MIGRATION_DECISIONS.md` | 11KB |

长期版说明已落在 `docs/legacy-browser-runtime.md`（替代已删除的 `docs/flaresolverr.md`）。
`CHANGELOG.md` 内仍有 19 处 `flaresolverr` 提及——属历史记录，保留即可。

## 5. 非冗余但需关注（非清理项）

- **测试环境不一致**：`site-packages` 装有迁移前的旧 `paper_fetch`，直接运行 `pytest`（不带
  `PYTHONPATH=src`）会因找不到 `runtime_browser` 等模块产生 82 个收集错误。
  建议 `pip install -e .` 重装为可编辑模式。这不是冗余，但会掩盖真实测试状态。
- **命名易混淆（非重复）**：
  - `providers/_atypon_browser_workflow_postprocess.py`（45 行，共享辅助）
    与 `providers/atypon_browser_workflow/postprocess.py`（482 行）是不同模块；
  - `providers/_atypon_browser_workflow_profiles.py`（199 行）
    与 `providers/atypon_browser_workflow/profile.py`（600 行）同理。
  内容不重复，但根级 `_atypon_browser_workflow_*` 与同名子包并存，建议后续统一归位。
- `runtime_browser.py:13,22,79` 仍带 "legacy" / Playwright 命名的别名（`PlaywrightUnavailableError`、
  `launch_playwright_chromium`、`PlaywrightContextManager`）。本轮已在 §8 复核为仓库内无真实消费者；
  清理顺序见 §8 / 清理建议第 5 条。

## 6. 深度扫描：跨文件未使用的函数/类

对 `src/` 全部模块级 `def` / `class` 做 AST 提取 + 全仓引用计数扫描。
已排除以下偽阳性来源：`src/` 内无 star import；抽样核实无装饰器注册；
`__all__` 字符串导出与字符串动态访问均会被计入；`tests/`（含 `tests/live/`）已纳入。

结果：**32 个模块级函数/类在整个仓库（src + tests + docs + references）中只出现 1 次
（即定义本身），从未被调用或导入，共约 312 行。** 它们集中成若干"被遗弃的小功能"，
疑似重构后遗留：

| 模块 | 死符号 | 行数 | 疑似原因 |
|---|---|---|---|
| `extraction/html/language.py` | `strip_non_english_html_nodes`、`collect_xml_abstract_blocks`、`strip_non_english_xml_subtrees` | 65 | 整套"非英文剥离"逻辑无人调用 |
| `workflow/fulltext.py` | `maybe_save_provider_payload`、`_provider_html_output_path`、`_apply_provider_artifacts` | 44 | "保存 provider 产物"功能被孤立 |
| `providers/_article_markdown_elsevier_tables.py` | `collect_elsevier_table_rows`、`elsevier_table_has_spans`、`render_elsevier_table_rows` | 25 | Elsevier 表格渲染旧路径 |
| `mcp/_instructions.py` | `format_defaults_markdown`、`format_environment_markdown`、`format_error_contract_markdown` | 18 | instruction 文案格式化器被替换 |
| `providers/atypon_browser_workflow/postprocess.py` | `_markdown_has_abstract_heading`、`_known_abstract_block_texts` | 11 | abstract 检测旧逻辑 |
| `extraction/html/_runtime.py` | `_markdown_promo_tokens`、`body_character_count` | 5 | — |
| `providers/protocols.py` | `StatusProvider`（Protocol 类） | 6 | 定义且被 `docs/architecture/target-architecture.md:228` 描述为"用于 workflow typing"，但代码中无任何类型注解使用它 |

其余 13 个独立的死函数：

| 位置 | 死符号 |
|---|---|
| `extraction/html/inline.py:460` | `join_inline_fragments` |
| `formula/convert.py:1070` | `collect_formula_samples` |
| `mcp/cache_index.py:144` | `_write_index` |
| `mcp/fetch_tool.py:150` | `_call_service_fetch_paper`（疑被同文件 `_fetch_paper_envelope` 取代） |
| `models/quality.py:296` | `_diagnostic_signals` |
| `provider_catalog.py:285` | `url_provider_tokens` |
| `providers/_article_markdown_math.py:185` | `render_display_formula` |
| `providers/_html_authors.py:68` | `load_json_assignment` |
| `providers/_html_section_markdown.py:438` | `needs_space_between` |
| `providers/_ieee_html.py:289` | `_ieee_asset_dedupe_key` |
| `providers/_springer_html.py:434` | `_springer_is_figure_or_illustration_context` |
| `providers/browser_workflow/fetchers/diagnostics.py:26` | `_looks_like_cloudflare_challenge_failure` |
| `quality/html_availability.py:177` | `_looks_like_explicit_body_container` |

devtools（内部工具，优先级低）：

| 位置 | 死符号 |
|---|---|
| `paper_fetch_devtools/geography/issue_artifacts.py:100` | `export_geography_issue_artifacts`（39 行） |
| `paper_fetch_devtools/geography/live.py:213` | `default_report_paths` |

**扫描范围限制**：仅覆盖模块级 `def` / `class`，不含类内方法、未用的局部变量/常量。
删除前建议逐项快速复核（尤其 `StatusProvider` 这类 Protocol、以及含同名脚本描述的
devtools 函数）。`workflow/fulltext.py`、`extraction/html/language.py` 各有 3 个连续死函数，
更像整块被遗弃的功能，可整段移除。

## 7. 深度扫描：类内未使用的方法

对 `src/` 全部 **443 个类内方法**做 AST 提取 + 类作用域引用解析。已排除偽阳性：
dunder（66）、基类/Protocol/ABC 覆写（106）、stdlib `HTMLParser` 回调覆写（8，
`handle_starttag` 等由 `.feed()` 隐式调用）、装饰器注册（21）。
扫描排除了未追踪的 `build/`、`dist/` 旧副本（见 §3）。

结果：**9 个类内成员（8 个方法 + 1 个 property）全仓零引用，约 32 行。** 已逐项核实。

### Tier 1 — 高置信（私有方法，全仓零引用）

| 位置 | 类.方法 | ~行 | 说明 |
|---|---|---|---|
| `http/cache.py:381` | `CacheMixin._discard_cache_entry` | 3 | 其逻辑在 `cache.py:174-178` 被直接内联，方法本身无人调用 |
| `providers/crossref.py:53` | `CrossrefClient._headers` | 2 | 委托 `self.lookup.headers()` 的死包装（注意 `SpringerClient._headers` 是另一个、仍在用——名称碰撞） |
| `providers/crossref.py:56` | `CrossrefClient._query_params` | 2 | 委托 `self.lookup.query_params()` 的死包装 |
| `providers/crossref.py:93` | `CrossrefClient._normalize_message` | 2 | 委托 `self.lookup.normalize_message()` 的死包装 |
| `metadata/crossref.py:213` | `CrossrefLookupClient._normalize_message` | 2 | 类内已直接用公开的 `normalize_message`，此私有别名孤立 |
| `providers/elsevier.py:677` | `ElsevierClient._fetch_official_payload` | 2 | 委托 `self._fetch_official_xml_payload()` 的死包装 |

`CrossrefClient` 是委托给 `CrossrefLookupClient` 的薄 facade，`_headers` / `_query_params`
/ `_normalize_message` 三个私有包装方法从未接入，可整组删除。

### Tier 2 — 公开方法，全仓零引用（已逐项核实，置信度等同 Tier 1）

| 位置 | 类.方法 | ~行 | 说明 |
|---|---|---|---|
| `workflow/types.py:115` | `FetchStrategy.effective_asset_profile_for_source` | 2 | 兄弟方法 `effective_asset_profile_for_provider`（line 112）在用；此 `_for_source` 变体零引用 |
| `runtime.py:296` | `RuntimeContext.get_or_set_session_cache` | 13 | `get_session_cache` / `set_session_cache` 在用；此 `get_or_set` companion 仅旧 `build/` 副本引用过 |

### 低置信 — property（按属性访问，删前再确认）

| 位置 | 类.property | ~行 | 说明 |
|---|---|---|---|
| `models/markdown.py:78` | `MarkdownImageMatch.text_without_attrs` | 4 | 该类在用，但消费方直接读 `.text` 属性，此 `@property` 似未被读取 |

**审计提醒**：
- 名称碰撞是主要陷阱——`CrossrefClient._headers` 死、`SpringerClient._headers` 活，
  必须按类作用域区分，不能只看全局名字计数。
- `也定义在子类`一类（18 个方法）被保守地双向排除，未单独审计"父类方法仍在用、
  但子类覆写已死"的理论遗漏。
- 扫描仅覆盖类内方法；未含未用的类属性/实例属性。

## 8. Subagent 补充：兼容 facade / 旧 API 出口

4 个 subagent 分区扫描后，新增发现一批“仓库内部不用，但为旧入口或测试白盒访问而保留”的
facade。它们比 §6/§7 的死代码风险高，因为可能有包外用户；建议按“先迁移内部测试，再
deprecate 或删除”的节奏处理。

| 项目 | 位置 | 证据 | 去除方式 |
|---|---|---|---|
| `build_provider_registry` 动态注入 | `providers/base.py:826-852` | `_build_provider_registry_compat` / `_install_provider_registry_compat` 仅自身命中；正式入口是 `providers/registry.py:22` 的 `build_clients` | 若不承诺包外兼容，删除 compat 函数和模块尾部 `_install_provider_registry_compat()`；否则先发 deprecation |
| MCP 同步 batch facade | `mcp/batch.py:247`、`:272`、`:305`、`:321` | MCP server 只注册 `batch_resolve_tool_async` / `batch_check_tool_async`（`mcp/server.py:488`、`:504`）；同步入口主要由 `tests/unit/_mcp_support.py` 和 `test_mcp_batch_resolve_payloads.py` 调用 | 把测试迁到 async tool 或 `_run_batch_sync` / `_run_batch_async` 层后，删除 `batch_resolve_payload`、`batch_check_payload`、`batch_resolve_tool`、`batch_check_tool` |
| MCP 同步 `fetch_paper_tool` | `mcp/fetch_tool.py:532` | server 注册 `fetch_paper_tool_async`（`mcp/server.py:407`）；同步 wrapper 在 `src/` 无生产调用，测试仍白盒调用 | 删除同步 `fetch_paper_tool`；保留 `fetch_paper_payload`，因为 batch article 模式仍复用 payload shaping |
| HTTP facade 私有导出 | `http/__init__.py:36`、`:71`、`:112`、`:131` | `paper_fetch.http` 重导出 `_CacheKey`、`_DiskCacheEntry`、`_PreparedRequest` 和 `time`；生产消费者直接走子模块，`time` 主要用于测试 patch | 测试改 patch `paper_fetch.http.cache.time` / `transport.time` / `retry.time`，再从 `http.__all__` 移除私有名和 `time` |
| `metadata.__init__` lazy facade | `metadata/__init__.py:17`、`:24` | 内部代码直接导入 `metadata.crossref` / `metadata.types`，未见 `from paper_fetch.metadata import CrossrefLookupClient` | 若不保留短路径 API，删除 `__getattr__` 和 package-level `__all__`；更稳妥是先 deprecate |
| `resolve.__init__` re-export | `resolve/__init__.py:3` | 内部和测试使用 `paper_fetch.resolve.query`；仅 `tests/unit/test_resolve_query.py` 有 `from paper_fetch.resolve import query as resolve_query`，不是 `ResolvedQuery` / `resolve_query` re-export | 清空 package re-export 或改为 lazy；若对外公开短路径，先保留 |
| `runtime_browser` 的 Playwright 旧名 | `runtime_browser.py:13`、`:22`、`:79` | `PlaywrightContextManager` 主要服务 §1 的 `runtime_playwright` shim/test；`launch_playwright_chromium`、`PlaywrightUnavailableError` 除自身和 shim 外无消费者 | 删除 §1 shim 后，从 `runtime_browser.__all__` 和模块中移除这些旧名；`RuntimeContext.new_playwright_context` 需先把 `_ieee_browser_html.py:89` 改为 `new_browser_context` |

## 9. Subagent 补充：活跃代码里的命名/抽象冗余

这一组不是死代码，不能直接删；问题是旧命名或半迁移抽象仍夹在活跃路径里，后续维护会继续
产生双入口和语义漂移。

| 项目 | 位置 | 证据 | 去除方式 |
|---|---|---|---|
| `direct_playwright_html_preflight` 旧字段名 | `browser_workflow/profile.py:47`、`:55`，`pnas.py:81`，`bootstrap.py:105` | 实际调用已是 `fetch_html_with_fast_browser`，日志仍叫 `browser_workflow_direct_html_preflight`；文档 `docs/providers.md:265` 还提到 legacy `playwright_direct` 标记，但代码已产出 `cloakbrowser_fast` | 重命名为 `fast_browser_html_preflight`，同步更新 PNAS profile、bootstrap 日志、测试和 docs |
| Markdown formula/caption IR 半迁移 | `extraction/markdown_render/_ir.py:30`、`:38`，`formulas.py:22`，`captions.py:9` | `MarkdownFormula` / `MarkdownCaption` / `render_formula` / `render_caption` 仅 `tests/unit/test_markdown_render_ir.py` 使用；生产公式路径走 provider helper 和 HTML helper | 二选一：要么把 XML/HTML 公式与 caption 真正迁到 IR；要么从 `markdown_render/__init__.py` 去掉未采用出口并删相应测试 |
| `markdown_render.formulas` 判断 helper 薄别名 | `extraction/markdown_render/formulas.py:75-88` | `first_html_formula_image_url`、`is_html_formula_container` 等只是转发 `extraction/html/formula_rules.py`；调用方主要是同文件和 `_html_section_markdown.py` | `_html_section_markdown.py` 直接导入 `formula_rules`，渲染层只保留 `render_html_formula_*` |
| MathML fallback 二次调用 | `_article_markdown_math.py:36-42`，`markdown_render/formulas.py:103-105`，`_ams_html.py:393-397`，`_article_markdown_math.py:201-205` | `render_external_mathml_expression` 失败时已经调用内部 `render_mathml_expression`，调用方又在空结果时二次 fallback；“internal MathML fallback” note 基本不可达 | 固定函数契约后删除调用方二次 fallback；如需诊断，改成返回带状态的结果 |
| Atypon `normalization` 再导出公式私有 helper | `atypon_browser_workflow/normalization.py:45-65`、`:601-617` | `.normalization` 从 `.formulas` 导入大量私有 helper 并放入 `__all__`；测试通过 `normalization` 间接调用公式 helper | 测试改直接导入 `atypon_browser_workflow.formulas`；`normalization` 内部只保留内部引用，不再通过 `__all__` 暴露 |
| tracked 公式资源副本 | `package.json`、`package-lock.json`、`scripts/mathml_to_latex_*.mjs` 与 `src/paper_fetch/resources/formula/*` | `cmp` 显示 4 组文件完全相同；包内资源用于安装，根目录/`scripts/` 用于 repo-local fallback 和 Node workspace | 选一个单一事实源：要么以 `src/paper_fetch/resources/formula` 为 canonical 并调整 dev fallback；要么保留根目录源文件并增加同步校验/生成步骤，避免漂移 |

## 10. Subagent 补充：测试侧冗余

这些不影响生产包，但会放大接口变更成本。优先抽纯 helper，不要把断言逻辑过度集中。

| 项目 | 位置 | 去除方式 | 风险 |
|---|---|---|---|
| 重复 `load_yaml` | `tests/unit/test_known_providers_sync.py:19`、`test_provider_manifest_schema.py:20` | 复用 `tests/unit/_manifest_sync.py:35` 的 `load_yaml` / manifest helper | 低，失败文案略变 |
| 重复 `_run_scaffold` | `test_scaffold_provider.py:15`、`test_scaffold_docs_sync.py:12` | 抽到 `tests/unit/_scaffold_support.py` | 低 |
| 重复 `FakeResponse` | `test_pdf_fallback_helpers.py:554`、`:614` | 提升为文件级 `_FakeUrlopenResponse`，各测试只保留不同 opener | 低，注意不共享可变状态 |
| 重复 `_extract_fixture_markdown` | `test_atypon_browser_workflow_postprocess.py:54`、`test_atypon_browser_workflow_markdown.py:56` | 抽到 Atypon markdown support helper | 低到中，保留各文件自己的断言 |
| live 测试清理/env helper | `tests/live/test_live_publishers.py:51` / `:56` 与 `test_live_mcp.py:36` / `:41` | 在 `tests/live/_runtime_env.py` 增加 tempdir cleanup 和 `require_env` | 低 |
| provider workflow helper 副本 | `_atypon_browser_workflow_provider_support.py:105`、`:109`、`:190` 与 `test_provider_waterfalls.py:26`、`:35`、`:40` | 抽到更中性的 `_provider_workflow_support.py`，供 Atypon 和 waterfall 测试共用 | 中，`test_provider_waterfalls.py` 覆盖面更宽 |
| service/regression stub 重复 | `test_regression_samples.py:32`、`:62` 与 `_paper_fetch_support.py` 的 `StubProvider` / `RecordingTransport` | 复用共享 stub，必要时给共享 stub 增加轻量参数 | 中，收益是减少 service 接口变更双维护 |
| 已删除兼容模块清单重复 | `test_architecture_closeout.py:18` 与 `test_import_boundaries.py:30` | 保留文件存在性 guard 与 import guard，但共用同一份清单 | 中，两个 guard 语义不同，不能只删其一 |

fixture 侧不建议按 DOI 去重：`tests/fixtures` 约 83MB，`block` 与 `golden_criteria` 有 5 个 DOI 重叠，
但 manifest 区分了 fulltext golden 与 access-gate/block 语义。若要减体积，应先做“manifest 未引用
fixture”扫描，而不是按 DOI 删除。

## 11. Subagent 补充：本地产物、脚本和文档冗余

§3 只列了 `legacy/`、`build/`、`dist/`。本轮脚本/打包 subagent 还发现一批已被 `.gitignore`
忽略、但会影响 grep/磁盘体积/复盘判断的本地产物。

| 目录/文件 | 当前体积/状态 | 去除方式 | 风险 |
|---|---:|---|---|
| `live-downloads/` | 632MB，`.gitignore:31`；最大子目录 `golden-criteria-review/` 约 559MB | 用 `scripts/clean-local-artifacts.sh --days N` 或手动清理；重要 report/manifest 先迁入正式 fixture | 可能丢 live review 证据 |
| `.paper-fetch-runs/` | 102MB，`.gitignore:33` | 同上；重要样本迁入 fixture 后删 | 可能丢迁移调试现场 |
| `.paper-fetch/` | 23MB，`.gitignore:32`，清理脚本默认未覆盖 | 加入清理脚本 safe target，或手动 `scripts/clean-local-artifacts.sh .paper-fetch --dry-run` 后清理 | 会清本地 MCP/CLI cache，首次运行需重抓 |
| `.formula-tools/` | 50MB，`.gitignore:5` | 不放默认 safe 清理；作为 explicit/heavy target 或重装前手动删 | 删除后公式转换可能降级或需重装 |
| `node_modules/` | 5.7MB，`.gitignore:2` | explicit/heavy target；不要默认删 | 删除后 Node fallback/校验需重新 `npm install` |
| `src/**/__pycache__`、`tests/**/__pycache__`、`scripts/**/__pycache__` | 32 个目录、482 个 `.pyc`，合计约 11MB | 加入清理脚本 safe target（`find ... -name __pycache__ -prune -exec rm -rf {}`） | 低，下次 Python 会重编译 |
| `src/paper_fetch_skill.egg-info/`、`.pytest_cache/`、`.ruff_cache/` | 分别约 48KB、180KB、152KB，均 ignored | safe target | 低 |
| `figures/.paper-fetch-mcp-cache.json` | 被 `.gitignore:34` 忽略 | 可删；不要清整个 `figures/`，README 仍引用示例图 | 低 |

脚本/文档层的冗余候选：

- `scripts/clean-local-artifacts.sh` 可升级成统一 ignored 本地产物清理入口：默认 safe targets
  包含 `.paper-fetch`、`__pycache__`、`*.egg-info`、pytest/ruff cache；`.formula-tools` 和 `node_modules`
  放 explicit/heavy target。
- `install.sh` 与 `scripts/dev-bootstrap.sh` 都创建 venv、升级 pip、安装包、复制 env 并调用
  `install-formula-tools.sh`。保留“用户安装入口”和“开发 bootstrap 入口”是合理的；若要去重，
  可让 `dev-bootstrap.sh` 调用 `install.sh --editable` 后再安装 dev extras，但需谨慎保护
  `--lite`、`--system` 和 dev extras 语义。
- `installer/manifest.json` 已声明 managed block、skill/MCP 名和 env keys，但
  `install-offline.sh`、`install-offline.ps1`、`scripts/windows-installer-helper.ps1` 仍保留同值 fallback；
  `installer/paper-fetch-skill.iss` 还硬编码 `AppVersion "1.4.1"`。建议正常 install/build 强制读
  manifest，仅 uninstall 保留 manifest 缺失 fallback；`.iss` 版本/文件名由 build 脚本传参或生成。
- `scripts/build-offline-package.sh` 与 `scripts/build-offline-package-windows.ps1` 的 snapshot、wheelhouse、
  formula tools、offline README、manifest/checksum 逻辑重复。不要强行跨 shell/PowerShell 共用执行逻辑；
  更稳的是把共享规范落到 `installer/manifest.json` 或小型 manifest generator。
- `docs/adding-a-provider.md` 与 `docs/provider-development.md` 有快速版/完整版重叠，但前者是人类 quickstart，
  且 README 与 drift 测试仍引用；低置信归档候选，当前不建议直接删。
- `docs/ai-onboarding/operator-prompts.md` 与 coordinator/brief/schema 文档重复部分操作规则；若未来
  `scripts/onboard_from_manifests.py` 可生成 prompts，可把它归档为历史模板。

## 12. 第二轮 Subagent：provider 重复实现

第二轮 provider subagent 重点扫了 `providers/` 的活跃路径。这里的多数项不是死代码，而是相同
orchestration / hook 形状在多个 provider 中重复维护；删除方式应以抽共享 hook、保留 provider
差异配置为主。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| PNAS / Science `extract_asset_html_scopes` 重复 | `providers/_pnas_html.py:87`、`providers/_science_html.py:249`、共享 helper `providers/atypon_browser_workflow/asset_scopes.py:112` | 两个 provider 都遍历 supplementary sections、从 body 移除、拼接 supplementary HTML，再返回 `content_fragment_html(...)` | 复用 Atypon shared helper，或把“supplementary section selector + body container”做成 profile strategy；风险低到中，需回放 asset golden |
| `scoped_asset_extractor` 入口形状重复 | `providers/_pnas_html.py:167`、`providers/_science_html.py:314`、`providers/_ams_html.py:906`、`providers/_wiley_html.py:589` | PNAS/Science 动态导入后直接转发共享 extractor，Wiley 基本 alias，AMS 只加 table asset 特例 | 在 Atypon profile 中提供默认 `scoped_asset_extractor`，只有 AMS/Wiley 覆写差异 hook；风险中 |
| Wiley 重写 `fetch_raw_fulltext` orchestration | `providers/browser_workflow/client.py:209`、`:249`，`providers/wiley.py:236`、`:271`、`:310`、`:450` | 基类已处理 bootstrap HTML、警告、seeded browser PDF、waterfall；Wiley 为插入 TDM API fallback 复制整段流程 | 把基类拆成 `pdf_fallback_steps()`、`final_fallback_failure()`、`html_failure_warning()` 等 hook；Wiley 只追加 `pdf_api` 步骤；风险中到高 |
| Springer HTML 两套并行模块 | `providers/html_springer_nature.py:423`、`:427`，`providers/_springer_html.py:492`、`:520`、`:571` | 两边都选择 article/main root、剥 chrome、处理 Nature abstract/body/back matter，且 `_springer_html` 还 fallback 到 `html_springer_nature` | 把 root 选择、Nature abstract/body 规则集中到 `html_springer_nature`；`_springer_html` 只负责 provider payload、metadata、assets 编排；风险高 |
| 空壳 Atypon client 类 | `providers/science.py:80`、`:86`，`providers/pnas.py:78`、`:85`，基类 `browser_workflow/client.py:55`，registry `providers/registry.py:29` | `ScienceClient` / `PnasClient` 只设置 `name` / `profile` 后继承 `BrowserWorkflowClient` | 引入 `make_browser_workflow_client(profile)` 或 profile catalog factory；先保留 alias class 兼容外部导入；风险中 |
| provider `__pycache__` 残留旧模块名 | `.gitignore:10`，`src/paper_fetch/providers/__pycache__/` | 本地 pyc 里仍有已删除旧名，例如 `_flaresolverr`、`springer_html` 等，会污染 grep/扫描判断 | 本地清 `__pycache__`；静态扫描默认排除 `**/__pycache__/**`；风险低 |

第二轮还用 AST 做了“函数体完全重复/近完全重复”抽查，新增这些可收敛点：

| 重复块 | 位置 | 去除方式 / 风险 |
|---|---|---|
| `_provider_failure_diagnostics` | `providers/_ieee_browser_html.py:50`、`providers/ieee.py:101` | 抽成 IEEE shared diagnostic helper，两个入口共用；风险低到中 |
| `_pdf_headers` | `providers/copernicus.py:273`、`providers/arxiv.py:173` | 抽到 provider shared PDF header helper；风险低 |
| `finalize_extraction` | `providers/_pnas_html.py:148`、`providers/_wiley_html.py:434` | 复用 Atypon finalizer 或做 profile postprocess hook；风险中，需保护 metadata/body/asset golden |
| `validate_query` | `mcp/schemas.py:137`、`:245` | 合并成单一 validator 或 schema factory；风险低 |

## 13. 第二轮 Subagent：公共层 helper / 数据结构重复

这一组横跨 `models/`、`workflow/`、`extraction/` 和 `quality/`。它们不是迁移残留，
但会让同一个概念在不同层里以不同名字、不同边界条件演化。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| Markdown 图片引用候选逻辑重复 | `models/render.py:305`、`:364`，`workflow/rendering.py:87`、`:176` | 两边都 normalize/strip `< >`、`urlsplit`/`unquote`、反斜杠转 `/`、折叠 `//`、去 `./` 和前导 `/`；workflow 额外处理相对路径和普通 link | 把 URL/reference candidate、basename、match builder 抽到 `models.markdown` 或 `markdown/assets.py`；风险中 |
| Markdown table 渲染 helper 分裂 | `extraction/markdown_render/tables.py:12`、`:32`，`extraction/html/tables.py:286`、`:294` | XML/JATS/Elsevier 用未对齐手写 renderer，HTML/Atypon 用另一套对齐 renderer | 让 `markdown_render.tables` 提供纯 cell escape + aligned renderer；`html.tables` 只负责 DOM -> matrix；风险中，golden spacing 会变 |
| Section hint dict/typed 转换重复 | `extraction/section_hints.py:27`、`:56`，`models/sections.py:222`、`:238`，`extraction/html/semantics.py:58` | 公共 helper 已能按 dict match，model 层又把 dict -> `SectionHint` -> dict 再调用 | 公共 helper 接收 typed `SectionHint` 或提供 factory；删除 model 私有 wrappers；风险低到中 |
| Body metrics 字段重复 | `models/schema.py:130`，`extraction/html/_runtime.py:694`，`models/quality.py:110`、`:230`，`quality/html_availability.py:130` | `BodyQualityMetrics`、`body_metrics()` dict、`coerce_body_quality_metrics()` 和 diagnostics key 多处维护同一字段集 | 引入 `BodyMetricsSnapshot` / 字段 mapping / coercer；先保持外部 payload shape；风险中 |
| provider key normalization 重复 | `quality/html_signals.py:200`，`extraction/html/provider_rules.py:440`，`workflow/types.py:106`，`workflow/routing.py:53` | `_normalize_provider_signal_key` 与 `_normalize_rule_key` 都做 strip/lower/hyphen->underscore/split-join，workflow preferred provider 又只 lower | 抽 `normalize_provider_key` / `normalize_profile_key`，补 `springer-nature`、`springer_nature` alias 测试；风险中 |
| image payload helper 通过 HTML shared alias 再转发 | `extraction/image_payloads.py:41`、`:53`，`extraction/html/shared.py:53`，`extraction/html/assets/dom.py:72`，`extraction/html/assets/_kind.py:172` | `image_magic_type` / `_image_dimensions` 已有 public helper，但活跃代码仍经 `extraction.html.shared` 私有 alias 导入 | 内部改直接导入 `extraction.image_payloads`；如需兼容，`html.shared` 只保留 re-export；风险低到中 |

## 14. 第二轮 Subagent：fixture / figures / references 资源冗余

fixture subagent 的结论是：不能简单按目录名或 hash 删除资源。部分“看似冗余”的文件其实是
测试隐式依赖，真正问题是 manifest 与资源目录之间缺少单一事实源。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| `body_assets` 不在 manifest 但被测试使用 | `tests/fixtures/golden_criteria/10.1029_2004gb002273/body_assets`、`tests/fixtures/golden_criteria/10.1126_sciadv.adl6155/body_assets`，manifest `:250`、`:778` | manifest 只列 `article.json` / `original.html`；测试通过目录 basename 找 asset（`tests/unit/_atypon_browser_workflow_provider_support.py:53`、`:60`，`test_atypon_browser_workflow_provider_html.py:302`、`:349`） | 不要删除；给 manifest 增加 `assets` glob 或 `asset_directory` 字段；风险高 |
| 两组 `expected.json` 完全重复 | `10.1038_s41467-022-30729-2` == `10.1038_s41561-022-00974-7`；`10.1038_s41612-021-00218-2` == `10.1038_s43247-024-01295-w` | 四个 fixture 均在 manifest 中登记（约 `:338`、`:1212`、`:1260`、`:1276`） | 不建议直接删；若要去重，可在 manifest 支持 shared expectation preset；风险中 |
| `figures/` raw provenance 与 README 展示产物混放 | `figures/10.1038_s41586-026-10265-5.fetch-envelope.json`、`figures/10.1038_s41586-026-10265-5_original.html`、`figures/10.1126_sciadv.adp3964.fetch-envelope.json` | README 只引用 PNG/Markdown 示例；Markdown 再引用 asset images，raw envelope/original HTML 不参与展示 | 移到 `docs/archive/figures-provenance/` 或删除并声明 figures 只保留展示产物；风险低到中，会丢审计证据 |
| `references/journal_lists.yaml` 是旧参考表 | 文件头已说明 runtime 不加载；当前 routing 事实源在 `publisher_identity.py` | 全仓只被自身和 references 定位文档提及；不像 `elsevier_markdown_mapping.md` 被 extraction rules 链接 | 移到 `docs/archive/references/journal_lists.yaml` 或删除并更新 references 定位说明；风险低 |
| `src/paper_fetch/resources/**/__pycache__` | ignored 本地产物 | 与 §11 相同，会污染资源扫描和旧模块判断 | 加入清理脚本 safe target；风险低 |

已复核但暂不列为清理项：`docs/ai-onboarding/manifests/{arxiv,copernicus,wiley}.yml`
被 `known-providers.yml` 索引并有测试覆盖；tracked `src/paper_fetch/resources` 仍有运行时用途；
公式资源副本已在 §9 单独列为“需选单一事实源”。

## 15. 第二轮 Subagent：CLI / CI / devtools 入口冗余

这一组偏“漂移”而非纯死代码：多个入口长期重复维护，结果出现了 stale import、no-op 参数和
文档清单落后于真实 provider catalog。优先修会减少后续重构误报。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| Windows CI smoke import 已过期 | `.github/workflows/ci.yml:333` | CI 里仍写 `from paper_fetch.mcp.tools import provider_status_payload`；仓库没有 `src/paper_fetch/mcp/tools.py`，实际函数在 `src/paper_fetch/mcp/fetch_tool.py:335` | 改 import，并加 workflow 文本测试防回归；风险高，离线 Windows job 会失败 |
| `--skip-playwright-install` 是 no-op 兼容参数 | `install.sh:34`、`:62`，`install-formula-tools.sh:15-24`，`src/paper_fetch/formula/install.py:198-205`，`docs/deployment.md:40`、`:243` | shell installer 转发该参数，formula shell 又丢弃；Python formula installer 只支持 `--target-dir` / `--no-node` | 删除参数和文档，或保留但打印 deprecated/no-op warning 并加测试；风险中 |
| Codex MCP wrapper 仍设置 Playwright 浏览器路径 | `scripts/run-codex-paper-fetch-mcp.sh:49-50`，安装入口 `scripts/install-codex-skill.sh:21-24` | 当前迁移后依赖 `CLOAKBROWSER_HEADLESS` / `CLOAKBROWSER_BINARY_PATH`；测试/文档已要求 offline install 不再传播 Playwright 路径 | 删除 `PLAYWRIGHT_BROWSERS_PATH` 分支并补文本测试；风险中 |
| MCP/Skill provider status 文档落后 | `skills/paper-fetch-skill/references/tool-contract.md:11` | 手写清单只列 crossref/elsevier/springer/wiley/science/pnas/ieee；实际 `provider_status_payload` 走 `provider_status_order()`，已包含 arxiv/copernicus/ams | 从 `provider_catalog` 生成或删除手写 provider 列表；风险低到中 |
| CLI 参数文档多处重复 | `src/paper_fetch/cli.py:445-517`，`README.md:231-243`，`docs/cli.md:13-36`，`skills/paper-fetch-skill/references/cli-fallback.md:17-30` | CLI parser、README、docs、skill fallback 四处维护选项摘要 | 以 `docs/cli.md` 为 canonical，README/skill 只留最小入口或生成 snapshot；风险低 |
| `scripts/fulltext_links.py` 未接入口 | `scripts/fulltext_links.py:14` | 定义 `download_from_fulltext_links`，无 shebang、无 CLI main，全仓无调用方 | 若已被 `_pdf_candidates.py` / workflow metadata routing 取代则删除；否则移入 `src/` 并接测试；风险低到中 |
| formula benchmark / validator 孤立 | `scripts/benchmark_formula_converters.py:14-20`、`:133-160`，`scripts/validate_latex_cli.mjs:1-20` | benchmark 无 docs/CI 引用；validator 只被 benchmark 调用，且 benchmark 依赖 §6 已标死的 `collect_formula_samples` | 删除 benchmark 与 benchmark-only helper，或迁到 `paper_fetch_devtools` 并补 docs/smoke；风险低 |
| devtools 双入口 | `pyproject.toml:50-53`，`scripts/run_golden_criteria_live_review.py:9-18`，`src/paper_fetch_devtools/golden_criteria/cli.py:32-70`，`scripts/run_geography_live_report.py:46-83` | `paper_fetch_devtools*` 被 wheel 排除；部分 scripts 只是 shim package CLI，geography 又保留一套自己的 parser | 选单一策略：scripts-only，或 package devtools CLI + 超薄 shim；风险中 |

## 16. 第三轮 Subagent：split-module 聚合层 / compat 垫片

第三轮重点复核了 split-module 迁移后留下的“聚合层”。这些层大多不是业务逻辑，
但会让测试和生产代码继续依赖私有旧路径，阻碍真正收口。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| `models/_core.py` 是 `models/__init__.py` 副本 | `models/_core.py:1`、`models/__init__.py:1` | 两文件除 docstring 外 29 行完全相同；全仓无 `paper_fetch.models._core` 引用 | 直接删除 `_core.py`；若担心包外私有导入，先保留 deprecation shim；风险低到中 |
| `extraction/html/assets/_core.py` 仅测试白盒引用 | `extraction/html/assets/_core.py:1`、`tests/unit/test_provider_request_options.py:15` | `_core.py` 自称 split helper compatibility aggregator；生产代码走 `extraction.html.assets` 公共入口，只有测试以私有 `_core` 导入 | 测试改 patch `assets.download` 或具体 split module 后删除 `_core.py`；风险低到中 |
| `atypon_browser_workflow/_core.py` 未接入公共入口 | `providers/atypon_browser_workflow/_core.py:1`、`providers/atypon_browser_workflow/__init__.py:13` | `_core.py` 聚合 profile/normalization/asset/postprocess/markdown 的 `__all__`，但 package `__init__` 只导出少量公共函数，全仓无 `_core` 导入 | 删除 `_core.py`，或把确需公开的函数显式放入 `__init__`；风险低到中 |
| `ProviderWaterfallStep` 纯别名 | `providers/_waterfall.py:52`、`:63` | `ProviderWaterfallStep = WaterfallStep`；文档和 base typing 已使用 `WaterfallStep`，但多 provider 仍导入别名 | 机械替换 provider/tests 为 `WaterfallStep`，再删别名；风险中，外部私有 API 可能受影响 |
| `quality/html_profiles.py` 旧 facade | `quality/html_profiles.py:7`、`:13`、`quality/html_availability.py:46`、`providers/_atypon_browser_workflow_profiles.py:20` | facade 主要 re-export `extraction.html.provider_rules` 与 `quality.html_signals`；生产代码仍经 facade 取 `site_rule_for_publisher` / signals | 内部调用改直接导入真实模块；若保留包外短路径，`html_profiles` 仅做 deprecation re-export；风险中 |

## 17. 第三轮 Subagent：源码级重复小工具

本轮本地 AST 扫描发现多组函数体完全重复或近完全重复。它们多数不该直接删除，而应抽到
provider-neutral helper，避免后续修一个边界条件时漏另一处。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| 默认 HTML 请求头重复 | `providers/arxiv.py:161`、`providers/copernicus.py:258`、`providers/ieee.py:138`、`providers/springer.py:554` | 四处都返回 `Accept: text/html,application/xhtml+xml` + `User-Agent` | 抽 `default_html_headers(user_agent)` 或 base protected helper；风险低 |
| cookie header 匹配逻辑重复 | `extraction/html/assets/requester.py:15`、`providers/_pdf_fallback.py:201` | 两边完整复制 browser cookie 的 domain/path/secure 匹配，再拼 `Cookie` header | 移到 `http/cookies.py` 或 provider-neutral requester helper；风险低到中，需保护 domain/path/secure 行为 |
| XML local-name helper 重复 | `extraction/html/language.py:448`、`providers/_article_markdown_xml.py:11`、`providers/elsevier.py:132` | 三处都是 `tag.rsplit("}", 1)[-1] if "}" in tag else tag` | 抽到 `extraction/xml.py` 或 `utils/xml.py`；注意 provider 与 extraction 的导入边界；风险低 |
| `srcset` 最佳 URL 解析重复 | `extraction/html/formula_rules.py:74`、`extraction/html/assets/dom.py:118` | 两处逐项解析 `w`/`x` descriptor 并按 score 取最大 URL | 抽到 HTML media helper，公式和资产 DOM 共用；风险低到中 |
| Playwright/CloakBrowser response headers/status adapter 重复 | `providers/_cloakbrowser.py:228`、`:237`，`providers/browser_workflow/html_extraction.py:148`、`:157` | 两处都兼容 `response.all_headers()` / `.headers` 和 `response.status` | 抽 `browser_response_headers/status` 到 browser runtime/shared helper；风险低 |
| retryable asset failure 判断重复 | `providers/_arxiv_assets.py:67`、`providers/_ieee_html.py:74`、`providers/springer.py:200`、共享常量 `providers/_retry_categories.py:7` | 三处都先看 `status`，再看 `error_category`，最后匹配 network reason tokens | 在 `_retry_categories.py` 或新 `_asset_retry.py` 提供 `is_retryable_asset_failure(...)`；风险低到中 |
| reference DOI match 重复 | `providers/_arxiv_references.py:54`、`providers/_ieee_metadata.py:258` | 两处都基于 `DOI_PATTERN`，并要求 DOI 前一字符非字母数字 | 放到 `publisher_identity` / `common_patterns`；风险低 |
| inline body figure/table asset filter 完全重复 | `models/render.py:393`、`:419` | `filter_inline_body_figure_assets` 与 `filter_inline_body_table_assets` 函数体完全一致 | 合并为 `filter_inline_body_assets`，旧函数作薄 wrapper 或直接改调用方；风险低到中 |
| section hint kind -> availability category 重复 | `quality/html_availability.py:602`、`extraction/html/semantics.py:462` | 两处同样把 `references` 映射为 `references_or_back_matter`，其余 data/code availability 直通 | 只保留 `semantics.category_for_section_hint_kind`，quality 层导入它；风险低 |
| `should_download_related_assets_for_result` 逻辑重复 | `providers/browser_workflow/client.py:355`、`providers/springer.py:1246` | 两处都只在无 provisional article 或 `content_kind == FULLTEXT` 时下载资产 | 抽到 base helper / 策略 flag；风险中，需保护 abstract-only fallback 的资产行为 |
| 小型路径 normalizer 重复 | `config.py:54`、`formula/paths.py:19` | 两处都把空值转 `None`，非空 `Path(...).expanduser()` | 抽 `normalize_optional_path` 到通用 utils，或接受为低价值重复；风险低 |

## 18. 第三轮 Subagent：MCP / workflow schema 与状态双维护

这一组不是“可立即删”的死代码，而是同一协议 shape、cache decoder 或 metadata 行为在多个层级
各自维护。建议先增加 schema/decoder 快照测试，再做收敛。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| MCP output TypedDict 镜像模型字段 | `mcp/output_schemas.py:46`、`:158`，`models/schema.py:54`、`:319` | `MetadataOutput` / `ArticleOutput` / `FetchPaperOutput` 手写镜像 `Metadata`、`ArticleModel`、`FetchEnvelope.to_dict()` 字段 | 由模型层导出 JSON schema / TypedDict adapter，或用单一 schema factory 生成 MCP output annotation；风险中 |
| MCP cache decoder 重复模型构造逻辑 | `mcp/fetch_cache.py:90`、`:141`、`:165`，`models/builders.py:117` | `metadata_from_payload`、`quality_from_payload`、`article_from_payload` 手写重建 Metadata/Quality/Section/Reference/Asset；字段清单和 coercion 又在 model builders 维护 | 在 models 层增加 `from_mapping` / decoder；MCP cache 只处理 cache version、request 匹配与兼容旧 sidecar；风险中 |
| MCP allowed sets 重复 Literal / runtime 常量 | `mcp/schemas.py:17`、`:18`、`:19`，`models/schema.py:32`、`:35`，`artifacts.py:26` | `ALLOWED_OUTPUT_MODES`、`ALLOWED_ASSET_PROFILES`、`ALLOWED_ARTIFACT_MODES` 手写重复 Literal 值 | 用 `typing.get_args()` 派生或提供 canonical allowed-value tuples；风险低到中，错误文案排序可能变 |
| 单值 list coercer 重复 | `mcp/schemas.py:82`、`:173` | `coerce_authors` 与 `coerce_preferred_providers` 都是 `None` 直通、字符串转单元素 list | 抽 `_coerce_optional_string_list`；风险低 |
| `no_download` 到 runtime 配置转换重复 | `workflow/request_builder.py:43`、`workflow/pipeline.py:69`、`:74` | request builder 已把 `artifact_mode` 改成 `"none"`，pipeline 又根据 `request.no_download` 重新计算 artifact mode 与 `download_dir` | 只在 `FetchPipeline.runtime_context()` 解释 `no_download`，builder 保持纯组装；风险低 |
| metadata merge 有两套实现 | `workflow/metadata.py:29`，`metadata/types.py:69`、`:118` | 已有 `MetadataMergeRule` / `merge_metadata_layers()`，workflow 仍手写 scalar/list merge、作者去重和 landing URL 优先级 | 用 `merge_metadata_layers()` 表达 primary/secondary 规则，scalarize / public landing URL 作为扩展 hook；风险中 |
| metadata fetch 与 fulltext probe 编排重复 | `workflow/metadata.py:170`、`workflow/routing.py:283` | 两边都并发 Crossref、initial provider metadata probes，再按 Crossref/routing metadata 补 provider probe 顺序 | 抽 `run_metadata_probe_bundle()` 返回 crossref result/error、provider probe results/errors、候选顺序；风险中到高 |
| 未用 asset-profile 派生常量 | `workflow/types.py:17`、`:20` | `HTML_BODY_ASSET_DEFAULT_PROVIDERS` / `HTML_BODY_ASSET_DEFAULT_SOURCES` 全仓只命中定义本身 | 直接删除，或改为测试内局部计算；风险低 |

## 19. 第三轮 Subagent：测试侧新增冗余

§10 已列过一批测试 helper。第三轮避开那些条目后，又发现以下重复支撑代码。
这些不会影响生产包，但会增加接口改名和 fixture 迁移成本。

| 项目 | 位置 | 去除方式 | 风险 |
|---|---|---|---|
| Service 测试重复 Elsevier/Crossref 数据 builder | `test_service_official_pipeline.py:24`、`:44`，`test_service_metadata_routing.py:347`，`test_service_pdf_and_provider_fallbacks.py:22` | 在 `_service_support.py` 增加 `elsevier_official_clients(...)` / `crossref_metadata(...)` builder，允许覆盖 metadata/raw payload/error | 中 |
| `RecordCaptureHandler` 重复 | `_service_support.py:37`、`test_http_cache.py:77` | 抽到 `_logging_support.py`，避免 HTTP 测试反向依赖 service helper | 低 |
| provider `_response(...)` 工厂重复 | `test_arxiv_provider.py:88`、`test_copernicus_provider.py:117` | 放到 `_paper_fetch_support.py` 或 `_http_support.py` 作为 `http_response(...)` | 低 |
| CLI `fake_fetch` stub 重复 | `test_cli.py:87`、`:133`、`:340`、`:806`、`:927` | 抽 `_fake_fetch(article, captured=None, use_render=False)` 或 patch context manager；保持每个测试独立 captured dict | 低到中 |
| golden fixture path/slug 手写绕过 helper | `tests/golden_criteria.py:15`、`:35` 已有 helper；`test_arxiv_provider.py:56`、`:68`，`_ieee_provider_support.py:200`，`test_ieee_provider_pdf_golden.py:81` 仍手写路径 / `replace("/", "_")` | 改用 `golden_criteria_asset()` / `golden_criteria_dir_for_doi()`；IEEE 样本 tuple 去掉 `fixture_dir` 字段 | 中 |
| HTML semantics fixture case 重复 | `test_html_semantics.py:38`、`:49`、`:115`、`:125` | 提升 `BACK_MATTER_HEADING_FIXTURES` / `ANCILLARY_HEADING_FIXTURES` 为文件级常量供两组 taxonomy 测试共用 | 低 |
| extraction rules validator 单测仍用旧 Springer 路径 | `test_extraction_rules_validator.py:227`，真实测试在 `test_springer_html_regressions.py:545`、`:583` | 更新 synthetic `TestDefinition.path` 和测试名，避免 validator 单测固化过期路径 | 低 |
| `--skip-playwright-install` no-op 被测试固化 | `tests/integration/test_skill_template.py:203`、`:263` | 若按 §15 删除参数，测试改断言不再出现；若保留 deprecated 行为，测试应断言 warning 而非正常功能 | 中 |
| installer `_write_executable` 重复 | `test_offline_install.py:20`、`test_skill_installers.py:15` | 抽到 `tests/unit/_installer_support.py` | 低 |
| `test_http_cache.py` 内部 fake_urlopen 重复 | `test_http_cache.py:590`、`:608`、`:753`、`:786` | 抽局部 helper（gzip response factory、rate-limit-then-ok opener） | 低 |
| browser asset Cloudflare failure side effect 重复 | `test_provider_request_options.py:1524`、`test_atypon_browser_workflow_provider_asset_failures.py:166` | 抽 `record_cloudflare_failure(fetcher, current_url)` 到 provider workflow test helper | 低到中 |
| `test_service_runtime.py` timing provider 双实现 | `test_service_runtime.py:266`、`:277`、`:319`、`:330` | 抽 tiny provider factory，参数化 raw/fulltext branch 和 expected timing | 低 |

## 20. 第三轮 Subagent：文档 / 脚本 / installer 漂移

这一组与 §15 类似，属于重复维护造成的漂移或即将漂移。多数不需要马上删代码，
但建议改成由 manifest、schema 或 provider catalog 生成。

| 项目 | 位置 | 证据 | 去除方式 / 风险 |
|---|---|---|---|
| `BROWSER_RUNTIME_REQUIRED` signal 文档重复 | `docs/ai-onboarding/failure-recovery.md:77`、`:83`，`scripts/capture_fixture.py:473` | 文档定义两次同 code，分别写 browser 与 playwright/browser；脚本实际只发一个 code | 合并为一个 signal，runtime 类型放 `details.route`；风险低到中 |
| extraction rules validator 漏 AMS section | `scripts/validate_extraction_rules.py:25-33`、`docs/extraction-rules.md:1374` | validator `PROVIDER_SECTIONS` 不含 `AMS`，但文档已有 `## AMS`，同脚本 requirements 又含 `ams` | 从文档 heading 或 provider catalog 派生 provider section；至少补 AMS；风险中 |
| Windows offline CI 仍 smoke Playwright browser | `.github/workflows/ci.yml:337-352`、`scripts/windows-installer-helper.ps1:474-496`、`docs/deployment.md:166` | CI 仍用 `playwright.sync_api` 检查 `ms-playwright`；helper/docs 已有 CloakBrowser package smoke | 删除 Playwright browser smoke，或改成“不得打包 browser binary”检查；风险中 |
| Skill provider notes 漏 AMS | `skills/paper-fetch-skill/references/tool-contract.md:50-53`、`skills/paper-fetch-skill/SKILL.md:25`、`:69`、`docs/providers.md:32` | Skill 主文档已把 AMS 归入 browser runtime provider，provider 矩阵也说 AMS 支持 body/all assets，但 tool contract 只列 Wiley/Science/PNAS | provider notes 从 catalog/docs 摘要生成，或补 AMS 并减少手写列表；风险低到中 |
| 默认 User-Agent 版本文档漂移 | `skills/paper-fetch-skill/references/environment.md:4`、`src/paper_fetch/config.py:21`、`pyproject.toml:7`、`docs/deployment.md:61-66` | skill 环境文档写 `paper-fetch-skill/1.4`，代码默认是 `1.4.1` | 文档不写具体版本，只指向 `DEFAULT_USER_AGENT`，或生成片段；风险低 |
| 多宿主 skill installer 逻辑重复 | `scripts/install-codex-skill.sh:18-40`、`install-claude-skill.sh:19-37`、`install-gemini-skill.sh:19-41`、`scripts/windows-installer-helper.ps1:376-450` | 三个 shell installer 和 Windows helper 都重复“找 CLI、remove、组 env、add MCP server”，只是 flag 名不同 | 抽 host descriptor：CLI 名、env flag、scope flag、server command；shell/PowerShell 只保留薄适配层；风险中 |
| Codex `openai.yaml` 元数据硬编码 | `scripts/_skill_install_common.sh:122-129`、`installer/manifest.json:4-9`、`scripts/build-offline-package-windows.ps1:300-311` | display/description/prompt 在 common shell 硬编码；Windows build 已从 manifest 读取同一数据 | shell installer 也读 manifest，或把 agent manifest 作为静态源随 skill 复制；风险低到中 |
| onboarding manifest schema / brief / script 常量重复 | `scripts/onboard_from_manifests.py:33-67`、`:216-260`，`docs/ai-onboarding/provider-manifest.schema.json:169-180`，`agent-task-brief.md:20-62`，`manifest-discovery.md:97-110` | routing keys、DOI purpose 集合、forbidden files 同时存在于脚本常量、schema required、brief 示例和 discovery 文档 | schema 做事实源，脚本读取 schema defs，文档示例生成或快照校验；风险中 |
| capture fixture error payload 重写 structured error | `scripts/capture_fixture.py:86-140`、`scripts/_structured_errors.py:9-46` | `CaptureFixtureError` 重写 code/message/retryable/details/extras 到 JSON payload 的结构 | 让 capture route 错误继承或包装 `ToolError`，只保留 capture-specific details builder；风险低到中 |
| known-providers index 重复 manifest 字段 | `docs/ai-onboarding/known-providers.yml:14-17`、`:30-37`，`docs/ai-onboarding/manifests/{wiley,arxiv,copernicus}.yml:2-3` | manifest-backed provider 在 index 里重复维护 `name` / `display_source`，manifest 本身也有同值 | index 只保留 `manifest_path` 和外部状态；name/display_source 由 manifest 读取或生成校验快照；风险低 |
| fixture manifest loader 重复 | `scripts/capture_fixture.py:155`、`scripts/snapshot_expected.py:47` | 两处 `_load_manifest` 完全相同：不存在则 `{"samples": {}}`，校验 root/samples dict | 抽到 `scripts/fixture_manifest.py` 或 devtools utility；风险低 |

## 清理建议（按优先级）

1. **立即可做**：删除 §1 全部 6 项（`runtime_playwright.py`、`test_runtime_playwright.py`、
   `_html_structure.py`、`html_extraction.py` 的 3 个 `_DIRECT_PLAYWRIGHT_*` 别名）。
   零消费者，删除后测试不受影响。
2. **删除低风险 compat 副本**：§16 的 `models/_core.py`、`atypon_browser_workflow/_core.py`
   先删或 deprecate；`extraction/html/assets/_core.py` 需先把唯一测试改到公开/具体模块入口。
3. **先修显性漂移**：修 §15 的 Windows CI stale import（`paper_fetch.mcp.tools` → 真实入口），
   并给 workflow 文本加回归测试；同时处理 §20 的 AMS extraction-rule validator 漏检和
   Windows Playwright smoke 残留，避免 CI 继续固化旧运行时。
4. **删除死代码**：移除 §6 的 32 个未用函数/类（约 312 行）与 §7 的 9 个类内方法/property
   （约 32 行），逐项快速复核后即可删；`workflow/fulltext.py`、`extraction/html/language.py`
   的连续死函数、`CrossrefClient` 的 3 个私有包装方法可整段移除；§18 的未用
   `HTML_BODY_ASSET_DEFAULT_*` 常量可并入这一批。
5. **小重构**：§2 把 3 个测试改用新名，移除 `_LEGACY_DEP_ALIASES` / `direct_playwright` 别名；
   `_BasePlaywrightDocumentFetcher` 连同 `context.py` 改名。
6. **兼容 facade 收口**：优先删除 §8 中仓库内零引用的 `build_provider_registry` 动态注入；
   然后迁移 MCP 同步 wrapper 测试到 async 入口，删除同步 `fetch_paper_tool` / batch tool facade；
   §16 的 `ProviderWaterfallStep` 与 `quality/html_profiles.py` 也按“内部先改真实入口，外部再 deprecate”
   的节奏处理。
7. **provider 重复实现收口**：先做 §12 低风险项（PNAS/Science asset scopes、`_pdf_headers`、
   IEEE diagnostics、MCP `validate_query`）和 §17 的低风险工具块（默认 headers、XML local-name、
   reference DOI、section hint category）；Wiley orchestration、Springer HTML 双模块和 asset 下载策略
   要等 golden 回放齐全后再拆。
8. **公共 helper 收敛**：按 §13 抽 provider key normalization、image reference candidate、table
   renderer、body metrics snapshot；并按 §17 抽 cookie header、srcset、browser response adapter、
   retryable asset failure。每项都要保留当前 payload/Markdown 输出兼容测试。
9. **MCP/workflow schema 收敛**：§18 先派生 allowed-value sets、抽 list coercer 与 `no_download`
   单一解释点；output TypedDict、cache decoder、metadata merge/probe 编排属于中风险，先补 schema/cache
   快照测试再改。
10. **运行时命名收口**：删除 §1 的 `runtime_playwright.py` 后，继续清理 §8 的
   `runtime_browser` Playwright 旧名；先把 `_ieee_browser_html.py` 改用 `new_browser_context`。
11. **活跃旧命名重命名**：§9 把 `direct_playwright_html_preflight` 改成
   `fast_browser_html_preflight`，同步 bootstrap 日志、PNAS profile、测试和 provider 文档。
12. **安装/脚本参数收口**：§15 的 `--skip-playwright-install` 要么删除，要么明确 deprecated/no-op；
    `scripts/run-codex-paper-fetch-mcp.sh` 去掉 `PLAYWRIGHT_BROWSERS_PATH` 旧分支；同步 §19 中固化
    no-op 参数的测试。
13. **测试去重**：按 §10 先抽低风险 helper（`load_yaml`、`_run_scaffold`、`FakeResponse`、
   `_extract_fixture_markdown`），再按 §19 抽 logging handler、provider response、CLI fake fetch、
   installer executable helper、golden fixture path helper；provider workflow/regression stub 这类中风险重复
   最后处理。
14. **fixture/资源 manifest 化**：§14 的 `body_assets` 先补 manifest，再考虑资源去重；
    重复 `expected.json` 用 shared preset，不直接删目录；`journal_lists.yaml` 和 figures provenance
    可归档。
15. **脚本/文档单一事实源**：§20 的 skill provider notes、默认 UA、Codex `openai.yaml`、
    onboarding manifest schema/brief/script、known-providers index，统一从 catalog/schema/manifest 生成或校验；
    `capture_fixture` structured error 和 fixture manifest loader 可抽共享脚本 helper。
16. **本地清理**：`rm -rf legacy/ build/ dist/`（§3）；再按 §11 清理 `live-downloads/`、
   `.paper-fetch-runs/`、`.paper-fetch/`、`__pycache__`、cache/egg-info。`.formula-tools/` 与
   `node_modules/` 只作为显式 heavy target。
17. **环境**：`pip install -e .` 修复 §5 的测试收集错误。
18. **迁移收尾后**：归档 §4 的 4 份根目录迁移文档；低置信文档候选（`adding-a-provider`、
    `operator-prompts`）仅在替代入口/生成流程落地后处理。

## 复现命令

```bash
# §1 验证某模块无导入方
grep -rIn "_html_structure" --include='*.py' src tests
grep -rIn "runtime_playwright" --include='*.py' src

# §2 旧名引用方
grep -rIln "fetch_html_with_direct_playwright" --include='*.py' src tests

# §3 本地残留体积
du -sh legacy/ ; git ls-files legacy/ | wc -l   # 后者应为 0

# §6 抽查某死符号是否全仓零引用（输出应为 1，即仅定义本身那一行）
grep -rIw "body_character_count" --include='*.py' --include='*.md' src tests docs | wc -l

# §7 抽查某死方法是否全仓零引用（必须排除 build/ dist/ 旧副本；输出应为 1）
grep -rIw "_discard_cache_entry" --include='*.py' src tests docs | grep -vE 'build/|dist/' | wc -l

# §8 同步 MCP facade / provider registry compat 引用方
rg -n "build_provider_registry|batch_resolve_tool\(|batch_check_tool\(|fetch_paper_tool\(" src tests docs

# §9 tracked 公式资源副本应完全一致
cmp -s scripts/mathml_to_latex_cli.mjs src/paper_fetch/resources/formula/mathml_to_latex_cli.mjs
cmp -s scripts/mathml_to_latex_worker.mjs src/paper_fetch/resources/formula/mathml_to_latex_worker.mjs
cmp -s package.json src/paper_fetch/resources/formula/package.json
cmp -s package-lock.json src/paper_fetch/resources/formula/package-lock.json

# §11 ignored 本地产物体积
du -sh live-downloads .paper-fetch-runs .paper-fetch .formula-tools node_modules 2>/dev/null
find src tests scripts -type d -name '__pycache__' -prune -print | wc -l
find src tests scripts -type f -name '*.pyc' | wc -l

# §12 provider 重复块抽查
rg -n "def extract_asset_html_scopes|def scoped_asset_extractor|def _provider_failure_diagnostics|def _pdf_headers|def finalize_extraction|def validate_query" src/paper_fetch

# §13 公共 helper 重复抽查
rg -n "_normalize_provider_signal_key|_normalize_rule_key|BodyQualityMetrics|coerce_body_quality_metrics|image_magic_type|_image_dimensions|_image_reference_candidates" src/paper_fetch

# §14 fixture/资源漂移抽查
find tests/fixtures/golden_criteria -path '*/body_assets/*' -type f | wc -l
sha256sum \
  tests/fixtures/golden_criteria/10.1038_s41467-022-30729-2/expected.json \
  tests/fixtures/golden_criteria/10.1038_s41561-022-00974-7/expected.json \
  tests/fixtures/golden_criteria/10.1038_s41612-021-00218-2/expected.json \
  tests/fixtures/golden_criteria/10.1038_s43247-024-01295-w/expected.json

# §15 CLI/CI/docs 漂移抽查
rg -n "from paper_fetch\.mcp\.tools|PLAYWRIGHT_BROWSERS_PATH|--skip-playwright-install|provider_status_payload|download_from_fulltext_links" \
  .github scripts install.sh install-formula-tools.sh docs skills src tests

# §16 split-module 聚合层 / compat 垫片抽查
rg -n "paper_fetch\.models\._core|paper_fetch\.providers\.atypon_browser_workflow\._core|paper_fetch\.extraction\.html\.assets\._core|ProviderWaterfallStep|html_profiles" \
  src tests docs --glob '*.py' --glob '*.md'

# §17 源码级重复小工具抽查
rg -n "def _html_headers|def _landing_headers|def _headers|def _cookie_header_for_url|def cookie_header_for_url|def _xml_local_name|def xml_local_name|def first_url_from_srcset|def _first_url_from_srcset|def _response_headers|def _response_status|def .*retryable_asset_failure|def _reference_doi_match|def filter_inline_body_.*_assets|category_for_section_hint_kind|should_download_related_assets_for_result" \
  src/paper_fetch

# §18 MCP/workflow schema/cache/metadata 双维护抽查
rg -n "class MetadataOutput|class ArticleOutput|class FetchPaperOutput|def metadata_from_payload|def quality_from_payload|def article_from_payload|ALLOWED_OUTPUT_MODES|ALLOWED_ASSET_PROFILES|ALLOWED_ARTIFACT_MODES|merge_primary_secondary_metadata|merge_metadata_layers|HTML_BODY_ASSET_DEFAULT" \
  src/paper_fetch

# §19 测试侧新增冗余抽查
rg -n "RecordCaptureHandler|def _response\\(|def fake_fetch|golden_criteria_asset|replace\\(\"/\", \"_\"\\)|test_old_nature_fixture|--skip-playwright-install|def _write_executable|def fake_urlopen|cloudflare_challenge" \
  tests

# §20 文档/脚本/installer 漂移抽查
rg -n "BROWSER_RUNTIME_REQUIRED|PROVIDER_SECTIONS|playwright\\.sync_api|ms-playwright|paper-fetch-skill/1\\.4|display_source|openai\\.yaml|_load_manifest|CaptureFixtureError|ToolError" \
  docs scripts .github installer skills src tests

# 测试基线（必须带 PYTHONPATH=src）
PYTHONPATH=src python -m pytest tests/unit tests/integration -q
```
