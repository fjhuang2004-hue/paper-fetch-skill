# 冗余清理 Goal 执行说明

本文档用于直接启动全局 goal：

```text
/goal follow REDUNDANCY_GOAL.md
```

## 目标

按 `REDUNDANCY_ANALYSIS.md` 串行清理代码库冗余。主 agent 负责总控、分派、审查、整合和验证；
每个独立任务包由一个 subagent 完成。低风险项应尽量直接完成；高风险项如果测试覆盖不足、
会破坏公开 API，或需要跨任务包改动，应记录为 deferred，并给出最小后续拆分步骤。

## 固定约束

- 默认使用中文。
- 不触发 GitHub CI。
- 不回滚用户已有改动；遇到脏文件先判断是否与当前任务相关。
- 使用项目代码和已有 helper；不要自行另写已有成熟实现。
- 修改代码后同步相关文档。
- 默认使用 `PYTHONPATH=src python3 -m pytest ... -q`，并行复用项目 pytest 配置。
- live 测试、依赖外部状态的测试或排查顺序问题时才串行，并说明原因。
- 每个 subagent 必须只拥有一个明确写入范围；串行推进，主 agent 审查后再开下一项。

## 主 Agent 工作流

1. 读取 `AGENTS.md`、`REDUNDANCY_ANALYSIS.md` 和本文档。
2. 执行 `git status --short`，识别用户已有改动；只触碰当前任务包允许的文件。
3. 从 `REDUNDANCY_ANALYSIS.md` 的“串行 Subagent 推进索引”按 R00 到 R13 顺序推进。
4. 每个任务包启动一个 subagent。给 subagent 明确：
   - 任务 ID 和对应章节。
   - 允许写入的文件/目录。
   - 禁止触碰的文件/目录。
   - 需要运行的验证命令。
   - 完成后必须列出 changed files、tests run、remaining risks、deferred items。
5. 等待该 subagent 完成。主 agent 审查 diff、必要时小修整合，然后运行该任务包验证命令。
6. 更新 `REDUNDANCY_ANALYSIS.md` 中对应任务状态：`done`、`partial` 或 `deferred: 原因`。
7. 若任务跨出写入范围、和用户改动冲突、或风险高于预期，停止该任务，记录 deferred，不强行完成。
8. 完成全部低/中风险任务后运行最终验证，更新剩余风险和 deferred 清单。

## Subagent 分派模板

```text
你在 /home/dictation/paper-fetch-skill。你不是唯一的 agent，不要回滚或覆盖他人改动。

任务：REDUNDANCY_ANALYSIS.md 中 <任务ID>，对应 <章节>。

允许写入范围：
- <列出文件/目录>

禁止触碰：
- 任何不在允许范围内的文件
- 用户已有无关改动
- GitHub CI 触发行为

要求：
1. 先阅读相关章节和涉及文件。
2. 按现有代码风格实现最小安全改动。
3. 同步必要文档/测试。
4. 运行指定验证命令；如果不能运行，说明原因。
5. 最终回复必须包含：
   - changed files
   - tests run
   - remaining risks
   - deferred items
```

## 串行任务队列

主 agent 以 `REDUNDANCY_ANALYSIS.md` 的“串行 Subagent 推进索引”为唯一队列来源。
当前任务包如下：

| ID | 主 agent 处理方式 |
|---|---|
| R00 | 本地主 agent 执行，不需要 subagent；确认 worktree 和基线。 |
| R01 | 一个 worker：删除零消费者垫片和对应测试。 |
| R02 | 一个 worker：处理 split-module `_core.py` 聚合副本。 |
| R03 | 一个 worker：删除高置信死代码、死方法和未用常量。 |
| R04 | 一个 worker：收口 browser workflow / Playwright 旧命名。 |
| R05 | 一个 worker：MCP facade 和低风险 schema 去重。 |
| R06 | 一个 worker：provider/helper 低风险重复抽取。 |
| R07 | 一个 worker：公共 helper 中风险收敛。 |
| R08 | 一个 worker：测试 helper/stub/path 去重。 |
| R09 | 一个 worker：脚本、CI、installer、skill 文档漂移。 |
| R10 | 一个 worker：fixture/resource manifest 化和归档候选。 |
| R11 | 一个 worker 或主 agent：本地产物清理入口；不要直接删除用户本地数据，除非明确确认。 |
| R12 | 先让一个 worker 做最小安全方案；高风险项可 deferred，不强行重构。 |
| R13 | 主 agent 执行最终文档状态、测试汇总和剩余风险整理。 |

## Deferred 规则

以下情况必须 deferred，而不是强行修改：

- 需要同时修改多个任务包的写入范围。
- 需要删除或改变公开 API，但没有 deprecation 或测试保护。
- golden/fixture 行为不清楚，且没有足够本地测试验证。
- 用户已有改动与任务冲突，无法确定意图。
- 修改会要求 live 测试、网络访问或外部状态才能判断正确性。
- 高风险 orchestration 改动会影响 Wiley/Springer/MCP cache/metadata probe 的主流程。

Deferred 记录格式：

```text
deferred: <任务ID>/<条目> - <原因>；建议后续步骤：<最小拆分或所需测试>
```

## 验证策略

每个任务包优先运行 `REDUNDANCY_ANALYSIS.md` 索引中列出的 targeted tests。
完成若干互相关联任务后运行：

```bash
PYTHONPATH=src python3 -m pytest tests/unit -q
```

最终目标完成前运行：

```bash
PYTHONPATH=src python3 -m pytest tests/unit tests/integration -q
```

如果测试因环境问题失败，主 agent 应区分环境失败和代码回归，并把关键错误摘要写入最终汇报。

## 完成标准

目标完成时必须满足：

- `REDUNDANCY_ANALYSIS.md` 的串行索引状态已更新。
- 所有安全任务为 `done` 或 `partial`，无法安全完成的任务有明确 `deferred` 原因。
- 相关代码、测试、文档已同步。
- 已运行 targeted tests；最终验证已运行或说明无法运行的原因。
- 最终回复包含：
  - 完成的任务 ID。
  - 修改的主要文件。
  - 测试结果。
  - deferred 清单和后续建议。
