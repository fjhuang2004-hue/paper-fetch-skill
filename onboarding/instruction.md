# /goal 通用执行说明：添加 Provider

本文是 provider onboarding 的通用执行入口。推荐直接运行：

```text
/goal follow onboarding/instruction.md 添加 <provider> provider
```

执行者必须把 `onboarding/` 当作唯一权威输入目录。不要从临时聊天记录、README 或旧人工教程推断 provider 行为；涉及路由、fixture purpose、route contract、Markdown contract、资产 profile、验收 gate 和文档同步时，以 provider manifest、schema、brief、hard constraints 和 acceptance 文档为准。

可自动化范围和不可自动化边界见 [`automation-roadmap.md`](./automation-roadmap.md)。可以优先使用 full runner：

```bash
python3 scripts/onboard_from_manifests.py run --provider <provider> --domain <domain> --output-dir .paper-fetch-runs/<provider>-onboarding
python3 scripts/onboard_from_manifests.py run --manifest onboarding/manifests/<provider>.yml --until merge-ready
```

runner 只通过 `PROVIDER_ONBOARDING_AGENT_CLI` 调用本地外部 agent CLI；它不能代替 operator 批准 access review，也不能把最终 Markdown 语义审查自动签为 true。

## 目标

新增或更新一个 provider，使项目可以对该 provider 的 DOI 或 article URL 走 provider-owned fulltext waterfall，返回结构化 `ArticleModel` / Markdown，并覆盖真实 DOI replay fixture、route 成功判定、Markdown Review Loop、资产语义、fallback、provider status 和文档同步。

目标完成时应满足：

- `onboarding/manifests/<provider>.yml` 存在且通过 schema。
- `onboarding/access-reviews/<provider>.yml` 已由 operator 批准，且 `may_continue: true`。
- manifest 含完整 `route_contract`：每个 `main_path` step 都有成功要求和必要拒绝条件。
- manifest 含完整 `markdown_contract`：每个 non-null fixture purpose 都有正向和负向 Markdown 断言输入。
- 真实 fixture 已按 manifest purpose 捕获；不伪造 DOI、DOM、fixture 或 expected output。
- `onboarding/reviews/<provider>.yml` 记录每个 non-null fixture 和 `extra_fixtures` 的样本代表性与 Markdown 语义审查。
- provider-owned implementation、provider-local tests、expected snapshots、sync-back、shared docs 均完成。
- provider-local acceptance、Markdown review contract、route contract、bundle completeness、owner reuse、docs validation 和完整 unit 验证通过。

## 执行原则

- 默认使用中文汇报。
- 使用项目代码和项目脚本完成开发，不使用 Agent 自带的 paper-fetch MCP、Skill 或外部环境 CLI 替代项目实现。
- 不触发 GitHub CI，除非用户明确要求。
- 不提交 commit，除非用户明确要求。
- 不编辑中心 provider 逻辑文件：
  - `src/paper_fetch/provider_catalog.py`
  - `src/paper_fetch/extraction/html/provider_rules.py`
  - `src/paper_fetch/quality/html_signals.py`
  - `src/paper_fetch/quality/html_availability.py`
- 不绕过 `ProviderClient.fetch_result()` 自己拼最终 `FetchEnvelope`。
- 不保留 scaffold placeholder、skip placeholder 或 Markdown review-loop placeholder。
- 若遇到未授权、captcha、challenge、403、rate limit 或样本不合适，按 structured error 和 `onboarding/failure-recovery.md` 处理，优先替换对应 purpose 的 DOI sample。

## 固定执行流

严格按下列阶段推进。每个阶段完成后先验证，再进入下一阶段；若验证失败，先修当前阶段。

1. 预检与上下文：
   - `git status --short`
   - 阅读 `onboarding/README.md`、`agent-task-brief.md`、`hard-constraints.md`、`provider-manifest.md`、`acceptance.md`。
2. 生成 onboarding dry-run artifacts：
   - `PYTHONPATH=src python3 scripts/onboard_from_manifests.py start --provider <provider> --domain <domain> --dry-run --output-dir .paper-fetch-runs/<provider>-onboarding`
   - 检查 DAG 顺序和 generated briefs。
3. 编写或修复 manifest：
   - 若缺少 `onboarding/access-reviews/<provider>.yml`，可用 `python3 scripts/backfill_access_reviews.py --provider <provider> --write` 生成 blocked 草稿；草稿不等于批准，operator 仍需补齐合法访问、allowed runtime、禁止行为、challenge 策略、临时站点策略并改为 `may_continue: true`。
   - 填 `routing`、`main_path`、`route_contract`、`markdown_contract`、`asset_profile`、`supplementary_scope`、`probe`、`fixtures.doi_samples` 和 docs fact base。
   - `success_criteria` 和 `extraction_hints` 是 sync-back 字段，初稿只放空对象、空数组或 null。
   - 验证：`PYTHONPATH=src python3 -m pytest tests/unit/test_provider_manifest_schema.py -q`
4. 捕获 fixtures：
   - 运行 `scripts/capture_fixture.py --from-manifest onboarding/manifests/<provider>.yml --all --auto-via --fail-fast`。
   - null purpose 必须有清楚的 `evidence_reason`。
5. 生成 cleaning proposal：
   - `python3 scripts/propose_cleaning_chain.py --provider <provider> --write`
   - 确认 compact proposal 和 full evidence 都存在：`onboarding/cleaning-chain-proposals/<provider>.yml` 与 `<provider>.evidence.yml`。
   - `python3 scripts/onboard_from_manifests.py check-cleaning-proposal --provider <provider>` 必须通过，保证 `fixtures_digest` 未过期。
6. Scaffold provider：
   - `python3 scripts/scaffold_provider.py --from-manifest onboarding/manifests/<provider>.yml --merge-existing=safe`
   - 若 stdout 返回 `status: MERGE_PLAN`，按 diff preview 合并已有文件，不删除用户改动。
7. 实现 provider：
   - Implementation worker brief 会 inline compact cleaning proposal；只把其中带 provenance 的清洗建议、contract delta、over-cleaning probes 和 token conflict report 当作输入证据。
   - 只改 provider-owned 文件和 provider-local 测试。
   - 先把每个 `route_contract.<step>` 写成 route 成功 / 拒绝测试。
   - 先把每个 `markdown_contract.<purpose>` 写成 provider-local Markdown 断言，marker 用 `markdown-review: purpose=<purpose> doi=<doi>`。
   - 再实现 waterfall、typed payload、HTML/XML/PDF 转换、资产和 status。
8. Markdown Review Loop：
   - 对每个 non-null fixture 生成 baseline Markdown。
   - 可先运行 `python3 scripts/bootstrap_review_artifact.py --provider <provider> --manifest onboarding/manifests/<provider>.yml` 生成 review 草稿；草稿默认 `markdown_semantic_reviewed: false`。
   - 人工阅读 Markdown，并写入 `onboarding/reviews/<provider>.yml`：`baseline_markdown_path`、`baseline_markdown_sha256`、`review_notes`、`sample_representative`、`markdown_semantic_reviewed`、`issues`、`assertions`、`fixes`。
   - `issues` 和 `fixes` 使用带稳定 `id` 的对象；每个 fix 必须引用已有 `issue_ids`，并列出至少一个 provider-local `test_names`。
   - 每个 issue 先落 provider-local 断言，再修 provider-owned 实现。
   - 重复到所有 fixture Markdown 干净。
9. Shared integration：
   - 由 coordinator 集成 provider-owned worker 之外的共享面：provider catalog、MCP status/instructions/schema、golden/live review、benchmark samples、必要的 shared renderer/workflow、shared docs 和 changelog。
   - 每个共享改动必须能追溯到 manifest fact、bundle sync-back、fixture replay 或 provider-local test 暴露的共享缺口。
10. 生成 expected snapshots：
   - `PYTHONPATH=src python3 scripts/snapshot_expected.py --doi "<doi>" --review`
   - `PYTHONPATH=src python3 scripts/snapshot_expected.py --doi "<doi>"`
   - 或用 `python3 scripts/onboard_from_manifests.py verify --provider <provider> --task snapshot-expected` 枚举 manifest 中所有 non-null DOI 的 review/write/check 命令。
11. Sync-back manifest：
   - `PYTHONPATH=src python3 scripts/manifest_sync_back.py --provider <provider> --manifest onboarding/manifests/<provider>.yml --sync-docs`
12. 本地验收：
    - 优先运行 `python3 scripts/onboard_from_manifests.py run-checks --provider <provider> --all-local`。
    - `python3 scripts/onboard_from_manifests.py check-cleaning-proposal --provider <provider>`
    - `python3 scripts/propose_cleaning_chain.py --provider <provider> --check-contract`
    - `PYTHONPATH=src python3 -m pytest tests/unit/test_<provider>_provider.py -q`
    - `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_markdown_review_contract.py -q`
    - `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_route_contract.py -q`
    - `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_bundle_completeness.py tests/unit/test_provider_owner_reuse.py -q`
    - `PYTHONPATH=src python3 -m pytest tests/unit/test_manifest_bundle_sync.py -q`
    - `PYTHONPATH=src python3 -m pytest tests/unit/test_human_docs_drift.py -q`
    - `python3 scripts/validate_extraction_rules.py`
    - 对 browser/CDN-risk provider 运行 provider subset live review，例如 `PAPER_FETCH_RUN_LIVE=1 python3 scripts/run_golden_criteria_live_review.py --providers mdpi`
    - 维护期或合并前人工巡检 route-source drift 时，可本地手动运行 `PAPER_FETCH_RUN_LIVE=1 python3 scripts/run_provider_drift_report.py --provider <provider> --output .paper-fetch-runs/drift/<provider>.json`；该命令不是 GitHub CI gate。
    - `PYTHONPATH=src python3 -m pytest tests/unit -q`
13. 文档同步与 merge-ready：
    - 更新 `docs/providers.md`、`docs/extraction-rules.md`、`CHANGELOG.md` 和 `onboarding/known-providers.yml`。
    - 文档同步后重新运行 docs drift、manifest bundle sync 和 extraction rules validation。

## Provider 专项检查清单

- 路由只用已验证 prefix、domain、domain suffix 或 publisher alias。
- Article/PDF/XML URL 模板来自 manifest 或 provider bundle，不在多个文件散落复制。
- route success 不只看 HTTP 200；必须满足 `route_contract`。
- HTML/XML/PDF wrapper、access gate、challenge、empty shell 和 abstract-only 不得误判 fulltext。
- PDF fallback 必须拒绝 HTML wrapper；text-only fallback 必须标记资产跳过。
- `asset_profile=none/body/all` 语义稳定；supplementary 只能来自明确 scope。
- `ProviderMetadata` 是 provider / metadata adapter 产出的可选字段 `TypedDict`，用于 metadata merge、routing probe 和文章构建前的元数据传递；它不是新的 runtime payload 容器。Provider 对外 override 签名必须保持 `Mapping[str, Any]` 兼容，只在内部构造、合并或局部收窄时使用 `ProviderMetadata`。
- Markdown 无站点 chrome、access noise、重复 boilerplate、重复 figures/tables。
- References 不被误清洗，正文 citation anchor 不被误当成 references 条目。
- Formula、table、caption 由 canonical renderer 或 provider adapter 输出，不新建平行 renderer。
- `probe_status()` 不访问秘密路径，不泄露 token 或本地 browser endpoint。

## 最终汇报格式

可先生成 operator digest：

```bash
python3 scripts/onboard_from_manifests.py summarize --provider <provider> --format markdown --output .paper-fetch-runs/<provider>-onboarding/summary.md
```

目标完成时，最终回复应包含：

- 变更文件概览。
- provider main path 和公开 source。
- fixture 覆盖表，按 purpose 列出 DOI 或 null 原因。
- review artifact 摘要，按 `fixture/purpose -> issue id -> fix id -> test` 说明。
- 运行过的命令和结果。
- 未解决风险或无法覆盖的 fixture purpose。

不要宣称完成，除非本地 acceptance 已通过；如果某个 gate 不能通过，明确列出阻塞原因、失败命令和下一步修复点。
