# 架构优化全量执行方案

Date: 2026-05-22

本文不是当前架构基线;当前系统事实以 [`docs/architecture/overview.md`](docs/architecture/overview.md) 为准。本文的用途是把已发现的架构优化点改写成一个可以交给 goal/agent 一次性全量执行的任务书:执行者不需要再做方案取舍,只需要按顺序实施、验证、记录结果。

## 全量 Goal

在不改变 public CLI/MCP payload wire shape、不改变 provider 抓取语义的前提下,完成以下架构收紧:

1. 修复 MDPI 在架构/部署文档中的 baseline 漂移。
2. 先修共享 typing 边界,再把第一个真实 provider 批次纳入 mypy。
3. 将可静态表达的 banned import 规则前移到 ruff,保留路径条件型架构测试。
4. 加入 coverage baseline 能力,不设置覆盖率阈值。
5. 按零行为变化原则拆分三个过大的 provider HTML 模块。
6. 区分本地 ignored 杂物和 tracked fixture 体积问题,生成可执行的 fixture 体积治理基线。

## 全局约束

- 默认使用简体中文更新文档。
- 不触发 GitHub CI;只运行本地命令。
- 常规 unit/integration 验证复用 `pyproject.toml` 的 pytest 并行配置,不要加 `-n 0`。只有 live 测试或排查顺序问题才串行。
- provider 拆分阶段必须零行为变化:只搬代码、改 import、保留 facade,不得同时修改 fallback、availability、asset 下载或 Markdown 输出语义。
- 机械格式化、typing 修复、provider 拆分、fixture 迁移不要混在同一个提交/变更集中。如果一次性 goal 执行,也要按本文阶段输出独立 diff 摘要。
- 如果某阶段验证失败,先修该阶段,不要继续扩大修改面。

## 当前证据快照

这些数据来自当前分支的非破坏性检查,执行前应快速复核是否仍成立。

- mypy 当前只覆盖 `src/paper_fetch/models`、`src/paper_fetch/workflow`、`providers/base.py`、`providers/protocols.py`、`mcp/schemas.py`,约 19 个核心文件/目录;`src/paper_fetch` 约 220 个 Python 文件、58,448 行。
- `copernicus` 直接纳入 mypy 当前会暴露 13 个错误;`arxiv` 全套模块当前会暴露 26 个错误。
- `docs/architecture/overview.md` 当前遗漏 MDPI,但 `README.md`、`docs/providers.md` 和 `src/paper_fetch/mcp/_instructions.py` 已把 MDPI 当作正式 provider。
- 大 provider HTML 模块体量:
  - `src/paper_fetch/providers/_springer_html.py`:1642 行、72 个函数。
  - `src/paper_fetch/providers/_mdpi_html.py`:1522 行、77 个函数、1 个 class。
  - `src/paper_fetch/providers/_ams_html.py`:1095 行、68 个函数。
- `python3 -m ruff check --select E,F,I,UP,B,TID . --statistics` 会报约 7633 个问题,其中 `E501` 5581 个、`TID252` 1502 个;不能一次性全量开启。
- 测试约 52,404 行,源码约 58,448 行;dev deps 当前没有 `pytest-cov`。
- `tests/fixtures` 约 102M、tracked fixture 约 340 个;`legacy/flaresolverr` 和 `build` 当前本地存在但 `git ls-files legacy build` 为 0,属于 ignored/local 问题。

## 执行阶段图

按下列顺序执行。后续阶段依赖前置阶段提供的护栏和事实基线。

| 阶段 | 目标 | 主要文件 | 必须验证 |
| --- | --- | --- | --- |
| 0 | 预检与工作树保护 | 无代码改动 | `git status --short`、目标命令复核 |
| 1 | 修复 MDPI 文档漂移 | `docs/architecture/overview.md`,`docs/deployment.md`,`docs/README.md` | docs grep + extraction rules validator |
| 2 | 收紧 typing 并纳入 Copernicus | typing facade、models builder、`providers/copernicus.py`,`pyproject.toml` | targeted mypy + full mypy + copernicus unit |
| 3 | ruff banned import 前移 | `pyproject.toml`,`tests/unit/test_import_boundaries.py` | `ruff check .` + import boundary tests |
| 4 | coverage baseline | `pyproject.toml`,`.gitignore`,`docs/deployment.md` 或测试文档 | unit coverage baseline command |
| 5 | provider HTML 模块拆分 | `_ams_html.py`,`_mdpi_html.py`,`_springer_html.py` 及新 helper 模块 | 对应 provider tests + golden adapter tests |
| 6 | fixture/杂物治理基线 | docs 或新增 devtools/report 文件 | fixture size report + no tracked cleanup diff |
| 7 | 全量收口验证 | 无新增实现 | ruff, mypy, unit, devtools, validator, integration |

---

## 阶段 0 - 预检与工作树保护

### 目的

确认执行者不会覆盖用户已有改动,并复核本文件中的证据是否过期。

### 执行步骤

1. 运行:

   ```bash
   git status --short
   ```

2. 如果存在非本文任务相关的 tracked 修改,不要 revert;记录并避开。只有确认是本次执行者自己产生的错误改动时才恢复。
3. 复核关键事实:

   ```bash
   PYTHONPATH=src python3 -m mypy src/paper_fetch/providers/copernicus.py src/paper_fetch/providers/_article_markdown_copernicus.py --show-error-codes
   PYTHONPATH=src python3 -m mypy src/paper_fetch/providers/arxiv.py src/paper_fetch/providers/_arxiv_*.py --show-error-codes
   python3 -m ruff check --select E,F,I,UP,B,TID . --statistics
   find tests/fixtures -type f -printf '%s %p\n' 2>/dev/null | sort -nr | sed -n '1,40p'
   ```

### 继续条件

- 能明确区分本次任务改动和已有用户改动。
- 如果命令结果与本文差异较大,先更新本文证据或在最终结果中说明。

---

## 阶段 1 - 修复 MDPI 文档漂移

### 问题

MDPI 已是正式 provider,但 architecture/deployment/docs README 仍有遗漏。`docs/architecture/overview.md` 是系统架构 baseline,遗漏一个 1500+ 行 browser provider 会误导新增 provider 和 runtime 判断。

### 精确改动

1. 更新 `docs/architecture/overview.md`:
   - Extraction 阶段映射的 `provider-html-or-xml-extraction` owner 列表加入 `_mdpi_html`。
   - metadata probe 描述中,不做 publisher metadata probe 的 provider 列表加入 `mdpi`。
   - provider source 示例加入 `mdpi_html` / `mdpi_pdf`。
   - browser workflow 共享 provider 列表改为 Wiley / Science / PNAS / AMS / MDPI。
   - fallback 语义写明 `mdpi` 与 `arxiv` / `copernicus` / `elsevier` 一样,主链不可用后进入 metadata-only fallback。
   - official provider 不走通用 HTML fallback 的列表加入 `mdpi`。
2. 更新 `docs/deployment.md`:
   - 所有 Wiley / Science / PNAS / AMS browser runtime 列表同步加入 MDPI。
   - 章节标题加入 MDPI。
3. 更新 `docs/README.md`:
   - browser workflow 文档摘要加入 MDPI。
   - 若有 HTML asset/challenge 链路 provider 列表,同步加入 MDPI。

### 禁止事项

- 不改 provider 代码。
- 不改 MCP instructions,除非复核发现它与 `docs/providers.md` 已不一致。

### 验证

```bash
rg -n 'mdpi|MDPI' docs/architecture/overview.md docs/deployment.md docs/README.md
python3 scripts/validate_extraction_rules.py --ci
```

---

## 阶段 2 - 收紧 typing 并纳入 Copernicus

### 问题

typed contract 定义在 models/workflow/base/protocols,但真实 provider 产出处不在 mypy 覆盖内。直接纳入 provider 会失败,必须先修共享 typing 边界。

### 已知错误类别

- `paper_fetch.extraction.html.assets` facade 动态导出导致 mypy 看不到 `FIGURE_KIND`、`SUPPLEMENTARY_KIND`、`html_asset_identity_key`、`split_body_and_supplementary_assets`。
- `ProviderClient` 基类方法接收 `Mapping[str, Any]`,provider 子类使用 `ProviderMetadata` 作为参数类型导致 override 错误。
- `merge_html_metadata()` 期望 `ProviderMetadata | None`,调用方传普通 `dict[str, Any]`。
- `article_from_markdown()` / `metadata_only_article()` 期望 `SourceKind`,provider 局部 `source` 变量被推成普通 `str`。
- builder/helper 中 `list[Mapping[str, Any]]` 参数导致 `list[dict[str, Any]]` 传参报不变性错误。

### 精确改动

1. facade typing:
   - 在 `src/paper_fetch/extraction/html/assets/__init__.py` 为动态导出的 public API 添加 mypy 可见显式 alias,或补同名 `.pyi`。
   - 不改变运行时 `__all__` 语义。
2. builder/helper 协变:
   - 将只读资产参数从 `list[Mapping[str, Any]]` 放宽为 `Sequence[Mapping[str, Any]]`。
   - 优先改 public builder 入参,不要在 provider 侧到处 cast list。
3. provider 方法签名:
   - `CopernicusClient.fetch_raw_fulltext()`、`download_related_assets()`、`to_article_model()` 的 `metadata` 参数保持与基类兼容:`Mapping[str, Any]`。
   - 内部需要 TypedDict 时,在最小范围内 normalize/cast。
4. SourceKind:
   - `source` 变量标注为 `SourceKind`,或用明确 Literal 分支调用 builder。
5. mypy 配置:
   - 只有 targeted mypy 清零后,才把以下文件加入 `pyproject.toml [tool.mypy].files`:
     - `src/paper_fetch/providers/copernicus.py`
     - `src/paper_fetch/providers/_article_markdown_copernicus.py`

### 禁止事项

- 禁止文件级 `# type: ignore`、`ignore_errors = true` 或扩大 `ignore_missing_imports` 来掩盖 provider 错误。
- 不在此阶段纳入 arXiv;arXiv 是下一批。
- 不改 Copernicus 抓取、fallback、asset 下载语义。

### 验证

```bash
PYTHONPATH=src python3 -m mypy src/paper_fetch/providers/copernicus.py src/paper_fetch/providers/_article_markdown_copernicus.py --show-error-codes
PYTHONPATH=src python3 -m mypy
PYTHONPATH=src python3 -m pytest tests/unit/test_copernicus_provider.py -q
```

### 后续批次记录

Copernicus 完成后,新增 backlog 条目推进 arXiv。arXiv 当前错误分布在 `_arxiv_html.py`、`_arxiv_authors.py`、`_arxiv_metadata.py`、`_arxiv_assets.py`、`_arxiv_atom.py`、`arxiv.py`,不应混入 Copernicus 批次。

---

## 阶段 3 - ruff banned import 前移

### 问题

当前 ruff 只配置 target/src,规则集弱;但一次性开启 `E,F,I,UP,B,TID` 会制造 7000+ 条历史问题。需要先迁移高信号、低噪声的 banned import。

### 精确改动

1. `pyproject.toml`:
   - 在 `[tool.ruff.lint]` 显式配置当前可通过的基础规则。
   - 添加 `TID251` 所需配置。
   - 不启用 `E501` 和 `TID252` 作为门禁。
2. `flake8-tidy-imports.banned-api` 至少覆盖:
   - `paper_fetch.providers._article_markdown`
   - `paper_fetch.providers._html_access_signals`
   - `paper_fetch.providers._html_availability`
   - `paper_fetch.providers._html_citations`
   - `paper_fetch.providers._html_semantics`
   - `paper_fetch.providers._html_tables`
   - `paper_fetch.providers._html_text`
   - `paper_fetch.providers._language_filter`
   - `paper_fetch.providers._atypon_browser_workflow`
   - `paper_fetch.providers._atypon_browser_workflow_html`
   - `paper_fetch.providers.html_assets`
   - `paper_fetch.providers.pnas_html`
   - `paper_fetch.providers.science_html`
   - `paper_fetch.providers.springer_html`
   - `paper_fetch.providers.wiley_html`
   - `paper_fetch.extraction.html._assets`
   - `paper_fetch.resolve.crossref`
3. `tests/unit/test_import_boundaries.py`:
   - 删除"source/tests 禁止 import 已删除 compat module"这类扁平 banned import AST 测试。
   - 保留:
     - provider-neutral 层禁止 import `paper_fetch.providers._*`。
     - HTML asset 模块禁止 import public `paper_fetch.models`。
     - arxiv provider 禁止导入 PyPI `arxiv` 包。

### 机械清理策略

如果开启 `I/UP/B` 后 ruff 仍报大量自动修复项,拆成单独机械清理阶段:

```bash
python3 -m ruff check . --fix
```

该机械清理阶段不得夹带行为改动。执行后必须人工查看 diff,确认只有 import 排序、pyupgrade、bugbear 自动修复。

### 禁止事项

- 不把 `E501`、`TID252` 作为本阶段门禁。
- 不为了过 ruff 添加大面积 `noqa`。
- 不删除路径条件型架构测试。

### 验证

```bash
python3 -m ruff check .
PYTHONPATH=src python3 -m pytest tests/unit/test_import_boundaries.py -q
```

---

## 阶段 4 - coverage baseline

### 问题

测试规模大,但没有 coverage baseline,无法知道 provider 重构前哪些分支受保护。

### 精确改动

1. `pyproject.toml`:
   - dev dependency 增加 `pytest-cov`。
2. `.gitignore`:
   - 确认已有 `.coverage`、`htmlcov/`。
   - 补充 `coverage.xml`。
3. 文档:
   - 在 `docs/deployment.md` 或测试/开发相关文档中加入 coverage baseline 命令。
   - 明确第一阶段不设阈值,只生成观察报告。

### 推荐命令

```bash
PYTHONPATH=src python3 -m pytest tests/unit -q --cov=paper_fetch --cov-report=term-missing --cov-report=xml
```

### 禁止事项

- 不设置 `--cov-fail-under`。
- 不把默认 unit/integration 命令改成串行。
- 不把 coverage 作为 live/browser 测试前置条件。

### 验证

- coverage 命令能完成并生成 terminal report。
- `coverage.xml` 不进入 git。
- 默认 `PYTHONPATH=src python3 -m pytest tests/unit -q` 仍使用并行配置。

---

## 阶段 5 - provider HTML 模块拆分

### 问题

`_springer_html.py`、`_mdpi_html.py`、`_ams_html.py` 是单文件职责过载。项目已有 arXiv/IEEE 的职责拆分范式,应按 provider 逐个迁移。

### 拆分顺序

1. AMS:最小,hook 集中,用于验证拆分模式。
2. MDPI:display object、formula、supplementary 复杂,放第二。
3. Springer:最大、测试引用最多,放最后。

### 通用拆分规则

每个 provider 使用相同职责边界,文件名按实际 provider 替换:

- `_provider_authors.py`:author extraction pipeline、author cleanup。
- `_provider_references.py`:reference extraction / numbering / DOI cleanup。
- `_provider_assets.py`:body/supplementary/formula/table asset discovery。
- `_provider_markdown.py`:Markdown normalization / postprocess。
- `_provider_dom.py`:container selection、DOM cleanup、renderer hook。
- 原 `_provider_html.py`:保留 compatibility facade,re-export 旧入口。

### AMS 执行卡

1. 从 `_ams_html.py` 搬迁 author extraction 到 `_ams_authors.py`。
2. 搬迁 references 到 `_ams_references.py`。
3. 搬迁 asset extraction 到 `_ams_assets.py`。
4. 搬迁 markdown/dom normalization 到 `_ams_markdown.py` / `_ams_dom.py`。
5. `_ams_html.py` re-export 当前 `ams.py`、tests、`provider_rules` 仍依赖的函数。
6. 更新 `docs/extraction-rules.md` Owner:若测试仍走 facade,Owner 可写 facade + 新 canonical owner。

验证:

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_ams_provider.py tests/unit/test_author_pipeline_per_provider.py tests/unit/test_provider_typed_hooks.py -q
```

### MDPI 执行卡

1. 保持 `src/paper_fetch/providers/mdpi.py` 对 `_mdpi_html` facade 的 import 不变。
2. 先拆 references 和 keywords,再拆 formula,再拆 display object/assets,最后拆 markdown/dom。
3. `_mdpi_html.py` 必须继续导出:
   - `extract_markdown`
   - `extract_authors`
   - `extract_references`
   - `extract_keywords`
   - `extract_scoped_html_assets`
   - `extract_asset_html_scopes`
   - `mdpi_pdf_url_from_landing_url`
   - `mark_inline_assets`
   - `MDPI_*` rule constants
4. `tests/golden_corpus.py` 当前调用 `_article_container_html()`;拆分后要么保留 facade re-export,要么同步测试到新 owner。

验证:

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_mdpi_provider.py tests/unit/test_author_pipeline_per_provider.py tests/unit/test_golden_corpus_adapters.py -q
```

### Springer 执行卡

1. 保持 `src/paper_fetch/providers/springer.py` 对 `_springer_html` facade 的 import 不变。
2. 先拆 metadata/authors,再拆 references,再拆 assets/table image,最后拆 markdown/dom cleanup。
3. `_springer_html.py` 必须继续导出当前 tests 和 `springer.py` 使用的函数,尤其:
   - `decode_html`
   - `parse_html_metadata`
   - `merge_html_metadata`
   - `extract_html_payload`
   - `extract_asset_html_scopes`
   - `extract_source_data_html_scope`
   - `extract_springer_table_image_url`
   - `download_assets_for_springer`
   - `extract_authors`
   - `normalize_display_authors`
   - `clean_markdown`
4. 更新 `docs/extraction-rules.md` Owner 引用。

验证:

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_springer_html_regressions.py tests/unit/test_springer_html_tables.py tests/unit/test_provider_waterfalls.py tests/unit/test_author_pipeline_per_provider.py -q
```

### 禁止事项

- 不改 provider public source 值。
- 不改 waterfall 顺序。
- 不改 asset profile 语义。
- 不改 Markdown 输出规范。
- 不删除 facade,除非所有调用方和 docs owner 已迁完且完整测试通过。

### 阶段验收

- 三个 provider 拆分后,对应旧 facade 文件行数明显下降。
- 所有 provider 专项测试通过。
- `docs/extraction-rules.md` owner 与新模块结构一致。

---

## 阶段 6 - fixture/杂物治理基线

### 问题

本地 ignored 杂物和 tracked fixture 体积是两个问题,不能混在一起处理。

### 精确改动

1. 本地 ignored 杂物:
   - 运行 dry-run:

     ```bash
     scripts/clean-local-artifacts.sh --dry-run
     ```

   - 只清理 `git check-ignore` 确认为 ignored 的目标。
   - 清理不应产生 tracked diff。
2. tracked fixture 体积报告:
   - 新增或更新一个文档化报告,建议路径为 `docs/architecture/fixture-size-baseline.md` 或 `docs/testing/fixture-size-baseline.md`。
   - 报告至少包含:
     - top 40 fixture 文件。
     - size、path、文件类型、推断 provider。
     - 是否 tracked。
     - 已知引用测试。
     - 建议动作:`keep`、`compress-evaluate`、`minimize-evaluate`、`lfs-evaluate`。
3. 不立即迁移到 git-lfs。
4. 不删除 golden fixture,除非有对应测试替代和明确验收。

### 推荐命令

```bash
find tests/fixtures -type f -printf '%s %p\n' 2>/dev/null | sort -nr | sed -n '1,80p'
du -sh tests/fixtures legacy build 2>/dev/null || true
git ls-files tests/fixtures | wc -l
git ls-files legacy build | wc -l
```

### 验证

- `git status --short` 中没有因清理 ignored 目录产生 tracked diff。
- fixture size baseline 文档存在且可复核。
- 不改变现有 fixture 驱动测试读取路径。

---

## 阶段 7 - 全量收口验证

阶段 1-6 完成后运行:

```bash
python3 -m ruff check .
PYTHONPATH=src python3 -m mypy
PYTHONPATH=src python3 -m pytest tests/unit -q
PYTHONPATH=src python3 -m pytest tests/devtools -q
python3 scripts/validate_extraction_rules.py --ci
PYTHONPATH=src python3 -m pytest tests/integration -q
```

如果只做文档阶段,至少运行:

```bash
python3 scripts/validate_extraction_rules.py --ci
```

## 完成定义

全量 goal 只有同时满足以下条件才算完成:

- MDPI 在 architecture/deployment/docs README 中被完整纳入 provider baseline。
- `PYTHONPATH=src python3 -m mypy` 通过,且 `copernicus` provider 文件已纳入 mypy `files=`。
- ruff 能表达 removed compat module banned imports,同时路径条件型架构测试仍保留。
- coverage baseline 命令可运行,但没有 coverage fail-under 阈值。
- AMS/MDPI/Springer 至少完成按职责拆分并保留旧 facade,对应 provider tests 通过。
- fixture 体积问题有可复核 baseline,本地 ignored 清理与 tracked fixture 治理被明确分开。
- 最终验证命令结果被记录在执行总结中。

## 如果全量执行中途受阻

默认策略不是停止,而是自动诊断、修复、重跑验证并继续。只有满足"退出条件"时才停止。

### 自动恢复总规则

1. 每个阶段开始前记录:
   - `git status --short`
   - 本阶段允许触碰的文件/目录
   - 本阶段验证命令
2. 阶段验证失败时,执行者必须先读取失败输出,把失败归类为"本阶段范围内可修复问题"或"越界问题"。
3. 对本阶段范围内可修复问题,直接修复并重跑本阶段验证命令。
4. 同一阶段最多连续自动修复 3 轮。3 轮后仍失败,进入退出条件。
5. 自动修复不得扩大到阶段允许文件之外;如果必须跨阶段修改,先确认它属于前置共享契约问题,并在执行总结中记录原因。
6. 不要用 blanket `type: ignore`、文件级 `noqa`、删除测试、降低断言、跳过测试来"恢复"。

### 各阶段自动恢复策略

- mypy 阶段失败:
  - 先按错误码分类:`attr-defined` 优先补 facade typing / `.pyi`;`override` 优先放宽子类签名到基类兼容;`arg-type` 优先修 helper 入参协变或局部 cast;`return-value` 优先规范 TypedDict 返回。
  - 修复后先跑 targeted mypy,再跑 full mypy。
  - 如果错误扩散到非 Copernicus/arXiv 批次 provider,不要继续扩大纳入范围;只修共享 typing 边界或记录为后续批次。
- ruff 阶段失败:
  - 如果是 banned import,改 import 到 canonical owner。
  - 如果是自动修复类 import/order/pyupgrade,可运行 `python3 -m ruff check . --fix`,但必须随后人工检查 diff,确认没有行为改动。
  - 如果是 `E501` 或 `TID252` 等历史噪声,不要通过大面积 `noqa` 解决;应调整本阶段规则配置,保持这些规则不作为门禁。
- coverage 阶段失败:
  - 如果缺少依赖,确认 `pytest-cov` 已在 dev deps 中。
  - 如果生成了 `coverage.xml` 或 `.coverage`,确认被 `.gitignore` 覆盖。
  - 如果 unit 测试本身失败,按失败测试修复;不要降低 coverage 命令或改串行。
- provider 拆分阶段失败:
  - 先判断是 import/export 断裂、docs owner 漂移、还是行为回归。
  - import/export 断裂优先在旧 `_provider_html.py` facade 补 re-export,保持调用方稳定。
  - 行为回归优先恢复搬迁前逻辑,不得在拆分阶段顺手重写算法。
  - 若某个 provider 拆分 3 轮仍无法恢复专项测试,保留已通过 provider 的拆分,把失败 provider 回到最近专项测试通过的 facade 状态,记录剩余阻塞后继续后续非依赖阶段。
- fixture/杂物阶段失败:
  - 如果清理 ignored 目录产生 tracked diff,立即恢复 tracked diff,重新只做报告。
  - 如果 fixture 引用无法自动识别,报告中标记 `reference-unknown`,不要删除或迁移 fixture。

### 退出条件

只有以下情况允许停止全量执行:

- 同一阶段自动修复 3 轮后验证仍失败。
- 修复需要改变 public CLI/MCP payload wire shape 或 provider 抓取语义。
- 修复需要恢复或覆盖阶段开始前已存在的用户改动。
- 外部依赖不可用且无法用本地代码或测试 fixture 继续验证。

退出时必须记录:

- 当前阶段和失败命令。
- 已尝试的自动修复。
- 剩余失败的文件、行号、错误码或测试名。
- 已回到的最近安全状态,以及哪些阶段已经完成。
