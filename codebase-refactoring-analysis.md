# 代码库重构分析报告

> 生成日期：2026-05-08
> 范围：`paper-fetch-skill` 全仓（src/ 44,698 行 + tests/ 27,955 行）
> 分析方法：4 个并行 explorer agent 分别对 providers / extraction-quality / tests / 顶层架构做交叉调研，再人工抽样验证关键发现。

---

## 〇、执行摘要

**整体评估**：代码库已经达到中等成熟度——有 `protocols.py`、`registry.py`、`provider_catalog.py` 这种声明式抽象，并由 `tests/integration/test_architecture_closeout.py` 和 `tests/unit/test_import_boundaries.py` 守边界。三个入口（CLI / MCP / Skill）也都收敛到 `service.py` 之下。

**真正的痛点**：本周 IEEE provider 接入暴露了"扩展一个新 publisher 需要碰多少地方"——这是当前代码库重构 ROI 最高的方向，约 **1500–2000 LOC** 可压缩。

**核心建议**：
- 立刻可做（低风险）：测试公共基础设施抽取、`*_rules.py` 注册式扩展
- 中期推进（中风险）：fetch waterfall 模板下沉到 base、browser workflow 三个命名空间合并、CLI / MCP 编排合一
- 暂缓：`RuntimeContext` 责任拆分（等其他重构完成后自然清晰）

---

## 一、代码库现状

| 维度 | 数字 | 说明 |
|---|---|---|
| 源码总行数 | 44,698 | `src/` 下 |
| Unit 测试行数 | 27,955 | 46 个文件 |
| Provider 数量 | 7 | crossref / elsevier / springer / wiley / science / pnas / ieee |
| 三大单文件 | `providers/ieee.py` 2244、`extraction/html/assets/download.py` 1367、`providers/springer.py` 1253 | 都已超过通常 review 阈值 |
| Golden fixtures | 81 | `tests/fixtures/golden_criteria/` |
| 入口 | 3 | `paper-fetch` CLI、`paper-fetch-mcp` server、`skills/paper-fetch-skill/` 静态 skill |

近期活动重点（git log）：
- `b63c2a3` IEEE Provider Support
- `c117ec5` docs: plan publisher provider support
- 还有 Copernicus / MDPI 待接入（见 `publisher-provider-todo.md`）

---

## 二、四个维度的详细分析

### 2.1 Providers 模块

**结构**：`src/paper_fetch/providers/` 下混合了三种组织方式：

```
providers/
  base.py                    676 行  ProviderClient 抽象
  protocols.py               58 行   协议定义
  registry.py                28 行   provider 注册
  crossref.py / wiley.py / pnas.py / science.py    薄 publisher 实现
  elsevier.py / springer.py / ieee.py              重 publisher 实现（910–2244 行）
  _flaresolverr.py           1020 行 共享 anti-bot 客户端
  _springer_html.py          1073 行 共享 HTML 解析（Springer 主，但被多 provider 引用）
  _pdf_fallback.py           539 行
  _pdf_common.py             290 行
  _article_markdown_*.py     ×4     Elsevier markdown 拆 4 个文件
  _browser_workflow_*.py     ×3     裸文件
  browser_workflow/          子包：article / assets / bootstrap / client / pdf_fallback / profile
  browser_workflow_fetchers/ 子包：context / diagnostics / file / image / memo / scripts
  science_pnas/              子包：_core / asset_scopes / markdown / normalization / postprocess / profile
```

**问题清单**：

1. **fetch_raw_fulltext 模板未下沉**
   - `ieee.py:1316`、`springer.py:399`、`elsevier.py:435` 都重复实现 landing→HTML→PDF→abstract 的同形回退链，每处约 300-400 行
   - `base.py:374` 已有 `ProviderClient` 抽象，但只下沉了 metadata 部分
   - 估计可压缩 ~1000 行

2. **browser_workflow 三个命名空间**
   - 同时存在 `browser_workflow/`、`browser_workflow_fetchers/`、`_browser_workflow_*.py`
   - `browser_workflow/__init__.py` 已经在重新导出 50+ 名字
   - 这是"两次重构留下的考古层"

3. **PDF fallback 散布三处**
   - `_pdf_common.py` 290 行（质量评估）
   - `_pdf_fallback.py` 539 行（HTTP / Playwright 重试）
   - 各 provider 内部还有 `_pdf_candidates()`（如 ieee.py:1233）
   - 应统一为 `PdfFallbackStrategy`

4. **HTML marker / quality 检查重复**
   - `ieee._ieee_marker_counts()` (ieee.py:1184)
   - `springer._finalize_springer_abstract_only_article()` (springer.py:220)
   - `base.py:161-189` 的 metadata 合成
   - 三处独立实现，trace marker 字符串硬编码（"fulltext:ieee_html_ok"、"fulltext:springer_html_fail"…）

5. **Elsevier markdown 拆分**
   - `_article_markdown_elsevier.py` 959 行
   - `_article_markdown_elsevier_document.py` 470 行
   - 拆分理由不明确，需要 review

6. **science_pnas 子包架构不一致**
   - science / pnas 是 26-27 行的薄壳 + 一个独立子包
   - 而 wiley.py 是 319 行的单文件，没有子包
   - 应该统一一种结构

7. **`_*` 命名约定不一致**
   - `_pdf_fallback.py`、`_springer_html.py`：被公开 provider 直接 import
   - `_browser_workflow_*.py`：被 `browser_workflow/__init__.py` re-export
   - 建议要么收纳到 `_helpers/` 子包，要么去掉下划线

### 2.2 Extraction / HTML 与 Quality 模块

**结构**：`extraction/html/` 与 `quality/` 通过 provider 名字耦合。

**问题清单**：

1. **provider 规则散布在多处** —— IEEE 已示范集中模式，老 provider 还没迁
   - 集中模式（**已做对**）：`extraction/html/ieee_rules.py` 73 行集中所有 IEEE 规则（cleanup selectors / promo tokens / access-block tokens / drop keywords）
   - 散布模式（**待迁移**）：
     - `extraction/html/_runtime.py:135-149` 硬编码 `PROFILE_MARKDOWN_PROMO_TOKENS = {"pnas": (...), "springer_nature": (...), "ieee": (...)}` 字典
     - `quality/html_profiles.py:11-16` 直接 `from ...ieee_rules import IEEE_*`
     - `quality/html_profiles.py:159-168` 是 `IEEE_SITE_RULE_OVERRIDES`
     - `quality/html_profiles.py:327-356` 每个 provider 独立的 `{name}_positive_signals` / `{name}_blocking_fallback_signals`

2. **IEEE 规则双名 alias** —— `ieee_rules.py:35`
   ```python
   IEEE_AVAILABILITY_CLEANUP_SELECTORS = IEEE_EXTRACTION_CLEANUP_SELECTORS
   ```
   说明 author 已经意识到两套用途的规则是同一套，但还没去掉冗余命名

3. **不完整重构的痕迹** —— `_runtime.py:323`
   ```python
   def should_drop_html_element(..., noise_profile):
       del noise_profile  # 接受参数后立刻删
   ```

4. **html_profiles.py 模块加载期硬编码 provider** —— line 11-16 的 import 让第三方 provider 必须改源码才能注册

5. **Validator 缺 provider rule 完整性检查** —— `scripts/validate_extraction_rules.py` 没有"新 provider 必须注册哪些 callback" 的契约检查

### 2.3 测试组织

**结构**：59 个测试文件 / 27,955 行。

| 文件 | 行数 | 备注 |
|---|---|---|
| `test_service.py` | 3,147 | |
| `test_science_pnas_provider.py` | 2,571 | 37 tests |
| `test_ieee_provider.py` | 1,803 | 27 tests，本周新增 |
| `test_mcp.py` | 1,792 | |
| `test_provider_request_options.py` | 1,559 | |
| `test_models_render.py` | 1,296 | |
| `test_http_cache.py` | 1,258 | |
| `test_provider_waterfalls.py` | 1,238 | |
| `test_springer_html_regressions.py` | 914 | 25 tests |
| `test_html_shared_helpers.py` | 894 | |

**问题清单**：

1. **`RecordingTransport` 在 5 个文件中独立实现**（已验证）
   - `test_provider_waterfalls.py:312`
   - `test_resolve_query.py:9`
   - `test_provider_request_options.py:24`
   - `test_pdf_fallback_helpers.py:11`
   - `test_ieee_provider.py:20`
   - 五份签名几乎一致（method / url / headers 录制 + 缺响应抛错），下一个 provider 进来又要写第六份

2. **添加 IEEE 一个 provider 的成本**
   - 新 unit test 文件 1803 行
   - `tests/golden_corpus.py:280-318` 加 `_build_ieee_article()` ~40 行
   - `tests/integration/test_golden_corpus.py:41-47` 加 6 个 EXPECTED_PROVIDER_* 常量
   - 整体线性增长

3. **15+ 测试直接 import 私有模块**
   - `test_html_shared_helpers.py:22-23` 引 `_springer_html / _wiley_html`
   - `test_springer_html_regressions.py:11-14` 引 5 个私有 helper
   - `test_html_availability.py` 引 `_springer_html`
   - 重构内部代码会大批断测试

4. **Provider HTML builder 散落各测试文件**
   - `test_ieee_provider.py:59-317` 自带 `_landing_html()` / `_dynamic_html()` 等
   - `golden_corpus.py:140-329` 又有 7 个 `_build_*_article()`
   - 不重用

5. **EXPECTED_PROVIDER_* 常量分散**（`test_golden_corpus.py:17-48`）—— 每加一个 provider 要更新 6 个 dict

### 2.4 顶层架构与入口

**结构**：

```
__init__.py        re-export RuntimeContext / fetch_paper / probe_has_fulltext
service.py         Facade（薄）
runtime.py         RuntimeContext：env / HTTP pool / artifact / parse cache / playwright
config.py          配置加载
tracing.py         trace event model
logging_utils.py   结构化 log
artifacts.py       ArtifactStore（写盘）
cli.py             CLI entry
mcp/
  server.py        505 行
  tools.py         516 行  facade，re-export 50+ 名字
  fetch_tool.py    642 行
  batch.py         443 行
  fetch_cache.py   399 行
  + cache_index / cache_payloads / results / log_bridge / schemas / output_schemas
```

**问题清单**：

1. **CLI 与 MCP 重复编排** —— 已验证
   - `cli.py:130-152`：build_runtime_env → RuntimeContext → FetchStrategy → fetch_paper → save_markdown_to_disk
   - `mcp/fetch_tool.py:87-114`：完全同序
   - 差异只在参数解析

2. **`RuntimeContext` 责任过重** —— `runtime.py:99-190`
   - 既是配置容器（env / download_dir / 各种 client tuning）
   - 又是运行时单例池（HTTP transport / playwright browser / parse cache / artifact store）
   - 还作为公共 API 出现在 `fetch_paper()` / `probe_has_fulltext()` 签名里
   - 调用者必须懂 env 解析、HTTP 调优、浏览器生命周期才能正确构造

3. **Playwright browser 单例耦合** —— `runtime.py:144-190`
   - 共享 browser instance 在 PNAS direct HTML、browser-workflow asset 抓取、PDF fallback 三处共用
   - 没有 protocol / manager 抽象
   - `_playwright_headless` 状态可变；如果两 provider 用不同 headless 值会静默重用错误浏览器

4. **MCP 子包碎片化** —— 10 个文件，3980 行
   - `tools.py` 是 compatibility facade，re-export 50+ 名字
   - 没有清晰的"垂直分层"，每个文件都拿一片 fetch / resolve / probe 的编排
   - 加新功能（streaming / webhook）不知道往哪放

5. **logging / tracing / log_bridge 三处分散**
   - `logging_utils.py`：format
   - `tracing.py`：event model（outcome enum 没强校验）
   - `mcp/log_bridge.py`：MCP notification 改写
   - 没有 `Tracer` / `Logger` 注入接口

6. **架构边界测试只防删不防重复**
   - `test_import_boundaries.py:92-113`、`test_architecture_closeout.py:139-174` 防止 legacy import / sys.path 污染
   - 但不验证"CLI / MCP / workflow 不该重复编排"

7. **`ArtifactStore` 与 `FetchCache` 双写盘路径** —— 没有事务、没有统一错误通道、`download_dir` 中途变化时两边会偏离

---

## 三、Top 重构机会（综合排序）

### 🔴 高杠杆 / 中风险

**[R1] 把 provider 规则统一到注册式 `*_rules.py`**
- 杠杆：未来每个新 provider 都只需要新建一个文件 + 一行注册，不用碰 `_runtime.py` / `html_profiles.py`
- 风险：中（要迁移 5 个老 provider 的零散规则；有 golden corpus 兜底）
- 范围：`extraction/html/*_rules.py` × 5 + `quality/html_profiles.py`
- 起步：参考 `ieee_rules.py:1-73` 的形态，先建 `pnas_rules.py` / `springer_rules.py` / `wiley_rules.py` / `science_rules.py`，再把 `_runtime.py:135-149` 的 `PROFILE_MARKDOWN_PROMO_TOKENS` 改造成 registry

**[R2] 把 fetch waterfall 模板下沉到 `base.py`**
- 杠杆：最大；ieee.py / springer.py / elsevier.py 三大文件能压 ~1000 行
- 风险：高；触动 main fetch 路径，必须 golden corpus + live smoke 全绿
- 范围：`base.py` 加 `fetch_with_fallback_chain(landing → html → pdf → abstract)` 模板，三大 provider 退化为声明 marker / candidate URL / quality threshold
- 前置：[R5] 先做（要靠测试基础设施做安全网）

### 🟡 中杠杆 / 低-中风险

**[R3] 合并 browser_workflow 三个命名空间**
- 杠杆：中（结构清晰化，未来 Wiley/Science 改造会快）
- 风险：中（很多 import 路径要改）
- 范围：`browser_workflow/`、`browser_workflow_fetchers/`、`_browser_workflow_*.py` 三处合并为单一 `browser_workflow/` 子包

**[R4] 抽 CLI / MCP / Skill 共用的 `FetchPipeline`**
- 杠杆：中（消除 cli.py:130-152 与 mcp/fetch_tool.py:87-114 的重复编排）
- 风险：低（接口稳定，仅做 extract method）
- 范围：新建 `workflow/pipeline.py`，CLI 与 MCP 退化为参数解析 + 调用
- 顺手做：把 `RuntimeContext` 拆成 `FetchRequest`（请求参数）+ `RuntimeDeps`（运行时资源）

**[R5] 抽出测试公共基础设施**
- 杠杆：中（每加一个 provider 节省 ~200 行 boilerplate）
- 风险：低（仅测试代码）
- 范围：
  - `RecordingTransport` 五份合一，移到 `tests/unit/_paper_fetch_support.py`
  - `golden_corpus.py:140-329` 的 7 个 `_build_*_article()` 改成 builder registry
  - `test_golden_corpus.py:17-48` 的 EXPECTED_PROVIDER_* 6 个 dict 合并成 manifest

### 🟢 低杠杆 / 低风险（顺手清理）

**[R6] 拆分巨型测试文件**
- `test_service.py` 3147 / `test_science_pnas_provider.py` 2571 / `test_ieee_provider.py` 1803 / `test_mcp.py` 1792
- 按 workflow（landing / html / pdf / abstract）拆，每个 < 600 行

**[R7] 清理不完整重构痕迹**
- `extraction/html/_runtime.py:323`：`should_drop_html_element` 接收 `noise_profile` 后 `del noise_profile`
- `ieee_rules.py:35`：`IEEE_AVAILABILITY_CLEANUP_SELECTORS = IEEE_EXTRACTION_CLEANUP_SELECTORS`，改为单一名字

**[R8] 限制 unit test 直接 import 私有模块**
- 影响 15+ 文件
- 在公开 provider 模块上暴露薄 API，让测试走 `springer.parse_html()` 而不是 `_springer_html.parse_html_metadata()`
- 配合 [R1] 一起做更顺

**[R9] 收纳 PDF fallback / HTML quality 共享逻辑**
- `_pdf_common.py` + `_pdf_fallback.py` + 各 provider `_pdf_candidates()` → `PdfFallbackStrategy`
- 各 provider 的 marker counter → `HtmlQualityAssessor`
- 杠杆中等但范围广，建议放在 [R2] 之后做

**[R10] Trace marker 集中**
- 现状散布 100+ 个硬编码字符串："fulltext:ieee_html_ok"、"fulltext:springer_html_fail"…
- 集中到 `tracing.py` 的枚举或常量

**[R11] 统一 `ArtifactStore` 与 `FetchCache`**
- 改成单一 `ArtifactManager` + manifest，写入走 temp+rename 事务
- 优先级低，等 [R4] 完成后再看

---

## 四、建议的执行顺序

```
阶段 1（低风险打基础，约 1-2 个 PR）
    [R5] 测试基础设施      ──┐
    [R7] 清理重构痕迹      ──┤── 并行可做
    [R10] Trace marker 集中 ──┘

阶段 2（结构清理，约 2-3 个 PR）
    [R1] *_rules.py 注册式 ───→ [R8] 测试避免私有 import
                                        ↓
                                 [R6] 拆巨型测试文件

阶段 3（主路径下沉，约 2-3 个 PR，需 live smoke 验证）
    [R3] browser_workflow 合并 ─┐
    [R9] PDF / HTML quality   ─┤
    [R2] fetch waterfall 模板 ─┘

阶段 4（入口与编排）
    [R4] FetchPipeline + RuntimeContext 拆分
    [R11] ArtifactStore + FetchCache 合并

每个阶段结束后：
- PYTHONPATH=src python3 -m pytest tests/unit -q
- live smoke（Wiley/Science/PNAS/IEEE 各取一例）
- golden corpus 全绿
```

---

## 五、决策点

需要在动手前确认的几个方向性问题：

1. **是否在做 R1 之前先把 Copernicus / MDPI 接进来？** 
   - 优势：用两个新 provider 验证 R1 的注册式设计
   - 劣势：把"散布规则"模式又复制两次，迁移成本变大

2. **R2（waterfall 模板下沉）是否需要保留 provider 级 escape hatch？**
   - 现状：每个 provider 都可以在任意一步 hook 自定义逻辑
   - 模板化后：要么强制走 happy path，要么留 callback hook
   - 建议：留 hook，但默认实现要让 90% 的步骤无需 override

3. **R4 拆 RuntimeContext 是否破坏公开 API？**
   - `fetch_paper(query, *, runtime=...)` 已经在 README、MCP / CLI / Skill 中暴露
   - 拆分时建议保留 `RuntimeContext` 作为兼容 typedef，新代码用 `RuntimeDeps`
   - 至少需要一个 deprecation 周期

4. **要不要在重构同时做 PyPI 发布**（todo.md 第 9 项）？
   - 建议：分开做。先把 R1+R5+R7 完成（不动 API），再发 PyPI；R2+R4 之后再发一个 minor

---

## 附录 A：被引用的关键文件

| 文件 | 角色 |
|---|---|
| `src/paper_fetch/providers/base.py` | ProviderClient 抽象（676 行） |
| `src/paper_fetch/providers/ieee.py` | 最新 provider，最大单文件（2244 行） |
| `src/paper_fetch/providers/_flaresolverr.py` | anti-bot 共享客户端（1020 行） |
| `src/paper_fetch/extraction/html/_runtime.py` | 提取引擎（797 行）；含 `PROFILE_MARKDOWN_PROMO_TOKENS` 硬编码 |
| `src/paper_fetch/extraction/html/ieee_rules.py` | 集中规则模式示范（73 行，本周新增） |
| `src/paper_fetch/quality/html_profiles.py` | provider profile（含 IEEE 等的 positive_signals） |
| `src/paper_fetch/runtime.py` | RuntimeContext（容器+依赖+生命周期） |
| `src/paper_fetch/service.py` | Facade |
| `src/paper_fetch/cli.py` / `mcp/fetch_tool.py` | 重复编排来源 |
| `tests/unit/_paper_fetch_support.py` | 已有的测试 helper（299 行，未充分使用） |
| `tests/golden_corpus.py` | golden 测试编排 |
| `tests/integration/test_architecture_closeout.py` | 架构边界测试 |
| `publisher-provider-todo.md` | Copernicus / MDPI / IEEE 接入清单 |

## 附录 B：分析覆盖追踪（已并入正文）

| 模块 | 处理 |
|---|---|
| `formula/convert.py` + `formula/install.py` | §6.2 [R12]：后端选择 strategy 化 |
| `extraction/html/assets/download.py` | §6.2 [R15]：候选状态机统一 |
| `paper_fetch_devtools/` | §6.3：边界清晰，无需重构 |
| `vendor/flaresolverr/` | §6.2 [R16]：reference 文件待 grep 验证 |
| `installer/` + `install-offline.{sh,ps1}` + skills installers | §6.6 [R18]–[R20]：见下文 |

---

*报告完。如需推进任一项，请指明 R 编号或方向。*

---

## Refactoring Closeout

- [x] Round 1: test infra + golden parallelism
  - 合并通用 `RecordingTransport` 到 `tests/unit/_paper_fetch_support.py`，provider/request/pdf/resolve/IEEE 相关测试复用共享 helper。
  - 将 golden corpus 集成测试从 `unittest` 方法内循环和 `subTest` 改为 pytest 参数化，full golden 可按 fixture 被 `-n auto` 分发。
  - 将 golden provider 期望收敛为单一 `ProviderGoldenContract` registry，并同步 full golden 59-fixture CI 文案和本地验证文档。
- [x] Round 2: provider extraction rules registry
  - 新增 `paper_fetch.extraction.html.provider_rules`，集中 Science/PNAS/Springer Nature/Wiley/IEEE 的 Markdown promo、cleanup、availability site rule 和 access-block tokens。
  - `_runtime.py` 与 `quality/html_profiles.py` 改为读取 registry，移除新增 provider 必须改核心硬编码字典的路径，并清理 `noise_profile` 接收后删除的残留。
  - `scripts/validate_extraction_rules.py` 增加 provider rule registry 完整性检查，`docs/provider-development.md` 写入新增 provider 的规则注册要求。
- [x] Round 3a: low-risk legacy path cleanup
  - R14：`metadata_types.py` 已迁移为 `paper_fetch.metadata.types`，全仓 import 更新到 canonical namespace，旧顶层 `metadata_types.py` 删除；`paper_fetch.metadata` 对 TypedDict schema 做公开 re-export，`CrossrefLookupClient` 改为懒加载 re-export 以避免 provider 初始化环。
  - R7：IEEE provider 改为直接使用 `paper_fetch.extraction.html.provider_rules`，删除旧 `extraction/html/ieee_rules.py` 兼容导出和 `IEEE_AVAILABILITY_CLEANUP_SELECTORS` alias。
  - R16：确认 `vendor/flaresolverr/fetch_fulltext.reference.py` 只有报告提及、无生产/测试/脚本引用后删除；`vendor/flaresolverr/UPSTREAM.md` 与架构卫生测试已记录 reference snapshot 不再回流。
  - R8：`test_browser_workflow_namespace.py` 改为只断言 canonical `paper_fetch.providers.browser_workflow` facade 和子模块，不再 import 旧 `_browser_workflow_*` 兼容入口。
  - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_ieee_provider.py tests/unit/test_import_boundaries.py tests/integration/test_architecture_closeout.py -q`；`PYTHONPATH=src python3 -m pytest tests/unit/test_browser_workflow_namespace.py -q`。
  - 剩余风险：R3 还需把 production 里的 `_browser_workflow_*` / `browser_workflow_fetchers/` 兼容实现迁入 canonical 子包并删除旧入口；部分单元测试仍直接覆盖 provider-private 当前实现，需随后续结构重构逐步改为公开行为测试。
- [x] Round 3b: split giant unit tests
  - R6：将 `test_service.py`、`test_science_pnas_provider.py`、`test_ieee_provider.py`、`test_mcp.py` 拆为 focused test modules，并抽出 `_service_support.py`、`_science_pnas_provider_support.py`、`_ieee_provider_support.py`、`_mcp_support.py` 共享测试 helper。
  - 拆分后相关新文件均不超过 600 行；原巨型文件仅保留指向新模块的 stub docstring，避免继续承载测试逻辑。
  - 同步文档：`docs/extraction-rules.md` 的测试追踪链接已按测试函数名指向新文件；`docs/deployment.md` 的本地 smoke 命令改为覆盖 `test_service_*.py` 与 `test_mcp_*.py`。
  - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_service_*.py tests/unit/test_science_pnas_provider_*.py tests/unit/test_ieee_provider_*.py tests/unit/test_mcp_*.py -q`（202 passed, 46 subtests passed）。
  - 剩余风险：拆分是机械移动，未重写测试语义；部分 support 模块仍携带 provider-private helper import，后续 R3/R13/R15 再按公开行为和 canonical namespace 收口。
- [x] Round 3c: trace marker 集中
  - R10：在 `paper_fetch.tracing` 中加入 `trace_marker`、`provider_stage_marker`、`fulltext_marker`、`download_marker`、`metadata_marker`、`route_marker`、`resolve_marker`、`fallback_marker` builder，集中 provider / workflow / artifact 路径中的 marker 构造。
  - 已迁移 `artifacts.py`、`workflow/fulltext.py`、`workflow/metadata.py`、`workflow/routing.py`、`workflow/rendering.py`、`workflow/shared.py`、`providers/base.py`、`providers/elsevier.py`、`providers/springer.py`、`providers/wiley.py`、`providers/ieee.py` 与 browser workflow 相关模块的硬编码 source trail marker。
  - 删除旧路径：无文件删除；本轮删除的是生产代码内重复硬编码 marker 构造，marker 文本值保持不变（例如 `fulltext:ieee_html_ok`、`route:provider_selected_elsevier`）。
  - 只读复核：结构优化轮开始时已让 subagent 复核 R10/R3/R12/R4/R17 的切入顺序，结论是先收口 R10 再进入 browser workflow namespace consolidation。
  - 验证：`PYTHONPATH=src python3 -m compileall -q src/paper_fetch`；`PYTHONPATH=src python3 -m pytest tests/unit/test_provider_waterfalls.py tests/unit/test_service_*.py tests/unit/test_ieee_provider_*.py tests/unit/test_provider_payloads.py tests/unit/test_import_boundaries.py -q`（122 passed, 46 subtests passed）。
  - 剩余风险：测试和文档中的 marker 字符串仍作为行为契约保留；后续 R3/R12/R4/R17 若新增 marker 必须继续走 `tracing.py` builder。
- [x] Round 3d: browser workflow namespace consolidation
  - R3：将 `paper_fetch.providers._browser_workflow_shared` 和 `_browser_workflow_html_extraction` 的实现迁入 `paper_fetch.providers.browser_workflow.shared/html_extraction`，将 `paper_fetch.providers.browser_workflow_fetchers/` 迁入 `paper_fetch.providers.browser_workflow.fetchers/` 包。
  - 删除旧路径：`src/paper_fetch/providers/_browser_workflow_shared.py`、`src/paper_fetch/providers/_browser_workflow_html_extraction.py`、`src/paper_fetch/providers/_browser_workflow_fetchers.py`、`src/paper_fetch/providers/browser_workflow_fetchers/`；同步 `SOURCES.txt`、architecture closeout guard、`docs/providers.md`、`docs/architecture/target-architecture.md`、`docs/extraction-rules.md`。
  - 生产 import 已改到 canonical namespace：`_science_pnas_profiles.py` 使用 `paper_fetch.providers.browser_workflow.shared`，browser workflow 内部使用 `.fetchers` 和 `.html_extraction`。
  - R3 补丁：`paper_fetch.providers.browser_workflow` facade 改为懒加载 re-export，避免 `_science_pnas_profiles -> browser_workflow.shared -> browser_workflow.__init__ -> _flaresolverr -> _science_pnas_profiles` 的初始化环。
  - 只读复核：本轮开始已启动 browser workflow 只读复核；本地扫描确认生产代码无旧入口 import，旧模块 `importlib.util.find_spec()` 返回 `None`。
  - 验证：`PYTHONPATH=src python3 -m compileall -q src/paper_fetch`；`PYTHONPATH=src python3 -m pytest tests/unit/test_browser_workflow_namespace.py tests/unit/test_science_pnas_candidates.py tests/unit/test_provider_request_options.py tests/unit/test_service_browser_workflow.py tests/unit/test_science_pnas_provider_retries.py tests/unit/test_html_availability.py tests/integration/test_architecture_closeout.py -q`（103 passed, 24 subtests passed）。
  - 补充验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_science_pnas_markdown.py tests/unit/test_browser_workflow_namespace.py tests/unit/test_formula_conversion.py tests/unit/test_elsevier_markdown.py -q`（78 passed, 9 subtests passed）。
  - 剩余风险：`browser_workflow` facade 仍 re-export 若干测试 patch 点（如 `_cached_browser_workflow_markdown`、fetcher builders），后续 R17 拆 Playwright 生命周期时需要继续保持 facade patch 面或同步测试改成公开行为。
- [x] Round 3e: formula backend registry
  - R12：新增 `FormulaBackendStrategy` 与 `FORMULA_BACKEND_REGISTRY`，由 registry 派生 `SUPPORTED_BACKENDS`、`BENCHMARK_BACKENDS`、`AUTO_BACKENDS`，保留 `texmath`、`mathml-to-latex`、`mml2tex`、`legacy`、`auto` 名称和 `MATHML_CONVERTER_BACKEND` 优先级。
  - 删除旧路径：无文件删除；删除的是 `_convert_mathml_string_uncached()` 内按 backend 名称分支的旧 if-else dispatcher，改由 strategy 执行。converter 函数仍动态按函数名解析，保留测试和调用方 monkeypatch `convert_with_texmath` / `convert_with_mathml_to_latex` 的行为。
  - 行为保持：默认 `texmath` 失败仍 fallback 到 `mathml-to-latex`；显式 `backend="texmath"` 或 env `MATHML_CONVERTER_BACKEND=texmath` 失败不 fallback；`auto` 仍按 `texmath -> mathml-to-latex` 顺序尝试；`legacy` 仍识别但运行时报不可用。
  - 同步文档：`docs/providers.md` 的公式后端说明补充 registry 与 benchmark/auto 顺序约定。
  - 只读复核：本轮开始已让 subagent 复核 R12 env/default/cache/benchmark 行为和测试覆盖，重点风险为显式选择与默认 fallback 的差异。
  - 验证：`PYTHONPATH=src python3 -m py_compile src/paper_fetch/formula/convert.py`；`PYTHONPATH=src python3 -m pytest tests/unit/test_formula_conversion.py tests/unit/test_elsevier_markdown.py tests/unit/test_science_pnas_markdown.py -q`（初次暴露 R3 lazy facade 循环后修复）；最终 `PYTHONPATH=src python3 -m pytest tests/unit/test_science_pnas_markdown.py tests/unit/test_browser_workflow_namespace.py tests/unit/test_formula_conversion.py tests/unit/test_elsevier_markdown.py -q`（78 passed, 9 subtests passed）。
  - 剩余风险：`BENCHMARK_BACKENDS` 被 benchmark 脚本直接 import，后续新增后端必须在 registry 上显式声明 benchmark 资格并补脚本/文档测试。
- [x] Round 3f: FetchPipelineRequest builder
  - R4-修正：新增 `paper_fetch.workflow.request_builder.build_fetch_pipeline_request()`，统一 CLI/MCP 对 `FetchPipelineRequest` 的装配，集中 context 派生的 `env`、`transport`、`clients`、`cancel_check` 应用规则。
  - 删除旧路径：无文件删除；删除 CLI/MCP 直接拼 `FetchPipelineRequest(...)` 的重复路径。生产代码中直接构造 `FetchPipelineRequest` 只剩 request builder 本身（测试 helper 仍可直接构造 dataclass）。
  - MCP 独有行为保持在 adapter 层：`prefer_cache`/fetch-envelope sidecar 读写、`save_markdown` 后刷新 cache index、inline image shaping、async progress/cancellation/log bridge 不下沉到 workflow。
  - 同步文档：`docs/providers.md` 和 `docs/architecture/target-architecture.md` 说明 `request_builder` 与 `FetchPipeline` 的分工；`SOURCES.txt` 加入 `workflow/request_builder.py`。
  - 只读复核：本轮已启动 subagent 复核 R4 request builder 范围；本地扫描确认 `FetchPipelineRequest(` 的生产调用只剩 `workflow/request_builder.py`。
  - 验证：`PYTHONPATH=src python3 -m py_compile src/paper_fetch/workflow/request_builder.py src/paper_fetch/cli.py src/paper_fetch/mcp/fetch_tool.py tests/unit/test_fetch_pipeline.py`；`PYTHONPATH=src python3 -m pytest tests/unit/test_fetch_pipeline.py tests/unit/test_cli.py tests/unit/test_mcp_payload_cache.py tests/unit/test_mcp_async_tools.py -q`（55 passed）。
  - 剩余风险：MCP Markdown 保存仍是 adapter 后置步骤而不是 pipeline `MarkdownSaveSpec`，因为它需要刷新 MCP cache index；后续 R11 合并写盘管理时可再评估是否下沉。
- [x] Round 3g: Playwright lifecycle manager
  - R17：新增 `paper_fetch.runtime_playwright.PlaywrightContextManager`，将 Playwright manager/browser/headless 状态、shared Chromium lazy start、headless 切换重启和 idempotent close 逻辑从 `RuntimeContext` 中抽出。
  - `RuntimeContext` 保留 public API：`playwright_browser()`、`new_playwright_context()`、`close_playwright()` 继续可用，但只委托给 lifecycle manager；provider/browser workflow 仍只依赖 `RuntimeContext.new_playwright_context()`，不接触 manager/browser 内部状态。
  - 删除旧路径：无文件删除；删除 `RuntimeContext` 内 `_playwright_manager`、`_playwright_browser`、`_playwright_headless` 等旧生命周期状态字段。
  - 同步文档：`docs/architecture/target-architecture.md` 和 `docs/providers.md` 说明 `PlaywrightContextManager` 负责生命周期；`SOURCES.txt` 加入 `runtime_playwright.py`。
  - 只读复核：本轮已启动 R17 subagent 复核；本地扫描确认 runtime 旧 browser/manager/headless 状态只剩新 manager 内部实现，provider 调用点仍是 `new_playwright_context()`。
  - 验证：`PYTHONPATH=src python3 -m py_compile src/paper_fetch/runtime.py src/paper_fetch/runtime_playwright.py tests/unit/test_runtime_playwright.py`；`PYTHONPATH=src python3 -m pytest tests/unit/test_runtime_playwright.py tests/unit/test_service_runtime.py tests/unit/test_browser_workflow_fetchers.py tests/unit/test_science_pnas_provider_fallbacks.py tests/unit/test_ieee_provider_routes.py -q`（34 passed）。
  - 剩余风险：threaded browser-workflow asset fetchers 仍有自己的线程私有 Playwright fetcher lifecycle；这是并发 worker 隔离策略，不属于 `RuntimeContext` shared browser 生命周期。
- [x] Round 3h: IEEE waterfall 收口与 R9/R2 部分收口复核
  - R2：将 IEEE `fetch_raw_fulltext()` 的 direct REST HTML → clean-browser HTML → PDF → abstract 手写链改为 `ProviderWaterfallStep` 序列，复用 `run_provider_waterfall()` 聚合 warning、source trail 和最终 failure；`_fetch_dynamic_html_payload()`、`_fetch_browser_html_payload()`、`_fetch_pdf_payload()`、`_abstract_only_payload()` 继续作为 provider-specific retrieval/hook。
  - R9：复核确认 `PdfFallbackStrategy` 与 `HtmlQualityAssessor` 已是主要 PDF fallback / HTML availability 的共享入口，Springer、Wiley、IEEE 与 Science/PNAS markdown availability 路径已接入；IEEE 的 orchestration 重复本轮已删除。
  - 删除旧路径：无文件删除；删除的是 IEEE provider 内重复的 fallback 分支、warning 聚合和 trace marker 拼装逻辑。旧 import/API 兼容层未新增。
  - 只读复核：Rawls 复核指出 R9 共享策略已实质落地，但 Springer `fetch_raw_fulltext` 与 `prepare/maybe_recover` 仍有两套 HTML→PDF/abstract 入口，Wiley/Elsevier 的官方 PDF API 请求也仍保留 provider-specific 适配；IEEE 相关结论已在本轮补丁后过时。
  - 验证：`python3 -m py_compile src/paper_fetch/providers/ieee.py src/paper_fetch/providers/_waterfall.py`；`PYTHONPATH=src python3 -m pytest tests/unit/test_ieee_provider_routes.py tests/unit/test_ieee_provider_pdf_golden.py tests/unit/test_provider_waterfalls.py tests/unit/test_provider_payloads.py -q`（46 passed, 26 subtests passed）。
  - 剩余风险：R2 尚未抽到 provider 只声明静态 profile/DSL 的程度；Springer fetch-result recovery 为了保留 abstract-only provisional article 仍有专门路径。R9 也仍是部分完成：IEEE browser HTML/PDF 内部步骤、Elsevier official PDF API、Wiley TDM API 的 headers/token/redirect 适配仍留在 provider 层，但核心 PDF bytes→Markdown、候选重试和 HTML quality 判断已复用共享 helper。
- [x] Round 3i: HTML-to-Markdown renderer 共用层
  - R13：新增 `paper_fetch.extraction.html.renderer`，提供 `HtmlMarkdownRenderer`、`render_html_markdown()`、`render_provider_html_fragment()` 与 `RenderedHtmlFragment`，集中 HTML/cleaned-fragment → Markdown → clean/postprocess → sidecar payload 的薄编排。
  - 接入点：generic `ProviderClient.html_to_markdown()`、Springer/Nature HTML payload、Science/PNAS/Wiley browser-workflow markdown、IEEE DOM renderer 已改走 renderer facade；provider 仍保留各自的 container selection、DOM cleanup、quality 判定、authors/references 和 provider-specific postprocess。
  - 删除旧路径：无文件删除；删除的是 provider 内重复调用 `extract_article_markdown(...)` 后再手动 `clean_markdown(...)` 的编排代码。旧 import/API 兼容层未新增。
  - 同步文档：`docs/provider-development.md`、`docs/architecture/target-architecture.md`、`docs/extraction-rules.md` 更新 HTML renderer canonical owner；`SOURCES.txt` 加入 `extraction/html/renderer.py`。
  - 只读复核：Dewey 建议不要先抽大一统 renderer；本轮按其建议只抽已选 HTML fragment 的薄 facade，不移动 Springer/Nature、Science/PNAS/Wiley、IEEE 的高风险 selector 和 postprocess 差异。
  - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_html_renderer.py tests/unit/test_html_shared_helpers.py tests/unit/test_springer_html_regressions.py tests/unit/test_springer_html_tables.py tests/unit/test_science_pnas_markdown.py tests/unit/test_science_pnas_postprocess.py tests/unit/test_science_pnas_postprocess_units.py tests/unit/test_ieee_provider_routes.py tests/unit/test_ieee_provider_pdf_golden.py -q`（134 passed, 29 subtests passed）。
  - 剩余风险：Elsevier XML renderer 不纳入 R13 HTML facade；Springer/Nature 的 DOM renderer 与 Science/PNAS/Wiley 的 trafilatura + postprocess 语义仍不同，后续只能继续下沉通用 sidecar / finalize helper，不能强行统一选择策略。
- [x] Round 3j: asset download 状态机与 requester 拆分
  - R15：新增 `paper_fetch.extraction.html.assets.state`，承载 `AssetDownloadCandidate` / `AssetDownloadAttempt` / `AssetDownloadResolution`、bounded executor、按输入顺序回收和 save-result 收集逻辑；`download.py` 继续以旧私有名 import，保持内部调用点稳定。
  - 新增 `paper_fetch.extraction.html.assets.requester`，承载 cookie domain/path/secure 匹配、cookie-seeded opener 和 opener request；`download.py` 与 `assets.__init__` 仍暴露 `_build_cookie_seeded_opener` / `_request_with_opener` patch 点，避免破坏既有测试和 provider injection。
  - 删除旧路径：无文件删除；删除的是 `download.py` 内状态机 dataclass、并发收集器和 cookie-aware urllib requester 的内联实现。figure/supplementary resolver、诊断字段、保存格式和公开 API 未改。
  - 同步文档：`docs/providers.md`、`docs/architecture/target-architecture.md`、`docs/extraction-rules.md` 记录 `assets.state` / `assets.requester` owner；`SOURCES.txt` 加入新模块。
  - 只读复核：Carson 建议第一轮只抽 `_state.py` / `_requester.py`，暂不拆 figure/supplementary resolver；本轮按该低风险边界执行。
  - 验证：`python3 -m py_compile src/paper_fetch/extraction/html/assets/download.py src/paper_fetch/extraction/html/assets/requester.py src/paper_fetch/extraction/html/assets/state.py`；`PYTHONPATH=src python3 -m pytest tests/unit/test_html_shared_helpers.py tests/unit/test_science_pnas_provider_asset_downloads.py tests/unit/test_science_pnas_provider_asset_failures.py tests/unit/test_science_pnas_provider_retries.py tests/unit/test_ieee_provider_asset_downloads.py tests/unit/test_provider_request_options.py -q`（88 passed）。
  - 剩余风险：`download.py` 仍包含 figure/supplementary resolver、failure diagnostics 和 save logic；这些与输出 shape 高耦合，后续只能在新增更细粒度 regression 后继续拆。`providers._pdf_fallback` 仍有近似 cookie-aware requester，后续若上移共享需保持 PDF 错误文案。
- [x] Round 3k: ArtifactStore / FetchCache 写盘收口
  - R11：`ArtifactStore` 新增 `write_bytes_file()`、`write_text_file()`、`write_json_file()` 原子 writer，provider PDF/binary local copy、Springer original HTML、Markdown 保存、MCP fetch-envelope sidecar 和 cache index JSON 写入统一走该 writer。
  - `FetchCache` 继续负责 fetch-envelope sidecar 的 version、`EXTRACTION_REVISION`、request match、cache hit/reuse 和 index refresh 语义；实际 sidecar JSON materialization 委托给 `ArtifactStore`，避免再维护独立 `.part + replace` 写盘路径。
  - 删除旧路径：无文件删除；删除的是 `FetchCache.write_fetch_envelope()`、`cache_index._write_index_unlocked()` 和 `workflow.rendering.save_markdown_to_disk()` 内部各自手写的原子写入流程。asset downloader 的文件命名 / 并发下载 / 诊断 shape 暂不下沉到 `ArtifactStore`。
  - 同步文档：`docs/README.md`、`docs/providers.md`、`docs/architecture/target-architecture.md` 更新 `ArtifactStore` 与 `FetchCache` 边界。
  - 只读复核：Averroes 建议低风险口径限定为 provider payload/html、Markdown 与 fetch-envelope/cache-index 的原子写盘管理，sidecar cache 语义仍留在 `FetchCache`；本轮按该边界执行。
  - 验证：`python3 -m py_compile src/paper_fetch/artifacts.py src/paper_fetch/mcp/fetch_cache.py src/paper_fetch/mcp/cache_index.py src/paper_fetch/workflow/rendering.py`；`PYTHONPATH=src python3 -m pytest tests/unit/test_service_runtime.py tests/unit/test_mcp_payload_cache.py tests/unit/test_fetch_pipeline.py tests/unit/test_mcp_server_resources.py tests/unit/test_cli.py tests/unit/test_fetch_common.py tests/unit/test_mcp.py tests/unit/test_mcp_async_tools.py -q`（82 passed, 8 subtests passed）。
  - 剩余风险：provider asset 下载仍在各 downloader 中直接落盘，这是 asset candidate 命名、并发 resolve / 串行 save 和诊断字段的一部分；后续若继续收口必须先补资产输出 shape regression。
- [x] Round 3l: Springer waterfall/recovery 收口
  - R2：`SpringerClient.prepare_fetch_result_payload()` 的 HTML success / abstract-only provisional / HTML failure 路径改为 `ProviderWaterfallStep` + `run_provider_waterfall()`，由 waterfall 统一附加 `fulltext:springer_html_fail`、保留 HTML warning 并返回 fetch-result recovery context。
  - R2：`maybe_recover_fetch_result_payload()` 的 PDF recovery 改为单步 waterfall，复用同一套 warning/source trail 聚合；Springer 专属的 abstract-only provisional article、metadata-only fallback 和 PDF recovery 行为保留。
  - 删除旧路径：无文件删除；删除的是 Springer fetch-result prepare/recover 中手写 HTML failure 包装、PDF fallback 失败消息拼接和 warning 追加的重复分支。
  - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_springer_html_regressions.py tests/unit/test_springer_html_tables.py tests/unit/test_provider_waterfalls.py tests/unit/test_service_provider_managed_fallbacks.py -q`（69 passed, 13 subtests passed）。
  - 剩余风险：Springer 仍保留 provider-owned HTML attempt、inline table、abstract-only provisional article 和 PDF candidate 构造；本轮只收口 orchestration，不把这些 provider-specific retrieval/hook 抽成静态 DSL。
- [x] Round 3m: official PDF response helper 收口
  - R9：`paper_fetch.providers._pdf_common` 新增 `pdf_fetch_result_from_response()`，集中 response headers/body/final URL → PDF 校验 → `PdfFetchResult` 的转换，复用既有 `looks_like_pdf_payload()`、`filename_from_headers()` 和 `pdf_fetch_result_from_bytes()`。
  - R9：Wiley TDM PDF 与 Elsevier official PDF lane 已接入该 helper；各 provider 仍保留自己的 URL、headers/token/env、redirect 策略、rate-limit/transient retry 参数和错误文案。
  - 删除旧路径：无文件删除；删除的是 Wiley/Elsevier provider 内重复的 response header 归一化、PDF body 校验和 `PdfFetchResult` 构造代码。
  - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_provider_waterfalls.py tests/unit/test_provider_request_options.py tests/unit/test_service_official_pipeline.py tests/unit/test_service_pdf_and_provider_fallbacks.py -q`（83 passed, 3 subtests passed）。
  - 剩余风险：browser PDF fallback、asset 下载、cookie/Playwright seeding 和 provider-specific auth 没有强行合并；`providers._pdf_fallback` 仍保留候选重试与失败 HTML 诊断的专门逻辑。
- [x] Round 3n: provider-private HTML test import 收口
  - R8：新增 `paper_fetch.providers.springer_html` 与 `paper_fetch.providers.wiley_html` 作为 provider-owned 公开 facade；Springer/Wiley provider 自身也改走这些 facade，以便测试 patch 点落在 canonical path。
  - R8：unit tests 中对 `_springer_html`、`_wiley_html`、`_science_html`、`_pnas_html` 的直接 import 已迁移到公开 facade 或现有 `ProviderBrowserProfile.fallback_author_extractor` hook；`tests/golden_corpus.py` 暂留必要 private import。
  - R8：`test_import_boundaries.py` 增加边界断言，阻止非 allowlist 测试继续直接 import provider-private HTML helper。
  - 同步文档：`docs/architecture/target-architecture.md` 与 `docs/extraction-rules.md` 的 HTML extraction owner 改到 `springer_html` / `wiley_html` facade。
  - 删除旧路径：无文件删除；本轮只新增 canonical facade 并删除测试中的 private import 依赖。旧 private implementation 仍作为 provider 内部模块存在。
  - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_import_boundaries.py tests/unit/test_html_shared_helpers.py tests/unit/test_springer_html_regressions.py tests/unit/test_science_pnas_markdown.py tests/unit/test_elsevier_markdown.py tests/unit/test_regression_samples.py -q`（132 passed, 17 subtests passed）；补充 `PYTHONPATH=src python3 -m pytest tests/unit/test_springer_html_tables.py tests/unit/test_html_availability.py tests/unit/test_models_render.py tests/unit/test_science_pnas_candidates.py tests/unit/test_service_pdf_and_provider_fallbacks.py tests/unit/test_provider_waterfalls.py -q`（140 passed, 21 subtests passed）。
  - 剩余风险：`tests/golden_corpus.py` 仍直接调用 private provider renderers 来生成/校验 corpus，后续需要等 golden 行为入口更细后再迁移；`_script_json` 等非本轮目标的 provider-private helper 仍有专门单元覆盖。

---

## 六、补充扫描（2026-05-08，第二轮）

> 第二轮扫描覆盖了首轮未深入的领域：`formula/`、`extraction/html/assets/`、`resolve/`、`metadata/`、`markdown/`、`models/`、`workflow/`、`http/`、`paper_fetch_devtools/`、`vendor/flaresolverr/`、`scripts/`。

### 6.1 首轮报告的修正

**[R4 修正]** **`FetchPipeline` 已经存在并在用**——首轮报告写错了。
- `src/paper_fetch/workflow/pipeline.py:63-125` 已实现 `FetchPipeline.run()`
- `cli.py:138-139` 调用 `FetchPipeline(fetch_paper).run(FetchPipelineRequest(...))`
- `mcp/fetch_tool.py:196-209` 同样调用，含 cache hooks
- **真正剩余的重复**：CLI / MCP 两边在**装配 `FetchPipelineRequest`**（env 解析、strategy 构造、download_dir 决议）时仍是各写各的。改造范围比首轮估计的小很多——不是抽 pipeline，而是抽 request builder。
- 影响：首轮 R4 优先级可降低，"阶段 4"基本只剩 R11。

**[CHANGELOG 误报修正]** CHANGELOG.md 实际为 222 行，不是首轮报告的 22,199（误读了 ls 字节数）。**不需要拆分**。

### 6.2 新发现的重构机会

#### 🟡 中杠杆 / 低-中风险

**[R12] `formula/convert.py` (982 行) 后端选择 strategy 化**
- 现状：`SUPPORTED_BACKENDS` / `BENCHMARK_BACKENDS` / `AUTO_BACKENDS` 三个 tuple + `BACKEND_TEXMATH` 等 5 个常量散落在 lines 33-53
- `resolve_backend()` (lines 318-330) 用 if-else 链做字符串映射，新增 backend 要在 4 处改
- **方案**：定义 `Backend` 枚举/dataclass，每个 backend 自己声明能力（worker class、benchmark 资格、auto fallback 优先级），dispatcher 走 registry

**[R13] HTML-to-Markdown renderer 共用基类**
- 现状：HTML→Markdown 转换逻辑分布在：
  - `markdown/citations.py` (citation 清理)
  - `providers/_html_section_markdown.py` (603 行，section markdown)
  - `providers/_article_markdown_elsevier.py` (959)
  - `providers/_article_markdown_elsevier_document.py` (470)
  - `providers/_article_markdown_common.py` + `_article_markdown_math.py` + `_article_markdown_xml.py`
- 5 个 `_article_markdown_*.py` 文件加起来 ~3000 行，inline / block / formula / link 的处理模式重复
- **方案**：抽出 `markdown/html_renderer.py` 提供 `InlineRenderer` / `BlockRenderer` 基类，provider-specific 模块只声明覆盖项

**[R14] `metadata_types.py` 与 `metadata/` 子包合并**
- 现状：
  - `src/paper_fetch/metadata_types.py` (60 行)：TypedDict schemas（FulltextLink、ProviderMetadata、CrossrefMetadata、HtmlMetadata）
  - `src/paper_fetch/metadata/__init__.py` (8 行) + `metadata/crossref.py` (276 行)：实现
- 类型与实现各在一处，import 要走两个路径
- **方案**：把 `metadata_types.py` 移到 `metadata/types.py`，从 `metadata/__init__.py` re-export
- 风险低（仅 import path 变化）

#### 🟢 低杠杆 / 低风险

**[R15] `extraction/html/assets/download.py` (1367 行) 候选状态机统一**
- 现状：多个 `_AssetDownloadCandidate` / `_AssetDownloadAttempt` / `_AssetDownloadResolution` dataclass (lines 71-98) 重叠表示 figure / supplementary / fallback 三条流程
- HTTP 重试（line 640: `retry_on_rate_limit=True`）、cookie opener 管理（lines 382-415）、缓存全部混在一文件
- **方案**：抽出 `AssetPipeline` 状态机；cookie opener 独立为 `CookieAwareRequester`

**[R16] `vendor/flaresolverr/fetch_fulltext.reference.py` (2135 行) 是否还活？**
- 命名 `.reference.py` 暗示是文档/参考实现
- 需要 grep 确认是否被 import 或被 build 引用
- 若仅是历史快照，应移到 `docs/` 或删除（节省 vendor 体积，加快打包）

**[R17] `RuntimeContext` 中 Playwright 部分独立**
- `runtime.py:144-195` 浏览器生命周期可抽到 `PlaywrightContextManager`
- 与首轮 R4-RuntimeContext-拆分 配套；但本身可独立做
- 杠杆：让单元测试可 mock 浏览器，而不是 mock 整个 `RuntimeContext`

### 6.3 验证后认为**不需要重构**的领域

| 模块 | 原本担心 | 验证结论 |
|---|---|---|
| `resolve/query.py` (276 行) | 可能与 provider catalog 耦合 | 干净——只引用 metadata 和 publisher_identity |
| `http/` | 是否多个 transport 实现 | mixin 组合（CacheMixin + RetryMixin + BodyMixin）干净 |
| `paper_fetch_devtools/` | 与主包边界是否清晰 | 单向依赖主包，无反向耦合 |
| `models/` | 与 `metadata_types.py` 是否重复 | 不重复——models 是 domain object（dataclass），metadata_types 是 payload schema（TypedDict） |
| `workflow/` 8 个文件 | 是否过度切分 | 每个文件一个 stage（fulltext / metadata / rendering / routing / resolution），结构合理 |
| `CHANGELOG.md` | 22,199 行是否要拆 | 实际 222 行，是首轮误读 |

### 6.4 修订后的执行顺序

```
✅ 阶段 1（已完成）
   [R5] 测试基础设施         done
   [R7] 清理重构痕迹（部分）  done（noise_profile 残留已清）

✅ 阶段 2（已完成）
   [R1] *_rules.py 注册式    done

⏳ 阶段 3（推荐下一步，按风险递增）
   [R14] metadata 类型合并    低风险，1 个 PR
   [R8]  测试避免私有 import   配合 R1 后续清理
   [R10] Trace marker 集中    独立可做
   [R16] vendor reference 清理 grep 验证后决定
   [R12] formula backend strategy

⏳ 阶段 4（结构改造）
   [R6]  拆巨型测试文件
   [R3]  browser_workflow 三命名空间合并
   [R13] HTML-to-Markdown renderer 基类
   [R15] asset download 状态机统一

⏳ 阶段 5（主路径下沉，需 live smoke）
   [R9]  PDF / HTML quality 共享
   [R2]  fetch waterfall 模板下沉到 base.py

⏳ 阶段 6（入口与编排，已被 FetchPipeline 减负）
   [R4-修正]  抽 FetchPipelineRequest builder（小改造）
   [R17] Playwright 独立
   [R11] ArtifactStore + FetchCache 合并
```

### 6.5 总结：剩余项目

| 编号 | 标题 | 状态 |
|---|---|---|
| R1 | provider rules 注册式 | ✅ 已完成（Round 2） |
| R2 | fetch waterfall 模板下沉 | ✅ 已完成（Round 3h/3l；IEEE 与 Springer fetch-result recovery 已迁入 waterfall，provider-specific retrieval hook 保留） |
| R3 | browser_workflow 合并 | ✅ 已完成（Round 3d） |
| R4 | CLI/MCP 编排合一 | ✅ 已完成（Round 3f；request builder 已抽取） |
| R5 | 测试公共基础设施 | ✅ 已完成（Round 1） |
| R6 | 拆巨型测试文件 | ✅ 已完成（Round 3b） |
| R7 | 清理重构痕迹 | ✅ 已完成（Round 2/3a；noise_profile 与 IEEE alias 均已清理） |
| R8 | 测试避免私有 import | ✅ 已完成（Round 3a/3n；unit tests 改走 public facade/profile hook，golden corpus 暂留例外） |
| R9 | PDF / HTML quality 共享 | ✅ 已完成（Round 3h/3m；共享策略与 official PDF response helper 已接入，browser/auth/asset 差异保留在 provider 层） |
| R10 | Trace marker 集中 | ✅ 已完成（Round 3c） |
| R11 | ArtifactStore + FetchCache 合一 | ✅ 已完成（Round 3k；写盘 writer 统一，FetchCache 保留 sidecar 语义） |
| R12 | formula backend strategy | ✅ 已完成（Round 3e） |
| R13 | HTML-to-Markdown renderer 基类 | ✅ 已完成（Round 3i；薄 facade 已抽取，高风险 provider 差异保留） |
| R14 | metadata 类型合并 | ✅ 已完成（Round 3a） |
| R15 | asset download 状态机 | ✅ 已完成（Round 3j；状态机/requester 已拆出，resolver 语义保留） |
| R16 | vendor reference 文件清理 | ✅ 已完成（Round 3a） |
| R17 | Playwright 独立 | ✅ 已完成（Round 3g） |

**R1-R19 主要收口项已完成**。剩余可选项集中在 installer / 部署层 R20，以及 `tests/golden_corpus.py` 的 provider-private renderer 例外；这些不属于本轮“不新增 provider、不发布 PyPI”的主路径收口范围。

**本轮最终验证**：
- `PYTHONPATH=src python3 -m pytest tests/unit -q`（829 passed, 158 subtests passed）
- `PYTHONPATH=src python3 -m pytest tests/integration/test_architecture_closeout.py -q`（14 passed）
- `PYTHONPATH=src python3 -m compileall -q src/paper_fetch`

---

## 6.6 Installer / 部署层扫描（首轮附录 B 第 5 项）

### 资产盘点

| 文件 | 行数 | 角色 |
|---|---|---|
| `install.sh` | 140 | 在线开发安装 |
| `install-offline.sh` | 775 | Linux 离线安装（preset、shell rc 编辑、MCP 注册、smoke） |
| `install-offline.ps1` | 413 | Windows 离线安装 |
| `install-formula-tools.sh` | 50 | 公式渲染工具单独安装 |
| `installer/paper-fetch-skill.iss` | 63 | Inno Setup 脚本 |
| `scripts/install-claude-skill.sh` | 168 | Claude Code skill + MCP 注册 |
| `scripts/install-codex-skill.sh` | 163 | Codex skill + MCP 注册 |
| `scripts/build-offline-package.sh` | 324 | Linux 离线包构建 |
| `scripts/build-offline-package-windows.ps1` | 542 | Windows 离线包构建 |
| `scripts/verify-offline-package.sh` | 154 | 离线包冒烟检查 |
| `scripts/windows-installer-helper.ps1` | 425 | Inno Setup helper |
| `scripts/flaresolverr-{up,down,status}` × 2 平台 | ~106 | 6 个薄 shim，调用 `vendor/flaresolverr/` 实际脚本 |

总计 **~3300 行 shell / pwsh**。

### 关键发现

#### **[R18] `install-claude-skill.sh` 与 `install-codex-skill.sh` 80% 重复**（已用 diff 验证）

`diff` 显示两个文件主体是机械替换：
- `claude` ↔ `codex`、`~/.claude` ↔ `${CODEX_HOME:-$HOME/.codex}`
- 共同部分（约 130 行）：参数解析、SCOPE / UNINSTALL / REGISTER_MCP 标志、SKILL_DIR 计算、`log` / `warn` / `die` helpers、skill 文件复制
- 差异部分（约 30 行）：CLI 工具名、Claude 有 `-s scope` 概念而 Codex 没有、Codex 走 `run-codex-paper-fetch-mcp.sh` launcher

**方案**：抽 `scripts/_skill_install_common.sh`，两个 host 安装脚本只声明 host-specific 配置（CLI 名、scope 语义、launcher 路径），主流程走共享 lib。
- 杠杆：高（未来加 Cursor / Continue / Cline 等 host 边际成本接近零）
- 风险：低（纯 bash 重构）
- 范围：~150 行 → 共享 100 行 + 两个 host 各 30-40 行
- 收口结果：已新增 `scripts/_skill_install_common.sh`，`install-claude-skill.sh` / `install-codex-skill.sh` 只保留 host-specific MCP 注册、卸载和完成提示；共享 lib 负责参数解析、scope/skill dir 计算、包安装、skill bundle 复制与 Codex `openai.yaml` shim 写入。

#### **[R19] 跨平台配置应该 manifest 化**

以下常量同时出现在 `install-offline.sh`（lines 16-33）和 `install-offline.ps1`（同名常量），双写：
- `MANAGED_BEGIN / MANAGED_END` 标记字符串（用于 shell rc 编辑）
- `CODEX_MANAGED_BEGIN / CODEX_MANAGED_END`
- `SKILL_NAME = "paper-fetch-skill"`、`MCP_NAME = "paper-fetch"`
- `MCP_ENV_KEYS` 数组（10 项：`PYTHONUTF8` / `PAPER_FETCH_ENV_FILE` / `PLAYWRIGHT_BROWSERS_PATH` / `FLARESOLVERR_*` …）

任何一项要新增/改名时，必须改两边。`build-offline-package.{sh,ps1}` 还有第三份。

**方案**：`installer/manifest.json`（或 `pyproject.toml [tool.paper-fetch.installer]`）作为单一来源；`.sh` 用 Python 标准库 `json` 读，避免新增 `jq` 运行时前置条件；`.ps1` 用 `ConvertFrom-Json` 读，Inno Setup 继续由 Windows build 脚本传入 manifest 派生的 `SetupBaseName`。
- 杠杆：中（消除 3 处重复、降低改名出错率）
- 风险：低（不新增系统依赖；Linux uninstall 在无法读取 manifest 时保留旧默认常量作为清理 fallback）
- 收口结果：已新增 `installer/manifest.json`，集中维护 `skill.name`、`mcp.name`、`mcp.env_keys`、managed block markers 和离线包命名；Linux/Windows offline installer、Windows Inno helper、Linux/Windows offline build script 均改为读取 manifest，生成的 offline manifest 也记录 `installer/manifest.json` 组件。

#### **[R20] 把"平台无关"的安装步骤搬到 Python**（可选，更激进）

观察：`install-offline.sh` 的 775 行里，相当一部分是平台无关逻辑：
- 编辑 shell rc（写 PATH、写 env block、写 source 行）—— 用 `re` 即可
- 调用 `paper-fetch --help` smoke check
- 注册 MCP（调 `claude mcp add` 或 `codex mcp add`）
- 处理 `--reuse-env-file` 等参数

`install-offline.ps1` 的 413 行也大致是相同的流程（Windows 风格）。

**方案**：抽出 `paper_fetch.installer` Python 模块（已在 pyproject.toml `paper-fetch-install-formula-tools` 入口点占位），shell / pwsh 缩减为只做 bootstrap：
1. 解压 / 解 ABI bundle
2. 创建 venv、`pip install --no-index ./wheelhouse`
3. 调用 `python -m paper_fetch.installer apply --preset=...`

bash / pwsh 各从 ~600 行缩减到 ~100 行。
- 杠杆：最高（彻底消除跨平台双写）
- 风险：中-高（要做端到端离线测试）
- 范围：是阶段性大动作；可作为 PyPI 发布前的整理

#### 不需要重构的部分

- **`flaresolverr-{up,down,status}` × 2 平台** 已经是 thin shim（每个 16-20 行），合理
- **`installer/paper-fetch-skill.iss`** 63 行 Inno Setup 脚本，简单清楚
- **`install.sh`**（140 行，开发用）、**`install-formula-tools.sh`**（50 行）维持现状

### 6.6 总结（追加到 R 编号表）

| 编号 | 标题 | 风险 | 杠杆 | 状态 |
|---|---|---|---|---|
| R18 | `install-{claude,codex}-skill.sh` 抽共享 lib | 低 | 高 | ✅ 已完成 |
| R19 | installer 配置 manifest 化（MCP_ENV_KEYS / SKILL_NAME 等三处去重） | 低 | 中 | ✅ 已完成 |
| R20 | `paper_fetch.installer` Python 模块吸收平台无关步骤 | 中-高 | 高 | ⏳ 新增（PyPI 发布前考虑） |

**修订总数**：原 17 项 → 加 3 项 = **20 项**，其中 R1 / R5 / R18 / R19 已完成、R4 / R7 部分完成、剩余 **13 项**。

按 ROI 重新排，R18/R19 已收口；后续低风险高回报项可继续从 **[R14] + [R10] + [R16]** 中选择，R20 与 PyPI 发布节奏对齐。
- [x] Round 3: browser workflow + PDF/HTML strategies
  - 新增 `paper_fetch.providers.browser_workflow.shared/html_extraction/fetchers` canonical wrapper，browser workflow 内部 import 改到新命名空间，旧 `_browser_workflow_*` 与 `browser_workflow_fetchers` 保留兼容入口。
  - 抽出 `PdfFallbackStrategy` 与 `HtmlQualityAssessor` 轻量策略包装，并接入 Springer/Wiley/IEEE 与 Science/PNAS markdown availability 路径。
  - 增加 namespace/strategy contract tests，并同步架构文档中 browser workflow fetcher canonical owner。
- [x] Round 4: provider waterfall template
  - 新增 `paper_fetch.providers._payloads.build_provider_payload()`，统一 `RawFulltextPayload` / `ProviderContent` typed payload 构造，逐步替换 Elsevier/Springer/IEEE 的 HTML/PDF/abstract payload 重复块。
  - 扩展 `ProviderWaterfallState.failure()`、`last_failure()`、`source_markers()` 只读 helper，减少 provider 直接索引 waterfall 内部 failure 列表。
  - 保持现有 warnings、trace marker、metadata merge、Springer abstract-only recovery 与 IEEE abstract-only fallback 行为不变，配套新增 payload/waterfall helper tests。
- [x] Round 5: FetchPipeline for CLI/MCP
  - 新增 `paper_fetch.workflow.pipeline.FetchPipeline`、`FetchPipelineRequest`、cache hooks 和 Markdown save spec，统一 CLI/MCP 的 `RuntimeContext` 创建、service 调用、可选缓存 hook、Markdown 保存 hook 与关闭生命周期。
  - CLI `main()` 改为通过 pipeline 调用 `fetch_paper`，保留原有 `fetch_paper` monkeypatch 面、输出序列化、asset link rewrite、`--save-markdown` warning/source-trail 语义。
  - MCP `_fetch_paper_envelope()` 改为通过 pipeline cache hooks 复用 `FetchCache` 读写，保留 `prefer_cache`、`no_download`、async cancellation、progress/log bridge 与 `service_fetch_paper` 兼容 patch 面；`ArtifactStore` 与 `FetchCache` 写盘边界保持分离。
- [x] Round 6: R18/R19 installer 收口
  - 新增 `scripts/_skill_install_common.sh`，把 Claude/Codex skill installer 的参数解析、安装、复制和通用提示收敛到共享 lib；host 脚本只保留 MCP CLI 差异。
  - 新增 `installer/manifest.json`，集中维护 skill/MCP 名称、MCP env key 顺序、managed block marker 和 offline package 命名；Linux/Windows installer 与 build script 改为读取 manifest。
  - 更新 installer smoke/offline package tests，覆盖共享 installer lib、manifest 单一来源和动态 Codex MCP table 写入；`PYTHONPATH=src python3 -m pytest tests/unit -q` 通过。

## Closeout note - 2026-05-08

R1 尾巴已收口：provider HTML availability signal 已从 `quality/html_profiles.py` 的 `PROVIDER_SIGNAL_HANDLERS` dict 迁入 `extraction/html/provider_rules.py` 的 `ProviderHtmlRules` 注册项；signal 实现独立到 `quality/html_signals.py`，保留 `html_profiles.py` re-export 兼容入口。
