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
- [ ] S3: 引入 ProviderBundle
- [ ] S4: onboarding 完整性 lint
- [ ] S5: Scaffold 脚本
- [ ] S6: 文档对齐 + checklist 化
- [ ] S7: Fixture 录制 + expected.json 工具
- [ ] S8: ProviderClient / Waterfall 脚手架
- [ ] S9: 重构对齐 grep lint 测试
- [ ] S10: docs 占位生成 + probe_status 默认实现

### 阶段 B：AI 接入最小闭环（S11A-S13）
- [ ] S11A: AI Manifest Discovery
- [ ] S11: ProviderManifest schema + manifests
- [ ] S12: scaffold --from-manifest
- [ ] S13: manifest ↔ bundle 同步 lint（含 sync-back）

### 阶段 C：批量调度与自动恢复（S14-S16）
- [ ] S14: Coordinator 编排脚本（单 provider 串行 + coding agent CLI）
- [ ] S15: capture_fixture 非交互化与 retry
- [ ] S16: 工具链结构化错误输出

### 阶段 D：文档权威源切换（S17）
- [ ] S17: AI onboarding 文档切主权威源 + 旧 docs 降级 / drift lint
