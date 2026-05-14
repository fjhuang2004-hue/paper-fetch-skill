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
  - 验收: S12 help/git grep 通过；真实 `arxiv.yml` `/tmp` scaffold probe 输出 JSON summary；`test_scaffold_provider_from_manifest.py` / `test_scaffold_provider.py` 18 passed；S11 manifest/schema/known-provider 回归 7 passed；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1217 passed + 264 subtests。
- [x] S13: manifest ↔ bundle 同步 lint（含 sync-back）
  - commit: 86d41fc
  - 摘要: 新增 manifest/bundle sync helper、`test_manifest_bundle_sync.py`、`manifest_sync_back.py` 和 round-trip 测试，按 known-providers 中的 manifest 校验 ProviderBundle/ProviderSpec 的 name、routing、asset profile、abstract-only、probe、display_source 与 sync-back 字段；Wiley ProviderSpec 补齐 manifest 已声明的 browser/FlareSolverr probe 依赖。
  - 验收: S13 文件/git grep 通过；`test_manifest_bundle_sync.py` / `test_known_providers_sync.py` / `test_manifest_sync_back.py` 11 passed；阶段 B manifest/scaffold/sync 回归 32 passed；`python3 -m ruff check .` 通过；AI onboarding 禁词 grep 通过；`validate_extraction_rules.py` 通过；全量 unit 1225 passed + 264 subtests；已 review `arxiv.yml` DOI sample evidence 字段。

### 阶段 B 验收点
- commit: 86d41fc
- 覆盖: S11A discovery help、S12 `--from-manifest` help、S13 manifest sync grep 均通过；`test_manifest_discovery_brief.py` / `test_onboard_from_manifests.py` / `test_provider_manifest_schema.py` / `test_known_providers_sync.py` / `test_scaffold_provider_from_manifest.py` / `test_manifest_bundle_sync.py` / `test_manifest_sync_back.py` 共 32 passed；全量 unit 1225 passed + 264 subtests。

### 阶段 C：批量调度与自动恢复（S14-S16）
- [x] S14: Coordinator 编排脚本（单 provider 串行 + coding agent CLI）
  - commit: 7bd7b9f
  - 摘要: 扩展 `onboard_from_manifests.py`，`start --provider/--manifest --dry-run` 生成 10 步串行 DAG、discovery/implementation worker brief、state schema 与 agent CLI runtime 元数据；新增 `next` / `verify` / `advance` 本地状态命令，保持脚本只生成 brief、DAG、verification plan，不调用 LLM SDK；补齐 coordinator spec、hard constraints、failure recovery 和 onboarding state schema。
  - 验收: S14 dry-run 文件和 `runtime: coding-agent-subagent` / `no_commit: true` grep 通过；LLM SDK grep 为空；`test_manifest_discovery_brief.py` / `test_onboard_from_manifests.py` 11 passed；AI onboarding 禁词 grep 通过；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1229 passed + 264 subtests。
- [x] S15: capture_fixture 非交互化与 retry
  - commit: 9f31d83
  - 摘要: `capture_fixture.py` 增加 `--from-manifest`、`--retry-via`、`--fail-fast`，manifest 模式按 purpose 读取 DOI/evidence/routing，`doi: null` 输出 skipped summary；HTTP 403/429、challenge、非 PDF fallback、access gate、empty shell、browser/FlareSolverr runtime 和 network transient 均映射为结构化 JSON error code，失败路径保持 stdout 为空。
  - 验收: S15 help/git grep 通过；`test_capture_fixture.py` 11 passed；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1236 passed + 264 subtests。
- [x] S16: 工具链结构化错误输出
  - commit: 7238a5f
  - 摘要: 新增 `_structured_errors.py` 统一 `ToolError` / JSON payload schema；scaffold、capture、snapshot、coordinator 的预期失败路径改为 stderr JSON，固定包含 `code` / `message` / `provider` / `manifest` / `task_id` / `retryable` / `details`；保留 S12/S15 兼容字段，并将 `failure-recovery.md` 改为按 `## Signal: <CODE>` 的恢复决策表。
  - 验收: 四条手工失败路径（scaffold flag conflict、capture missing DOI、snapshot missing fixture、coordinator state conflict）均非零退出、stdout 为空、stderr 可 JSON 解析；额外回归 capture missing/bad manifest 输出 `MANIFEST_NOT_FOUND` / `MANIFEST_SCHEMA_INVALID`；`test_structured_tool_errors.py` 7 passed；scaffold/capture/snapshot/coordinator 目标回归 35 passed；S16 grep 通过；`python3 -m ruff check .` 通过；`validate_extraction_rules.py` 通过；全量 unit 1243 passed + 264 subtests。

### 阶段 C 验收点
- commit: 7238a5f
- 覆盖: S14 coordinator dry-run/state DAG、S15 manifest capture/retry、S16 structured stderr 均通过本地验收；`test_manifest_discovery_brief.py` / `test_onboard_from_manifests.py` / `test_capture_fixture.py` / `test_scaffold_provider_from_manifest.py` / `test_snapshot_expected.py` / `test_structured_tool_errors.py` 等关键回归覆盖 coordinator、fixture capture、snapshot 和错误 schema；全量 unit 1243 passed + 264 subtests。

### 阶段 D：文档权威源切换（S17）
- [x] S17: AI onboarding 文档切主权威源 + 旧 docs 降级 / drift lint
  - commit: 3d9a101
  - 摘要: `docs/ai-onboarding/README.md` 切为 AI/coordinator provider onboarding 权威入口，覆盖 S14 coordinator、S15 manifest capture/retry、S16 structured error，并移除未跟踪审计文件链接；新增 `docs/ai-onboarding/acceptance.md`；`hard-constraints.md` 增加 drift lint 与 central-provider grep gates；`docs/provider-development.md` / `docs/adding-a-provider.md` 顶部降级为 human reference；新增 `test_human_docs_drift.py` 检查旧文档 API、hard-constraints grep 语法/路径和 AI/human API 禁用冲突。
  - 验收: S17 grep（`docs/ai-onboarding` banner、`Human reference only`、AI docs 禁词）通过；AI onboarding markdown 链接均可解析；`test_human_docs_drift.py` / `test_provider_manifest_schema.py` / `test_manifest_bundle_sync.py` 12 passed + 1 warning（human-only API drift warning，按 S17 规则不阻断）；`python3 scripts/validate_extraction_rules.py` 通过；`python3 -m ruff check .` 通过；全量 unit 1246 passed + 1 warning + 264 subtests；full golden corpus 174 passed。

### 最终验收点
- commit: 319baab
- 覆盖: S1-S17 均已落地并按阶段记录；S17 后 AI provider onboarding 权威源为 `docs/ai-onboarding/`，旧人类文档保留但不作为 AI worker 输入；audit §4 最终全量验收 17 段命令、结构断言和端到端 demo 均通过；最终 gate 修复将 `provider_catalog.py` 压到 279 行，并清空 availability override 函数 grep；最新全量 unit 为 1246 passed + 1 warning + 264 subtests，full golden corpus 为 174 passed；warning 来自 best-effort human docs drift 检测。
