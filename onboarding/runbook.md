# Provider Onboarding Runbook

本文是 provider onboarding 的三阶段 operator runbook，只负责说明阶段入口、人工审核点和可复制的 `/goal` 提示词。权威输入仍是 `onboarding/` 下的 schema、manifest、access review、provider review、hard constraints、failure recovery 和 acceptance 文档；本文不替代这些文件，也不提供脚本命令或 shell recipe。

## 使用原则

Provider onboarding 按三段推进：先做准入批准和启动上下文确认，再审 fixture 证据与覆盖，最后收口实现并完成 Markdown 语义终审。每一段都要求 operator 明确检查对应 artifact，而不是只接受 worker 的自然语言总结。

准入阶段只判断是否允许继续访问和启动，不批准未审核的 access review。Fixture 阶段只判断 manifest 样本、discovery proof、null purpose 和本地 fixture 证据是否足以支撑实现，不提前承担 Markdown 终审。实现收口阶段才阅读当前 `extracted.md`、真实 `markdown-quality.json` 和 `onboarding/reviews/<provider>.yml`，并决定是否可以写入最终语义签字。

## 1. 准入与启动

### 目标

确认 provider 的访问策略已经由 operator 人工批准，并把后续 worker 的边界固定在 `onboarding/` 权威输入内。该阶段关注能否合法、稳定、可审计地开始 discovery 或继续已有 manifest，不评估 fixture 质量，也不签署 Markdown 语义审查。

### `/goal` 提示词

```text
/goal 按 onboarding/ 权威输入启动 provider <provider> 的 onboarding 准入阶段。

目标：只完成 access review 人工批准状态、启动上下文和 worker 边界检查；不要实现 provider，不要捕获 fixture，不要签署 Markdown 语义审查。

边界：
- 只使用 onboarding/access-reviews/<provider>.yml、onboarding/hard-constraints.md、onboarding/failure-recovery.md、onboarding/acceptance.md 和相关 manifest/schema 文档判断是否可继续。
- 不自动把 access review 草稿改成 approved，也不自动设置 may_continue。
- 遇到登录、challenge、CAPTCHA、paywall、临时站点策略不清楚或访问异常时停止并报告原因。
- 不触发 GitHub CI，不提交 commit。

完成后输出：当前 access review 状态、是否允许进入 fixture/discovery 阶段、阻塞原因和需要 operator 人工决定的事项。
```

### 人工审核点

- `onboarding/access-reviews/<provider>.yml` 是否存在、符合 schema，且 `status` 与 `may_continue` 是 operator 真实批准后的结果。
- 访问策略是否清楚说明 runtime、登录需求、challenge/CAPTCHA/paywall 风险、限流风险和允许的抓取方式。
- `onboarding/hard-constraints.md` 中的 worker scope 是否能约束后续任务，尤其是不得绕过访问控制、不得写 secrets、不得触碰禁止路径。
- provider 名称、domain、DOI prefix 或已有 manifest 是否与启动目标一致，避免把一个 provider 的批准用于另一个 provider。

### 通过标准

- access review 已人工批准，且没有未解释的访问异常。
- 后续 worker 的输入范围、可写路径和停止条件清楚。
- operator 明确同意进入下一阶段；未批准、低信任或访问状态不明时必须停下报告。

## 2. Fixture 检测

### 目标

检查 manifest 中的 fixture 覆盖、discovery proof 和本地 fixture 证据是否足以支撑实现。该阶段只审样本代表性和证据链，不把 `markdown_semantic_reviewed` 设为最终通过，也不替代实现后的 Markdown 终审。

### `/goal` 提示词

```text
/goal 检测 provider <provider> 的 fixture 覆盖与代表性。

目标：审查 onboarding/manifests/<provider>.yml 中的 DOI samples、extra fixtures、discovery proof、null purpose 说明、本地 fixture 路径和样本证据，判断是否足以进入实现阶段。

边界：
- 只评估 fixture 选择、证据充分性、覆盖范围和与 manifest contract 的一致性。
- 对 table、formula、supplementary 的 discovery proof，检查 queries、candidates、selected_doi、rejections、exhausted 和 evidence_summary 是否能证明选择或 null 结论。
- 对低置信、样本不可用、proof 与本地 fixture/cleaning evidence 矛盾、null purpose 解释不足的情况，不自动通过。
- 不提前签署 markdown_semantic_reviewed: true；Markdown 语义终审留到实现收口阶段。
- 不触发 GitHub CI，不提交 commit。

完成后输出：每个 fixture purpose 的 DOI、confidence、代表性判断、缺失或矛盾证据、是否允许进入实现阶段。
```

### 人工审核点

- `fixtures.doi_samples` 是否覆盖必需 purpose；`structure`、`figure`、`references` 是否为非空 DOI。
- 每个非空 DOI 是否有可审计的 `evidence_url`、`evidence_reason`、`observed_signals` 和可信的 `confidence`。
- `fixtures.discovery_proof` 是否对 `table`、`formula`、`supplementary` 记录足够查询、候选、拒绝理由和选择依据。
- `doi: null` 的 optional purpose 是否真的有耗尽证据；若本地 fixture 或 cleaning evidence 已显示相关信号，null 结论必须重新审查。
- `extra_fixtures` 是否补充了结构广度，而不是替代固定 purpose 的必需覆盖。
- 本地 fixture 路径是否与 manifest DOI 和 purpose 对应，且样本不是 access gate、空壳或错误页伪装成正文。

### 通过标准

- 每个必需 fixture purpose 都有明确、可追溯、与本地证据一致的结论。
- 低置信样本已被解释并由 operator 接受，或被替换为更可靠样本。
- null purpose 有充分 exhausted proof，且没有与本地证据矛盾。
- Fixture 阶段只给出进入实现的许可，不给出 Markdown 语义终审许可。

## 3. 剩余实现与 Markdown 终审

### 目标

完成 provider 实现收口、本地验收和最终 Markdown 语义审查。该阶段必须基于当前 `extracted.md`、真实 `markdown-quality.json` 和 `onboarding/reviews/<provider>.yml` 做判断，不能只依赖旧报告、worker 回复或 bootstrap 草稿。

### `/goal` 提示词

```text
/goal 收口 provider <provider> 的剩余实现并完成 Markdown 终审。

目标：根据 onboarding/manifests/<provider>.yml、onboarding/reviews/<provider>.yml、当前 fixture extracted.md 和真实 markdown-quality.json，完成 provider-local 实现验收、Markdown quality 修复确认、figure asset 终审和最终 review artifact 检查。

边界：
- 所有实现判断必须回到 manifest 的 route_contract、markdown_contract、asset_contract 和 hard constraints。
- 阅读当前 extracted.md，而不是只信旧的 markdown-quality.json 或 worker 总结。
- markdown-quality.json 必须是对应当前 extracted.md 的真实审查记录；存在 blocking issue 时不得通过。
- 只有完成真实语义审查后，才能接受 markdown_semantic_reviewed: true。
- figure asset 终审必须确认正文内联位置、本地文件落盘、字节数和最终 Markdown 本地路径 rewrite；caption-only、remote-only 或缺少 provider-local 断言都不能通过。
- 不触发 GitHub CI，不提交 commit。

完成后输出：剩余实现状态、本地验收结果、每个 fixture 的 Markdown 语义结论、figure asset 结论、review artifact 是否可作为最终通过依据。
```

### 人工审核点

- provider-local 测试是否覆盖每个非空 fixture purpose、每个 `route_contract` step，以及 `markdown_contract` 的正向和负向断言。
- `extracted.md` 是否包含预期正文、结构、引用、表格、公式、图片或补充材料信号，并排除站点 chrome、access noise、重复 boilerplate 和错误页内容。
- `markdown-quality.json` 是否对应当前 `extracted.md`，且没有 fresh blocking issue。
- `onboarding/reviews/<provider>.yml` 是否为 durable artifact：包含每个非空 fixture 和 `extra_fixtures` 的路径、sha256、review notes、issues、assertions、fixes、`sample_representative: true` 和真实的 `markdown_semantic_reviewed: true`。
- `issues` 和 `fixes` 是否使用稳定 id，且每个 fix 都引用现有 issue 并列出 provider-local 测试。
- `asset_contract.figures.inline: body` 时，正文中的 Markdown 图片是否位于 References/Figures/Supplementary 等尾部 section 之前。
- `asset_contract.figures.download: required` 时，provider-local 断言是否覆盖本地文件路径、字节数、asset result state 和最终 Markdown 链接重写。

### 终审通过标准

- 实现只修改允许的 provider-owned 文件和 review artifact，没有触碰禁止路径或中心 provider-specific 逻辑。
- 本地验收通过，且失败、跳过或 warning 都有明确解释。
- 当前 `extracted.md`、`markdown-quality.json` 和 `onboarding/reviews/<provider>.yml` 三者一致。
- 每个非空 fixture 和 `extra_fixtures` 都完成真实语义审查；没有 TODO、TBD、unknown 或 bootstrap 占位。
- figure asset contract 满足 manifest 要求；不满足时必须阻塞并说明原因。

## 通用边界

- 不自动批准 access review；`status: approved` 和 `may_continue: true` 只能来自 operator 人工决定。
- 不自动登录、处理 CAPTCHA、绕过 challenge 或 paywall，也不发明临时站点策略。
- 不自动签 `markdown_semantic_reviewed: true`；该字段只能来自对当前 `extracted.md` 的真实语义审查。
- 不触发 GitHub CI；验收以本地、repo-owned artifact 和 acceptance 文档为准。
- 低置信 fixture、样本不可用、null purpose 证据不足、访问异常、retry exhaustion 或 blocked state 必须停下报告原因。
- Worker 回复和临时日志只能作为辅助材料；最终判断以 manifest、fixture、quality report、review artifact 和本地验收结果为准。
