# Failure Recovery

Coordinator 按 stderr JSON 的 `code` 选择恢复动作。每个 signal 的 `action` 是确定性步骤。

## Signal: MANIFEST_NOT_FOUND

diagnosis: 指定 manifest 路径不存在。
action: 将 provider 状态置为 `blocked`，记录缺失路径到 state。
retryable: false

## Signal: MANIFEST_DISCOVERY_FAILED

diagnosis: discovery worker 未产出合规 manifest。
action: 重派 `discover-manifest`；超过 retry budget 后将 provider 状态置为 `blocked`。
retryable: true

## Signal: MANIFEST_SCHEMA_INVALID

diagnosis: manifest 未通过 schema 或 YAML 结构校验。
action: 把 stderr JSON 的 `details.field` 和 `details.expected` 回派给 `discover-manifest`，只修 manifest 字段。
retryable: true

## Signal: MANIFEST_PROVIDER_CONFLICT

diagnosis: manifest provider 与已登记 provider 或当前 active provider 冲突。
action: 将 provider 状态置为 `blocked`，记录冲突 provider 到 state。
retryable: false

## Signal: MANIFEST_CODE_DRIFT

diagnosis: manifest 与 provider 代码、known-providers 或 bundle 注册状态不一致。
action: 重派 `implement-provider` 修 provider 代码；sync-back 字段只由 `manifest_sync_back.py` 写入。
retryable: true

## Signal: SCAFFOLD_OUTPUT_EXISTS

diagnosis: scaffold 目标文件、fixture sample 或 manifest entry 已存在。
action: 将当前 provider 状态置为 `blocked`，记录已存在路径或 sample id。
retryable: false

## Signal: SCAFFOLD_TEMPLATE_RENDER_FAILED

diagnosis: scaffold 模板输入无法渲染为 provider 文件、测试或文档占位。
action: 重派 `discover-manifest` 修导致渲染失败的 manifest 字段。
retryable: true

## Signal: SCAFFOLD_FORBIDDEN_FLAG_COMBINATION

diagnosis: scaffold CLI 同时收到 manifest mode 与 legacy flags，或缺少 legacy 必填 flag。
action: 使用单一输入模式重跑 scaffold；manifest mode 只保留 `--from-manifest` 和输出控制 flags。
retryable: true

## Signal: UNSUITABLE_DOI_SAMPLE

diagnosis: DOI sample 缺失、不适合当前 purpose，或 capture 目标会覆盖已有 fixture。
action: 重派 `discover-manifest`，只替换失败 purpose 的 `fixtures.doi_samples.<purpose>` 对象。
retryable: true

## Signal: HTTP_FORBIDDEN

diagnosis: capture 请求返回 HTTP 403。
action: 若 manifest `probe.requires_browser_runtime=true`，用浏览器运行时重跑；否则重派 `discover-manifest` 替换该 purpose DOI。
retryable: true

## Signal: HTTP_RATE_LIMITED

diagnosis: capture 请求返回 HTTP 429。
action: 按 provider retry budget 重跑 capture；超过 budget 后发出 `TASK_RETRY_EXHAUSTED`。
retryable: true

## Signal: CHALLENGE_DETECTED

diagnosis: capture 响应包含 challenge 或 CAPTCHA 页面。
action: 用 `--retry-via=browser` 重跑 capture；失败后重派 `discover-manifest` 替换该 purpose DOI。
retryable: true

## Signal: BROWSER_RUNTIME_REQUIRED

diagnosis: 当前 capture 路线需要 browser runtime。
action: 将 provider 状态置为 `blocked`，required runtime 类型记录在 `details.route`。
retryable: false

## Signal: NON_PDF_FALLBACK_CONTENT

diagnosis: `pdf_fallback` sample 返回非 PDF 内容。
action: 重派 `discover-manifest`，替换 `fixtures.doi_samples.pdf_fallback`。
retryable: true

## Signal: ACCESS_GATE_CAPTURED

diagnosis: 非 `access_gate` purpose 捕获到 access gate 页面。
action: 重派 `discover-manifest`，替换失败 purpose DOI；`access_gate` purpose 保持不变。
retryable: true

## Signal: EMPTY_ARTICLE_SHELL

diagnosis: 非 `empty_shell` purpose 捕获到空文章壳。
action: 重派 `discover-manifest`，替换失败 purpose DOI；`empty_shell` purpose 保持不变。
retryable: true

## Signal: NETWORK_TRANSIENT

diagnosis: DNS、TLS、timeout 或 HTTP 5xx 导致 capture 暂态失败。
action: 按 provider retry budget 重跑 capture；超过 budget 后发出 `TASK_RETRY_EXHAUSTED`。
retryable: true

## Signal: EXPECTED_SNAPSHOT_FAILED

diagnosis: snapshot expected 生成过程失败。
action: 重跑 `snapshot-expected`；若同一 fixture 再次失败，重派 `implement-provider` 修 provider replay 行为。
retryable: true

## Signal: EXPECTED_OUTCOME_PENDING

diagnosis: fixture manifest 的 expected outcome 仍为 `pending`，不能进入 acceptance。
action: 运行 `scripts/snapshot_expected.py` 为该 DOI 写入 `expected.json` 并同步 manifest outcome。
retryable: true

## Signal: FIXTURE_NOT_FOUND

diagnosis: manifest 未登记 DOI sample，或 fixture 原始资产不存在。
action: 重派 `capture-fixtures` 补齐原始资产；manifest 未登记时重派 `discover-manifest` 添加该 DOI sample。
retryable: true

## Signal: TASK_BRIEF_INVALID

diagnosis: coordinator brief、provider slug、state 或 task id 无效。
action: 重建当前 provider 的 DAG、brief 和 state 后重跑当前 coordinator command。
retryable: true

## Signal: WORKER_MODIFIED_FORBIDDEN_FILE

diagnosis: worker 修改了 brief 禁止写入的文件。
action: 丢弃 forbidden-path diff，重派当前 worker step，并把 code 写入 worker retry record。
retryable: true

## Signal: DISCOVERY_RETRY_EXHAUSTED

diagnosis: `discover-manifest` retry budget 已用尽。
action: 将 provider 状态置为 `blocked`，停止该 provider pipeline。
retryable: false

## Signal: TASK_RETRY_EXHAUSTED

diagnosis: 当前 retryable task 的 retry budget 已用尽。
action: 将 provider 状态置为 `blocked`，停止该 provider pipeline。
retryable: false

## Signal: GLOBAL_LINT_FAILED

diagnosis: global lint 或 cross-provider unit 验证失败。
action: 重派 `implement-provider`，只修当前 provider 引入的失败。
retryable: true
