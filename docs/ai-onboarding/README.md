# Provider Onboarding —— 非工程读者入口

> 这份文档给业务方、产品、运营、新人工程师读。读完后，你应该能判断：接入一个新学术出版商时，人需要提供什么种子信息，AI 会自动做哪些事，最终 PR 应该 review 什么。
>
> 如果你要改基础设施本身，跳到最后的「给工程读者的索引」。

## 这是什么 / 为什么有这套东西

paper-fetch-skill 接入一个新学术 provider 时，需要回答几类问题：

- 这个 provider 用什么 DOI 前缀、域名、publisher alias 路由
- 它的主路径是 HTML、XML、PDF fallback，还是摘要兜底
- 哪些真实 DOI 能代表正文、表格、公式、图、补充材料、参考文献、access gate 等场景
- 代码实现后，manifest、bundle、fixture、expected snapshot 是否一致

这套 AI onboarding 基础设施把这些问题拆成两段：

- AI discovery worker 先搜索公开文献和 landing page，自动生成带证据的 manifest
- implementation worker 再只按 manifest 和 fixture 写 provider 私有代码

人不再手写 manifest。人的输入降到 provider 种子，比如名称、域名或 DOI 前缀；人的主要职责变成最后 review PR。

## 角色一览

| 角色 | 是谁 | 干什么 |
|---|---|---|
| **Provider seed owner**（人） | 你 / 业务方 / 运营 | 提供 provider 名称，必要时补一个域名或 DOI 前缀 |
| **Coordinator**（机器+人混合） | 一个 coding agent CLI 长跑会话，操作员是人 | 维护单 provider 串行 DAG，生成 task brief，跑脚本、pytest、grep 和 retry |
| **Discovery worker**（机器） | Coordinator 派出的子 agent | 搜索公开文献、Crossref/OpenAlex/DOI landing page，生成 `manifests/<name>.yml` |
| **Implementation worker**（机器） | Coordinator 派出的另一个子 agent | 只根据 manifest、fixture、hard constraints 填 provider 私有代码 |
| **Reviewer**（人） | 你 / 团队 | 看 manifest evidence、fixture snapshot、能力矩阵和 diff summary |

## 接入一个 provider 的 10 步流水线

### Step 1. 提供 provider 种子

人给 coordinator 一个最小入口：

```bash
python3 scripts/onboard_from_manifests.py start \
  --provider mdpi \
  --domain mdpi.com \
  --dry-run \
  --output-dir /tmp/onboard_mdpi
```

也可以提供 DOI 前缀：

```bash
python3 scripts/onboard_from_manifests.py start \
  --provider mdpi \
  --doi-prefix 10.3390/ \
  --dry-run \
  --output-dir /tmp/onboard_mdpi
```

已有 manifest 的历史回放仍可用：

```bash
python3 scripts/onboard_from_manifests.py start \
  --manifest docs/ai-onboarding/manifests/mdpi.yml \
  --dry-run \
  --output-dir /tmp/onboard_mdpi_replay
```

### Step 2. AI discovery 生成 manifest

Coordinator 派 discovery worker。这个 worker 只允许写：

```text
docs/ai-onboarding/manifests/mdpi.yml
```

它会搜索 provider 页面、公开论文页面和 DOI landing page，填出：

- routing：DOI 前缀、域名、publisher alias
- main path：HTML / XML / PDF fallback / abstract-only
- fixture DOI samples：正文、表格、公式、图、补充材料、参考文献、PDF fallback、access gate 等 purpose
- evidence：每个 DOI 为什么代表这个 purpose

manifest 中每个 DOI 样本都必须带证据：

```yaml
fixtures:
  doi_samples:
    structure:
      doi: "10.3390/membranes15030093"
      evidence_url: "https://www.mdpi.com/..."
      evidence_reason: "Landing page exposes a normal article body with headings and figures."
      observed_signals: ["html_body", "figures", "references"]
      confidence: high
```

### Step 3. 校验 manifest

Coordinator 跑 schema validate 和同步 lint。失败时不会继续抓 fixture；它会按结构化错误回派 discovery worker 修 manifest，或把 provider 标成 `blocked`。

### Step 4. 抓 fixture

Coordinator 按 manifest 中的 DOI samples 调：

```bash
python3 scripts/capture_fixture.py \
  --doi 10.3390/membranes15030093 \
  --provider mdpi \
  --purpose structure \
  --from-manifest docs/ai-onboarding/manifests/mdpi.yml \
  --fail-fast
```

如果某个 DOI 选错了，脚本返回 `UNSUITABLE_DOI_SAMPLE`，coordinator 会回派 discovery worker 替换这个 purpose 的 DOI。

### Step 5. 生成代码骨架

Coordinator 跑：

```bash
python3 scripts/scaffold_provider.py \
  --from-manifest docs/ai-onboarding/manifests/mdpi.yml
```

骨架会从 manifest 生成 provider 模块、测试 starter、fixture capture 清单和 docs 占位。

### Step 6. Implementation worker 填代码

Coordinator 派 implementation worker。输入只包含：

- manifest YAML 全文
- 已抓好的 fixture
- scaffolded file paths
- hard constraints
- files allowed / forbidden lists

Implementation worker 不能 commit，不能改中心模块，不能改其他 provider 的 manifest。

### Step 7. 生成 expected snapshot + 回写 manifest

Coordinator 跑：

- `snapshot_expected.py`：把 provider 对 fixture 的当前产出固化成 expected snapshot
- `manifest_sync_back.py`：把代码里的 signal set、asset retry、metadata merge、success criteria 回写 manifest

这一步之后，manifest 从“AI discovery 输入种子”变成“输入种子 + 实现事实底稿”。

### Step 8. Provider 局部验收

Coordinator 跑该 provider 的局部 pytest 和 hard constraints grep。失败时，把失败输出回派 implementation worker；超过 retry 上限就停在 `blocked`。

### Step 9. 全局体检

Coordinator 跑全局 lint：

- manifest 和 code/bundle 是否一致
- provider 是否越界修改中心模块
- owner reuse grep 是否通过
- docs 占位是否填实
- 旧 provider 是否被破坏

### Step 10. 准备 PR

通过后，coordinator 把 manifest 状态从 `draft` 改成 `ready`，写 `known-providers.yml`，准备 `feat/provider-<name>` 分支和 PR summary。

## 人最后 review 什么

Review 重点不是逐行看 worker 怎么写代码，而是看这几件事：

1. Discovery evidence 是否合理：每个 DOI sample 的 `evidence_reason` 和 `observed_signals` 是否能解释它为什么代表该 purpose。
2. Fixture snapshot 是否合理：title、authors、abstract、section、figure/table/formula 数量是否明显异常。
3. Manifest sync-back 是否合理：signal set、success criteria、asset retry 是否和 provider 页面行为一致。
4. 用户可见文档是否准确：能力矩阵、provider 说明、CHANGELOG 是否没有夸大能力。

## 失败的兜底

Coordinator 不按自然语言猜失败原因，只按结构化 error code 决策：

- `MANIFEST_DISCOVERY_FAILED`：重派 discovery worker，或标记 provider blocked
- `MANIFEST_SCHEMA_INVALID`：让 discovery worker 修 manifest 字段
- `UNSUITABLE_DOI_SAMPLE`：让 discovery worker 替换对应 purpose 的 DOI
- `WORKER_MODIFIED_FORBIDDEN_FILE`：revert 越界文件并重派 worker
- `MANIFEST_CODE_DRIFT`：重派 implementation worker 修代码，sync-back 字段不能手改
- `TASK_RETRY_EXHAUSTED`：暂停该 provider，等待人工裁决

## 严格串行

Coordinator 一次只处理一个 provider。跑完当前 provider 的 `merge-ready`，才能开始下一个。

这个约束避免了共享文件冲突，也避免多个 provider 同时抓同一网站导致 rate limit。

## 给工程读者的索引

| 文件 | 内容 |
|---|---|
| [`provider-manifest.schema.json`](./provider-manifest.schema.json) | manifest 字段的 JSON Schema |
| [`provider-manifest.md`](./provider-manifest.md) | manifest 字段逐项解释 |
| [`manifest-discovery.md`](./manifest-discovery.md) | discovery worker 搜索证据和生成 manifest 的规则 |
| [`hard-constraints.md`](./hard-constraints.md) | worker 不可违反的 grep / pytest 约束 |
| [`failure-recovery.md`](./failure-recovery.md) | 结构化 error code 到恢复动作的映射 |
| [`coordinator-spec.md`](./coordinator-spec.md) | DAG、串行执行、retry、branch、merge、blocked 策略 |
| [`agent-task-brief.md`](./agent-task-brief.md) | worker 输入格式 |
| [`acceptance.md`](./acceptance.md) | provider PR 完成定义 |
| [`known-providers.yml`](./known-providers.yml) | 已接入 provider 机器索引 |
| [`manifests/<name>.yml`](./manifests/) | 单 provider 事实底稿 |
| [`../../provider-onboarding-standardization-audit.md`](../../provider-onboarding-standardization-audit.md) | 整套基础设施的规约 |

README 只作为入口说明。Schema 字段、grep 模式、pytest 命令和 error code 细节放在本目录的专门文件里。
