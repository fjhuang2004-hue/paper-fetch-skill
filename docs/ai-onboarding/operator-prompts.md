# Operator Prompts

本文件给出 operator 在 coding agent CLI 长跑会话里启动 coordinator、派 `discover-manifest` worker 和 `implement-provider` worker 时使用的提示词模板。所有模板只把已在 `docs/ai-onboarding/` 内固化的 brief 字段、schema 和 hard constraints 组装成 prompt；不引入新的权威字段，不复述自然语言导览。

模板分三段：

- A. Coordinator session prompt — 一次性贴入主会话，用于驱动 `coordinator-spec.md` 中的 10 步 DAG。
- B. discover-manifest worker prompt — 在第 1 步派 discovery 子 agent 时使用。
- C. implement-provider worker prompt — 在第 5 步派 implementation 子 agent 时使用。

`<NAME>` 用 normalized provider id 替换；`<DOMAIN>` 用 publisher 主域名替换。`<<<...>>>` 占位必须替换为对应文件的完整文本，不允许概括或截断。

## Authority Mapping

| Prompt | Authority Files Inlined |
|---|---|
| A | `coordinator-spec.md`、`hard-constraints.md`、`failure-recovery.md`、`onboarding-state.schema.json` 路径引用（不 inline 全文） |
| B | `briefs/discover-manifest.yml`、`provider-manifest.schema.json`、`hard-constraints.md` 全文 inline |
| C | `briefs/implement-provider.yml`、`hard-constraints.md`、`manifests/<NAME>.yml` 全文 inline |

Coordinator-spec.md §Worker Prompt Input 已固化这两个 worker 的 inline 要求。Operator 不得增加额外文件。

## A. Coordinator Session Prompt

主会话开始时一次性贴入。该会话扮演 coordinator，按 `coordinator-spec.md` 的 10 步 DAG 推进；在 `discover-manifest` 和 `implement-provider` 两步派子 agent，其它步骤直接调用 `scripts/onboard_from_manifests.py` 与配套脚本。

```text
你是 provider onboarding coordinator。项目根 /home/dictation/paper-fetch-skill，
PYTHONPATH=src。本次接入 provider: <NAME>，domain: <DOMAIN>。

# 权威输入
- docs/ai-onboarding/README.md
- docs/ai-onboarding/coordinator-spec.md
- docs/ai-onboarding/hard-constraints.md
- docs/ai-onboarding/failure-recovery.md
- docs/ai-onboarding/onboarding-state.schema.json

# 工作模式
1. 串行单 provider。state 文件 docs/ai-onboarding/onboarding-state.json 中
   active_provider 同时只能有 1 个 in_progress。
2. DAG 顺序固定 (coordinator-spec.md §Task DAG)：
   discover-manifest → validate-manifest → capture-fixtures → scaffold →
   implement-provider → snapshot-expected → manifest-sync-back →
   provider-local-acceptance → global-lint → merge-ready。
3. discover-manifest 与 implement-provider 必须派子 agent。其它步骤由本会话直接执行脚本。
4. 不准在本会话中 import 或调用任何 LLM SDK；LLM 调用只能通过 CLI 的子 agent 机制。
5. 子 agent brief 必须含 no_commit: true；commit 在 merge-ready 由本会话统一执行。
6. 派子 agent 时，prompt 必须只包含 brief + schema/manifest + hard-constraints；
   不得附加 README、audit、聊天记录或自然语言导览。
7. 失败处理只按 failure-recovery.md 中的 error code 路由；stderr 自然语言不算输入。
   每个 worker task 最多重试 3 次；超额则 provider 状态置为 blocked。

# 启动
请先执行：
  python3 scripts/onboard_from_manifests.py start \
    --provider <NAME> --domain <DOMAIN> \
    --output-dir docs/ai-onboarding/runs/<NAME>

随后按 DAG 推进：
- 第 1 步 discover-manifest：派子 agent，prompt 见 docs/ai-onboarding/operator-prompts.md §B。
- 第 5 步 implement-provider：派子 agent，prompt 见 docs/ai-onboarding/operator-prompts.md §C。
- 其它步骤：调用对应脚本，跑 verify → advance。

每步完成或失败均输出 task_id、状态和（失败时）structured error code。
```

`PROVIDER_ONBOARDING_AGENT_CLI` env 在启动前由 operator 设定；本 prompt 不写具体 CLI 品牌。

## B. discover-manifest Worker Prompt

`scripts/onboard_from_manifests.py start --provider <NAME> --domain <DOMAIN> --output-dir ...` 会写出 `briefs/discover-manifest.yml`。Operator 把下方模板贴入子 agent 任务，并把三处 `<<<...>>>` 占位替换为对应文件完整内容。

```text
你是 discover-manifest worker。只按下方 task brief 执行，不读其它文件来推断 provider 行为。
你只允许写 brief 中 files_allowed_to_modify 列出的 YAML 文件；任何其它路径（src/、tests/、
docs/providers.md、CHANGELOG.md、fixture 目录、provider 实现模块、共享 onboarding 文档）
一律禁止写。不准 commit。

# 任务目标
按 brief 中 search_requirements 收集证据，把 <NAME> 的 ProviderManifest YAML 写到
brief 中 output_manifest 指向的路径，并通过 schema 校验。

# 证据要求 (manifest-discovery.md §Search Evidence Requirements / §DOI Sample Evidence)
- routing.doi_prefixes、routing.domains、routing.domain_suffixes、
  routing.crossref_publisher 各至少 1 条 evidence_url + evidence_reason。
- fixtures.doi_samples 必须含 brief 中 search_requirements.doi_sample_purposes 全部 purpose。
- 每个 sample 对象固定 5 个字段：doi、evidence_url、evidence_reason、observed_signals、
  confidence。confidence ∈ {high, medium, low}。
- structure / figure / references 三个 purpose 不允许 doi: null。
- 其它 purpose 找不到样本时允许 doi: null，但 evidence_reason 必须写明搜索失败原因。
- 禁止写入 TODO / TBD / unknown 占位；未知字段用 schema 允许的 null 或省略表达。
- 不准把 API key、token、FlareSolverr endpoint URL 写进 manifest。
- 必须写 generation.generated_by=ai_discovery、generated_at(ISO8601)、source_queries
  (实际搜过的 query 列表)、confidence。

# 失败信号 (manifest-discovery.md §Retry Rules)
不要输出 traceback。无法完成时停止并报告下列 code 之一：
MANIFEST_DISCOVERY_FAILED / MANIFEST_SCHEMA_INVALID /
MANIFEST_PROVIDER_CONFLICT / UNSUITABLE_DOI_SAMPLE。

# task brief
<<<贴 docs/ai-onboarding/runs/<NAME>/briefs/discover-manifest.yml 全文>>>

# manifest schema
<<<贴 docs/ai-onboarding/provider-manifest.schema.json 全文>>>

# hard constraints
<<<贴 docs/ai-onboarding/hard-constraints.md 全文>>>

# 完成后输出
1. 写入的 manifest 路径。
2. 每个 doi_samples.<purpose> 的 doi 与 confidence。
3. generation.source_queries 列表。
4. 自检：是否所有 brief required 字段都存在、是否仍有 TODO/TBD/unknown 占位。
不要 commit；改动留在工作区。
```

Coordinator 收回后必须跑：

```bash
PYTHONPATH=src python3 -m pytest \
  tests/unit/test_provider_manifest_schema.py \
  tests/unit/test_known_providers_sync.py -q
python3 scripts/onboard_from_manifests.py advance --provider <NAME> --task discover-manifest
```

## C. implement-provider Worker Prompt

走到第 5 步前，coordinator 已经执行过 `validate-manifest`、`capture-fixtures`、`scaffold`（脚本动作，不派子 agent），并产出 `briefs/implement-provider.yml`、`docs/ai-onboarding/scaffold/<NAME>.json`、`docs/ai-onboarding/capture-commands/<NAME>.txt`。Operator 把下方模板贴入子 agent 任务，并替换 `<<<...>>>` 占位。

```text
你是 implement-provider worker。只在 brief 中 files_allowed_to_modify 列出的文件里写代码；
不准 touch files_must_not_modify 中任何路径（manifest、known-providers.yml、shared docs、
provider_catalog.py、provider_rules.py、html_signals.py、html_availability.py）。
不准 commit。Provider 行为唯一输入是下方 manifest；不准从 docs/provider-development.md、
docs/adding-a-provider.md、README、audit 文件或聊天记录推断 provider 行为。

# 任务目标
让 brief 中 acceptance.pytest 全部通过，并使 acceptance.grep_must_be_empty 中每条命令的
匹配数为 0。

# 强制 Markdown Review Loop
1. 先对 manifest 中每个 non-null `fixtures.doi_samples.<purpose>` 生成 baseline Markdown。
2. 逐个 fixture 阅读 Markdown，记录 `fixture/purpose -> issue -> assertion -> fix`。
3. 每个发现的问题必须先转成 `tests/unit/test_<NAME>_provider.py` 里的 provider-local 断言。
4. 主成功路径必须同时有 Markdown 正断言和站点 chrome / access noise / boilerplate 负断言。
5. 优先复用已有 provider 测试断言模式；不要保留 scaffold skipped placeholder 或 review-loop placeholder。
6. 修复只能写 brief 允许的 provider-owned 文件；不要把清洗规则写到中心模块。
7. 重复生成 / 阅读 / 写断言 / 修 provider，直到所有 non-null fixture Markdown 干净。

# 实现约束 (hard-constraints.md §Provider Logic)
- Provider routing / asset profile / probe / fixture purpose / docs source name
  必须从 manifest 字段读取；禁止硬编码到中心模块。
- 不允许在 provider_rules.py / html_signals.py / html_availability.py 增加
  provider-specific 函数或 if name == "<NAME>" 分支。
- waterfall_steps 顺序按 manifest 的 main_path / pdf_fallback / abstract_only_strategy
  推导，与 scaffold 生成的占位顺序一致。
- 不准写 API key / token / FlareSolverr endpoint URL；secrets 只从 env 读。
- 不准保留 # TODO / # kept for compatibility 长期 marker。
- extraction_hints.* / success_criteria.* / asset_retry / metadata_merge 是 sync-back
  字段，禁止手 edit；由 scripts/manifest_sync_back.py 在后续步骤回写。

# 失败处理 (failure-recovery.md)
brief 中 failure_recovery.max_retries = 3。
- acceptance.pytest 失败：自查并改 brief 允许的文件再跑。
- 触碰 files_must_not_modify：报告 WORKER_MODIFIED_FORBIDDEN_FILE 并停止。
- 卡住：报告对应 code，并停止；不要绕过 pytest 或 grep。

# task brief
<<<贴 docs/ai-onboarding/runs/<NAME>/briefs/implement-provider.yml 全文>>>

# hard constraints
<<<贴 docs/ai-onboarding/hard-constraints.md 全文>>>

# provider manifest
<<<贴 docs/ai-onboarding/manifests/<NAME>.yml 全文>>>

# 完成后输出
1. 改动文件清单（带 +/- 行数）。
2. acceptance.pytest 最后几行（含 passed 计数）。
3. 每条 acceptance.grep_must_be_empty 的命令与实际命中数（必须为 0）。
4. `reviewed_fixtures` 摘要：每个 non-null purpose 的 fixture、发现的问题、对应断言和修复。
5. 未解决的失败（如有）与对应 error code。
不要 commit；改动留在工作区。
```

Coordinator 收回后必须跑（按 brief 中 acceptance 段定义）：

```bash
PYTHONPATH=src python3 -m pytest \
  tests/unit/test_<NAME>_provider.py \
  tests/unit/test_provider_markdown_review_contract.py \
  tests/unit/test_provider_bundle_completeness.py \
  tests/unit/test_provider_owner_reuse.py -q
python3 scripts/onboard_from_manifests.py advance --provider <NAME> --task implement-provider
```

## Operator Checklist

每次接入新 provider，operator 必须：

1. 设定 `PROVIDER_ONBOARDING_AGENT_CLI` 环境变量并打开主会话。
2. 贴入 §A 模板，替换 `<NAME>` / `<DOMAIN>`。
3. 在 §B 模板中替换 `<NAME>` 与三处 `<<<...>>>` 占位，派 discover-manifest 子 agent。
4. 由主会话执行 `validate-manifest` / `capture-fixtures` / `scaffold` 脚本动作。
5. 在 §C 模板中替换 `<NAME>` 与三处 `<<<...>>>` 占位，派 implement-provider 子 agent。
6. 由主会话执行 `snapshot-expected` / `manifest-sync-back` / `provider-local-acceptance` / `global-lint` / `merge-ready`。

Operator 不得修改模板中已写明的固定字段（`task_id` 形态、`runtime: coding-agent-subagent`、`no_commit: true`、`failure_recovery.max_retries: 3`）。修改这些字段的唯一方式是改 `agent-task-brief.md` 与 `onboard_from_manifests.py` 的 brief 生成逻辑。
