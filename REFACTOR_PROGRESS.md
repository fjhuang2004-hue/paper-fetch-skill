## Provider Onboarding 标准化与 AI 接入基础设施

### 基线
- 起始 commit: 6d48d69（T22 末端）
- 基线测试: 1138 passed, 264 subtests passed
- 工作分支: refactor/provider-onboarding-standardization

### 阶段 A：标准化基础设施（S1-S10）
- [x] S1: 消除 provider_rules.py hook wrapper thunk
  - commit: 405f9a8
  - 摘要: `provider_rules.py` 删除 `_X_hooks_module()` / provider hook wrapper / `importlib` 桥接，规则表按访问时构建并直接绑定 provider-owned hook 函数。
  - 验收: S1 grep 为空，`provider_rules.py` 778 行，`test_provider_html_rules_shape.py` / `test_html_profiles.py` / `test_provider_typed_hooks.py` / import-boundary 相关测试通过；全量 unit 仍为 1138 passed + 264 subtests。
- [x] S2: 数据化 provider availability / blocking signals
  - commit: ce92370（另有 5b3fe71 刷新进入本阶段前已 stale 的 9 个 AMS golden summary）
  - 摘要: `AvailabilityPolicy` 迁移到 datalayer/text-marker/override typed 字段，删除 provider 专属 signal/override callable 与 `html_profiles` façade，provider 和 browser-workflow callsite 改走 typed evaluator。
  - 验收: S2 grep 为空，`html_signals.py` 331 行，S2 局部 pytest 43 passed + 6 subtests；全量 unit 1143 passed + 264 subtests；full golden corpus 174 passed。
- [x] S3: 引入 ProviderBundle
  - commit: bcd8c0f
  - 摘要: 新增 `ProviderBundle` 注册表，将 provider catalog、HTML rules、source map、asset retry 等 provider-owned 配置收敛到各 provider entry module，`provider_catalog.py` / `provider_rules.py` 改为按 bundle discovery 懒加载。
  - 验收: S3 grep 为空，provider bundle 注册文件数 10，`provider_catalog.py` 280 行；`test_provider_catalog.py` / `test_provider_bundle_registration.py` 38 passed + 16 subtests；全量 unit 1156 passed + 264 subtests；full golden corpus 174 passed。
- [x] S4: onboarding 完整性 lint
  - commit: 34d44aa
  - 摘要: `ProviderSpec` 增加 `html_capable`，`AvailabilityPolicy` 增加显式 `no_signals` opt-out，新增 bundle 完整性和中心 provider 分支 lint。
  - 验收: S4 新增测试 9 passed；`validate_extraction_rules.py` 通过；全量 unit 1166 passed + 264 subtests；S3 catalog/rules grep 仍为空且 `provider_catalog.py` 保持 280 行。
- [x] S5: Scaffold 脚本
  - commit: 0915df5
  - 摘要: 新增 `scripts/scaffold_provider.py`，支持 provider name/DOI/source/fulltext-client/html-capable/output-dir，生成 provider bundle 骨架、fixture 占位、manifest pending 条目和 starter unit test。
  - 验收: `scaffold_provider.py --help` 通过；`test_scaffold_provider.py` 5 passed；`validate_extraction_rules.py` 通过；全量 unit 1171 passed + 264 subtests。
- [x] S6: 文档对齐 + checklist 化
  - commit: dfbf682
  - 摘要: `docs/provider-development.md` 改为 bundle/scaffold 流程与 PR checklist，删除手改 catalog/rules 的旧接入说明；`target-architecture.md` 记录 ProviderBundle discovery 与 `_registry.py` 职责。
  - 验收: S6 docs grep 通过（旧 hook/signal 函数无命中，scaffold/checklist/bundle 文档存在）；`validate_extraction_rules.py` 通过；全量 unit 1171 passed + 264 subtests。
- [x] S7: Fixture 录制 + expected.json 工具
  - commit: 8e5becf
  - 摘要: 新增 `capture_fixture.py` 与 `snapshot_expected.py`，支持 DOI replay fixture 录制、manifest pending 条目、expected.json snapshot/review；quickstart 与 provider 开发文档改用工具流程。
  - 验收: S7 文件/help/sanity 命令通过；`test_capture_fixture.py` / `test_snapshot_expected.py` 7 passed；`validate_extraction_rules.py` 通过；全量 unit 1178 passed + 264 subtests。
- [x] S8: ProviderClient / Waterfall 脚手架
  - commit: af61293
  - 摘要: 新增 `WaterfallStep` 声明式 step 名称并保留 `ProviderWaterfallStep` 兼容 alias；`ProviderClient.fetch_raw_fulltext()` 默认运行 class-level `waterfall_steps`，空 steps 明确提示覆盖方法或声明 steps；scaffold 生成 fulltext client 时写入 `waterfall_steps` 与 provider-owned fallback step 占位函数。
  - 验收: S8 grep 通过；scaffold probe 生成 `waterfall_steps` 与 `ProviderFailure(NOT_SUPPORTED, ...)` 占位 step；`test_waterfall_default_fetch.py` / `test_scaffold_provider.py` 8 passed；`validate_extraction_rules.py` 通过；ruff touched Python files 通过；全量 unit 1181 passed + 264 subtests。
- [x] S9: 重构对齐 grep lint 测试
  - commit: 7fe6c2a
  - 摘要: 新增 owner-reuse grep pattern helper 与 `test_provider_owner_reuse.py`，从 provider bundle 枚举 provider，扫描 `X.py` / `_X_html.py` 中附录 B 的重复 owner helper；仅允许同行或上一行带非空 `# OWNER_REUSE_EXCEPTION: ...` 的命中。
  - 验收: S9 文件存在检查通过；`test_provider_owner_reuse.py` 6 passed；`validate_extraction_rules.py` 通过；ruff 新增测试文件通过；全量 unit 1187 passed + 264 subtests。
- [x] S10: docs 占位生成 + probe_status 默认实现
  - commit: a20c28b
  - 摘要: `ProviderSpec` 增加 `env_requirements` / `requires_playwright` / `requires_flaresolverr`，`ProviderClient.probe_status()` 默认基于 catalog 做轻量本地状态检查；`scaffold_provider.py` 默认同步 `docs/providers.md` / `docs/extraction-rules.md` / `CHANGELOG.md` 占位并支持 `--no-sync-docs`；三份 docs 增加 `SCAFFOLD` marker。
  - 验收: S10 grep 和 scaffold `/tmp` 探针通过；`test_probe_status_default.py` / `test_scaffold_docs_sync.py` / `test_scaffold_provider.py` 12 passed；catalog/bundle 相关目标测试 57 passed + 16 subtests；`validate_extraction_rules.py` 通过；`python3 -m ruff check .` 通过；全量 unit 1193 passed + 264 subtests。

### 阶段 B：AI 接入最小闭环（S11A-S13）
- [x] S11A: AI Manifest Discovery
  - commit: 64a96f5
  - 摘要: 新增 `onboard_from_manifests.py` discovery/start dry-run 入口，生成 `discover-manifest` task DAG 与 worker brief；补齐 discovery brief 机器合约文档和 `agent-task-brief.md`；测试覆盖 brief 搜索要求、allow/deny 写入范围、无 LLM SDK 依赖和 manifest replay 路径。
  - 验收: S11A 文件/help/dry-run grep 通过；`test_manifest_discovery_brief.py` / `test_onboard_from_manifests.py` 7 passed；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1200 passed + 264 subtests。
- [x] S11: ProviderManifest schema + manifests
  - commit: ac4b669
  - 摘要: 新增 AI onboarding README、ProviderManifest JSON Schema/字段说明、`known-providers.yml`，以及 `arxiv` / `copernicus` / `wiley` 三份现有 provider replay manifest；dev 依赖加入 `jsonschema`，测试校验 schema 合法、manifest 样例、禁用占位值和 known-providers 与 runtime catalog 同步。
  - 验收: S11 文件/禁词 grep 通过；`test_provider_manifest_schema.py` / `test_known_providers_sync.py` 5 passed；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1205 passed + 264 subtests。
- [x] S12: scaffold --from-manifest
  - commit: b246ab7
  - 摘要: `scaffold_provider.py` 增加 `--from-manifest`，先按 ProviderManifest schema 校验输入，再从 manifest 生成 ProviderSpec routing/probe/asset 占位、manifest 顺序的 `waterfall_steps`、多 DOI fixture `.gitkeep`、capture command 清单、docs/changelog 占位和 JSON artifact summary；legacy flags 保留为 fallback，但禁止与 manifest 输入混用。
  - 验收: S12 help/gitreps 通过；真实 `arxiv.yml` `/tmp` scaffold probe 输出 JSON summary；`test_scaffold_provider_from_manifest.py` / `test_scaffold_provider.py` 18 passed；S11 manifest/schema/known-provider 回归 7 passed；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1217 passed + 264 subtests。
- [ ] S13: manifest ↔ bundle 同步 lint（含 sync-back）

### 阶段 C：批量调度与自动恢复（S14-S16）
- [ ] S14: Coordinator 编排脚本（单 provider 串行 + coding agent CLI）
- [ ] S15: capture_fixture 非交互化与 retry
- [ ] S16: 工具链结构化错误输出

### 阶段 D：文档权威源切换（S17）
- [ ] S17: AI onboarding 文档切主权威源 + 旧 docs 降级 / drift lint
