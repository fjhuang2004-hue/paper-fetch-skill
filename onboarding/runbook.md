# Provider Onboarding Runbook

本文是 provider onboarding 的三阶段 operator runbook，只负责说明高自动化阶段入口、人类审核点和可复制的 `/goal` 提示词。权威输入仍是 `onboarding/` 下的 schema、manifest、access review、provider review、hard constraints、failure recovery 和 acceptance 文档；本文不替代这些文件，也不新增 coordinator 或 worker 合约。

## 使用原则

Provider onboarding 仍按三段推进：先准备准入和启动上下文，再生成并审查 fixture 证据与覆盖，最后自动收口实现并完成 Markdown 语义终审。默认要求 AI/coordinator 尽量使用项目已有 runner、verify、run-checks、diagnose、resume-blocked dry-run、summarize 和 repair loop 自动推进到当前阶段可达的最远点；人类只负责审核 artifact、批准 operator-only 结论和处理访问/权限类阻塞。

准入阶段由 AI 准备 access review 草稿、启动上下文、worker 边界、state 和 operator digest，但未获人类批准前不得把 `status: approved` 或 `may_continue: true` 当作已成立。Fixture 阶段由 AI 自动执行 discovery、manifest validate/autofix、capture、cleaning proposal 和本地检查，operator 只审核低置信、矛盾或无法自动决策的样本结论。实现收口阶段由 AI 自动执行 implementation、shared integration、snapshot、fresh Markdown quality review、repair loop、sync-back 和 local acceptance；operator 只基于当前 `extracted.md`、真实 `markdown-quality.json`、`onboarding/reviews/<provider>.yml` 和 run records 审核最终语义签字。

## 1. 准入与启动

### 目标

让 AI/coordinator 自动准备 provider onboarding 的启动上下文、access review 草稿或状态诊断、worker 边界和可继续计划；人类只审核合法访问、allowed runtime、challenge/CAPTCHA/paywall 策略、临时站点策略，并在确认后批准 access review。

### `/goal` 提示词

```text
/goal 按 onboarding/ 权威输入尽量自动启动 provider <provider> 的 onboarding。

目标：使用项目脚本自动完成准入准备、启动上下文、worker 边界检查和可继续计划；若 access review 已由 operator 批准，则继续启动 runner 到当前阶段可达的最远点。人类只负责审核 access review、访问策略和阻塞摘要。

自动化要求：
- 读取 onboarding/README.md、coordinator-spec.md、hard-constraints.md、failure-recovery.md、acceptance.md、automation-roadmap.md 和相关 schema，确认当前 DAG、state、worker 边界和 operator-only gate。
- 检查 onboarding/access-reviews/<provider>.yml；若缺失或 blocked，优先用项目脚本生成/更新 blocked 草稿和 operator digest，列出建议的合法访问模式、allowed runtime、禁止行为、challenge/CAPTCHA/paywall 策略和临时站点策略，等待人类审核。
- 若 access review 已符合 schema、status: approved 且 may_continue: true，使用项目 runner/verify/run-checks 自动生成 task DAG、brief、discovery evidence pack 或继续已有 manifest，并推进到下一个需要人类审核或结构化 blocker 的位置。
- 对 ACCESS_REVIEW_NOT_FOUND、ACCESS_REVIEW_NOT_APPROVED、BROWSER_RUNTIME_REQUIRED、CHALLENGE_DETECTED、HTTP_FORBIDDEN、HTTP_RATE_LIMITED 等 operator-only 阻塞，运行 diagnose 或 resume-blocked --dry-run 形成可审计摘要，不靠自然语言豁免继续。

边界：
- 可以生成草稿、摘要、dry-run artifact、state、brief 和诊断；不能在未获人类批准时把 access review 改成 approved 或把 may_continue 当作 true。
- 不自动登录、不处理 CAPTCHA、不绕过 challenge/paywall，不发明临时站点策略。
- 不触发 GitHub CI，不提交 commit。

完成后输出：已执行的项目命令或 dry-run 计划、access review 当前状态、是否已经允许进入 discovery/fixture 阶段、自动推进到的 task、结构化阻塞原因，以及需要人类审核/批准的具体字段。
```

### 人类审核点

- `onboarding/access-reviews/<provider>.yml` 是否存在、符合 schema，且 `status` 与 `may_continue` 是人类审核后的真实批准结果。
- AI 生成的合法访问模式、allowed runtime、登录需求、challenge/CAPTCHA/paywall 风险、限流风险和允许抓取方式是否可接受。
- `onboarding/hard-constraints.md` 中的 worker scope 是否能约束后续自动任务，尤其是不得绕过访问控制、不得写 secrets、不得触碰禁止路径。
- provider 名称、domain、DOI prefix 或已有 manifest 是否与启动目标一致，避免把一个 provider 的批准用于另一个 provider。

### 通过标准

- access review 已由人类审核批准，且没有未解释的访问异常。
- AI 已生成或更新可审计的 state、brief、diagnosis/summary，后续 worker 的输入范围、可写路径和停止条件清楚。
- 若未批准、低信任或访问状态不明，AI 已停在 operator gate，并明确列出人类需要决定的字段。

## 2. Fixture 检测

### 目标

让 AI 自动生成或验证 manifest fixture 覆盖、discovery proof、本地 fixture 和 cleaning evidence，并把低置信、矛盾、不可用或需要语义判断的样本结论交给人类审核。该阶段只审核样本代表性和证据链，不把 `markdown_semantic_reviewed` 设为最终通过。

### `/goal` 提示词

```text
/goal 自动检测并补齐 provider <provider> 的 fixture 覆盖与代表性证据。

目标：根据 onboarding/manifests/<provider>.yml、discovery evidence pack、fixture 目录和 cleaning proposal，尽量自动完成 manifest discovery/validate/autofix、fixture capture、cleaning proposal、inspect-discovery 和本地 fixture gate；人类只审核 AI 输出的代表性结论、低置信样本和无法自动通过的证据缺口。

自动化要求：
- 若没有 manifest 且 access review 已批准，派 discover-manifest worker 生成 onboarding/manifests/<provider>.yml；若已有 manifest，则从现有 manifest 继续。
- 运行或规划 validate-manifest，并使用允许的 autofix 补齐机器可判 schema/proof/contract 缺口；低置信 DOI candidate 只能记录 proof/rejection，不能静默替换为通过结论。
- 自动捕获所有 non-null DOI sample 和 extra_fixtures，按 failure-recovery 处理 UNSUITABLE_DOI_SAMPLE、ACCESS_GATE_CAPTURED、EMPTY_ARTICLE_SHELL、NON_PDF_FALLBACK_CONTENT、NETWORK_TRANSIENT 等结构化错误。
- 自动生成/校验 cleaning-chain proposal，并用 inspect-discovery 或 summary 汇总每个 fixture purpose 的 DOI、confidence、observed_signals、evidence_url、evidence_reason、本地 fixture 路径和 proof 状态。
- 对 table、formula、supplementary 的 discovery_proof，自动检查 queries、candidates、selected_doi、rejections、exhausted 和 evidence_summary 是否能证明选择或 null 结论。

边界：
- 不把低置信、样本不可用、proof 与本地 fixture/cleaning evidence 矛盾、null purpose 解释不足的情况自动标成已通过；必须输出为人类审核项或回到 discovery 替换样本。
- 不提前签署 markdown_semantic_reviewed: true；Markdown 语义终审留到实现收口阶段。
- 不触发 GitHub CI，不提交 commit。

完成后输出：自动执行/规划的命令、每个 fixture purpose 的 DOI 与 confidence、capture/cleaning/manifest gate 结果、缺失或矛盾证据、建议的自动修复或替换样本动作，以及需要人类审核才能进入实现阶段的事项。
```

### 人类审核点

- `fixtures.doi_samples` 是否覆盖必需 purpose；`structure`、`figure`、`references` 是否为非空 DOI。
- 每个非空 DOI 是否有可审计的 `evidence_url`、`evidence_reason`、`observed_signals`、本地 fixture 路径和可信的 `confidence`。
- `fixtures.discovery_proof` 是否对 `table`、`formula`、`supplementary` 记录足够查询、候选、拒绝理由和选择依据。
- `doi: null` 的 optional purpose 是否真的有耗尽证据；若本地 fixture 或 cleaning evidence 已显示相关信号，null 结论必须重新审查。
- `extra_fixtures` 是否补充了结构广度，而不是替代固定 purpose 的必需覆盖。
- 本地 fixture 路径是否与 manifest DOI 和 purpose 对应，且样本不是 access gate、空壳或错误页伪装成正文。

### 通过标准

- AI 已自动完成或明确规划 manifest validate/autofix、fixture capture、cleaning proposal 和 fixture gate，所有失败都有 structured code 或可审计原因。
- 每个必需 fixture purpose 都有明确、可追溯、与本地证据一致的结论。
- 低置信样本已被人类接受，或已由 AI 按 failure recovery 替换为更可靠样本。
- null purpose 有充分 exhausted proof，且没有与本地证据矛盾。
- Fixture 阶段只给出进入实现的许可，不给出 Markdown 语义终审许可。

## 3. 剩余实现与 Markdown 终审

### 目标

让 AI 自动完成 provider 实现收口、本地验收、snapshot、fresh Markdown quality review、repair loop、manifest sync-back、shared integration 和 operator digest；人类只基于当前 `extracted.md`、真实 `markdown-quality.json`、`onboarding/reviews/<provider>.yml` 和 run records 审核最终 Markdown 语义签字。

### `/goal` 提示词

```text
/goal 尽量自动收口 provider <provider> 的剩余实现并准备 Markdown 终审。

目标：根据 onboarding/manifests/<provider>.yml、onboarding/reviews/<provider>.yml、当前 fixture extracted.md、真实 markdown-quality.json、cleaning proposal 和 acceptance 文档，自动推进 implement-provider、shared-integration、snapshot-expected、manifest-sync-back、provider-local-acceptance、global-lint 和 merge-ready 前检查；人类只审核最终 artifact 和 operator-only 语义结论。

自动化要求：
- 以 manifest 的 route_contract、markdown_contract、asset_contract、probe、main_path 和 hard constraints 为唯一 provider 行为输入，自动派 implement-provider worker 或继续已有实现。
- 自动把 route_contract 和 markdown_contract 固化为 provider-local route/Markdown 正负断言；每个修复必须先有 provider-local 测试或明确的 shared renderer/workflow 证据。
- 自动生成/刷新 expected snapshots、extracted.md、markdown-quality-prompt.md 和 agent-authored markdown-quality.json；运行 fresh Markdown quality review，发现 blocking issue 时进入最多 3 轮 repair-markdown-quality 闭环。
- 自动校验 figure asset contract：正文内联位置、本地文件落盘、字节数、asset result state 和最终 Markdown 本地路径 rewrite；不满足时回到实现或 manifest 修复。
- 自动执行 manifest-sync-back、provider-local acceptance、review/provider contract、bundle completeness、owner reuse、docs validation 和可用的 local runner gate，并生成 operator summary。

边界：
- 不能只信旧 markdown-quality.json、worker 总结或 bootstrap 草稿；必须回到当前 extracted.md、fresh review 和 run records。
- 存在 fresh blocking issue、pending/fail quality report、缺少 provider-local 断言、asset contract 不满足或 review artifact 不一致时，不得通过。
- 可以准备或更新 review artifact 中可由证据支持的字段，但 `markdown_semantic_reviewed: true` 只能在人类审核当前 Markdown 和质量报告后作为最终语义签字成立。
- 不触发 GitHub CI，不提交 commit。

完成后输出：自动执行的实现/验收/repair/sync-back 结果、剩余失败或 structured blocker、每个 fixture 的 Markdown 质量结论、figure asset 结论、review artifact 与当前 extracted.md/markdown-quality.json 是否一致，以及需要人类审核后才能接受的最终语义签字项。
```

### 人类审核点

- provider-local 测试是否覆盖每个非空 fixture purpose、每个 `route_contract` step，以及 `markdown_contract` 的正向和负向断言。
- 当前 `extracted.md` 是否包含预期正文、结构、引用、表格、公式、图片或补充材料信号，并排除站点 chrome、access noise、重复 boilerplate 和错误页内容。
- `pdf_fallback` fixture 的 Markdown 是否来自 shared `pymupdf4llm` text-only 转换；provider 不应另加 PDF Markdown cleanup、front matter reconstruction、水印移除或 reference extraction。
- `markdown-quality.json` 与 fresh review 是否对应当前 `extracted.md`，且没有 blocking issue。
- `onboarding/reviews/<provider>.yml` 是否为 durable artifact：包含每个非空 fixture 和 `extra_fixtures` 的路径、sha256、review notes、issues、assertions、fixes、`sample_representative: true` 和真实的 `markdown_semantic_reviewed: true`。
- `issues` 和 `fixes` 是否使用稳定 id，且每个 fix 都引用现有 issue 并列出 provider-local 测试。
- `asset_contract.figures.inline: body` 时，正文中的 Markdown 图片是否位于 References/Figures/Supplementary 等尾部 section 之前。
- `asset_contract.figures.download: required` 时，provider-local 断言是否覆盖本地文件路径、字节数、asset result state 和最终 Markdown 链接重写。

### 终审通过标准

- AI 已自动完成或明确阻塞在 implement-provider、shared-integration、snapshot-expected、manifest-sync-back、provider-local-acceptance、global-lint 或 merge-ready 前检查，并提供 run records。
- 实现只修改允许的 provider-owned 文件、review artifact 和可追溯的 shared integration 文件，没有触碰禁止路径或中心 provider-specific 逻辑。
- 本地验收通过，且失败、跳过或 warning 都有明确解释。
- 当前 `extracted.md`、`markdown-quality.json`、fresh review 和 `onboarding/reviews/<provider>.yml` 四者一致。
- 每个非空 fixture 和 `extra_fixtures` 都完成人类审核后的真实语义审查；没有 TODO、TBD、unknown 或 bootstrap 占位。
- figure asset contract 满足 manifest 要求；不满足时必须阻塞并说明原因。

## 最大自动化边界

- AI 可以生成草稿、运行项目脚本、派 worker、修复机器可判缺口、替换不合适样本、刷新 snapshot/quality/report，并生成 operator digest。
- Access approval 不能由脚本伪造；`status: approved` 和 `may_continue: true` 只能在人类审核合法访问、runtime、challenge/CAPTCHA/paywall 和临时站点策略后成立。
- AI 不自动登录、不处理 CAPTCHA、不绕过 challenge 或 paywall，也不发明临时站点策略。
- AI 不能把 `markdown_semantic_reviewed: true` 当作纯机器结论；最终语义签字必须基于人类对当前 `extracted.md`、真实 `markdown-quality.json`、fresh review 和 review artifact 的审核。
- 不触发 GitHub CI；验收以本地、repo-owned artifact 和 acceptance 文档为准。
- 低置信 fixture、样本不可用、null purpose 证据不足、访问异常、retry exhaustion 或 blocked state 必须由 AI 先自动诊断/汇总，再交给人类审核或按 failure recovery 返回前序自动步骤。
- Worker 回复和临时日志只能作为辅助材料；最终判断以 manifest、fixture、quality report、review artifact、state run records 和本地验收结果为准。
