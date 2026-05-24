# AI Provider Onboarding Authority

`onboarding/` 是 AI/coordinator provider onboarding 的唯一权威输入目录。Worker 输入由 coordinator task brief、provider manifest、hard constraints 和 failure recovery signal 组成；`docs/provider-development.md`、`docs/adding-a-provider.md`、README、audit 文件和聊天记录不得作为 AI worker 的 provider 行为输入。

## Authority Boundary

- `scripts/onboard_from_manifests.py` 负责 coordinator DAG、state、task brief 和 verification plan。
- `onboarding/access-reviews/<name>.yml` 是 operator 对合法访问、runtime、challenge/CAPTCHA 和临时站点策略的批准记录；未批准时 coordinator 不进入 discovery。
- `onboarding/provider-manifest.schema.json` 定义 provider manifest schema。
- `onboarding/manifests/<name>.yml` 是单 provider 的 routing、fixture、probe、asset profile、sync-back 和 docs fact base。
- `onboarding/manifests/<name>.yml` 中的 `fixtures.discovery_proof` 强制记录 `table`、`formula`、`supplementary` 的候选检索矩阵；`validate-manifest` 会阻断缺失 proof、query 不足、`selected_doi` 不一致，以及与本地 fixture/cleaning evidence 矛盾的 null purpose。
- `onboarding/reviews/<name>.yml` 是 fixture 代表性和最终 Markdown 语义审查 artifact；acceptance 不接受只写在 worker 回复里的审查结果。
- `onboarding/instruction.md` 是可复用 `/goal` provider onboarding 执行入口，按 manifest contract 驱动从零新增或继续实现 provider。
- `onboarding/runbook.md` 是不同使用场景的入口索引，说明从零实现、已有 manifest 继续、查漏补缺、单 DOI quality repair 和 blocked state 恢复该用哪条命令。
- `scripts/capture_fixture.py --from-manifest <manifest> --all` 批量捕获所有 non-null DOI sample 和 `extra_fixtures`，自动跳过 null DOI purpose，并用 structured JSON error code 报告不可用样本。
- `scripts/capture_fixture.py --from-manifest <manifest> --all --auto-via --fail-fast` 是 coordinator capture 默认入口，按 manifest probe 和 access review 选择 `http` / `browser`。
- `scripts/scaffold_provider.py --from-manifest --merge-existing=safe` 负责从 manifest 生成 provider-owned skeleton；已有输出时复用安全内容或返回 JSON merge plan 和 diff preview。
- `scripts/bootstrap_review_artifact.py --provider <name> --manifest <path>` 生成 Markdown review 草稿，但不会把最终语义审查自动签为 true。
- `scripts/backfill_access_reviews.py --all --write` 为已实现但缺少 access review 的 provider 生成 blocked 草稿；草稿不是批准，`status: approved` 和 `may_continue: true` 仍只能由 operator 写入。
- `scripts/propose_cleaning_chain.py --provider <name> --write` 基于已提交 fixture 生成 `cleaning-chain-proposals/<name>.yml`，用于 capture 后、implement 前的清洗候选、contract delta、过度清洗探针和 token 冲突检查。
- `scripts/manifest_sync_back.py --provider <name> --manifest <path> --sync-docs` 是 `extraction_hints`、`success_criteria` 和 manifest docs facts 自动同步入口。
- `scripts/onboard_from_manifests.py diagnose`、`resume-blocked` 和 `summarize` 读取 coordinator state，分别提供 blocked 分诊、受控续跑和 operator digest；它们不会批准 access review、解决 challenge 或触发 GitHub CI。
- `scripts/run_provider_drift_report.py` 是本地手动 route-source drift report 入口；真实 live run 必须显式设置 `PAPER_FETCH_RUN_LIVE=1`，不接 CI。
- `onboarding/failure-recovery.md` 定义 coordinator 对 structured JSON error code 的恢复动作。
- `onboarding/automation-roadmap.md` 记录 runner、worker dispatch、live gate 和不可自动化边界。

## S14-S16 Toolchain

S14/S17 coordinator:

- local entrypoint: `python3 scripts/onboard_from_manifests.py`
- supported actions: `start`, `run`, `diagnose`, `resume-blocked`, `summarize`, `next`, `verify`, `run-checks`, `check-snapshot`, `repair-markdown-quality`, `advance`
- provider execution model: one active provider, serial task DAG, retry counters per task
- worker dispatch input: generated task brief plus manifest/hard-constraints material; default dispatcher is local `codex exec`, with `PROVIDER_ONBOARDING_AGENT_CLI` available as an operator override
- full automation entrypoint: `python3 scripts/onboard_from_manifests.py run --manifest onboarding/manifests/<name>.yml --until merge-ready`

S15 manifest capture/retry:

- discovery output: `onboarding/manifests/<name>.yml`
- capture entrypoint: `python3 scripts/capture_fixture.py --from-manifest onboarding/manifests/<name>.yml --all --auto-via --fail-fast`
- fixture retry routing: `UNSUITABLE_DOI_SAMPLE`, `HTTP_FORBIDDEN`, `HTTP_RATE_LIMITED`, `CHALLENGE_DETECTED`, `NON_PDF_FALLBACK_CONTENT`, `ACCESS_GATE_CAPTURED`, `EMPTY_ARTICLE_SHELL`, `NETWORK_TRANSIENT`
- retry target: replace only the failed `fixtures.doi_samples.<purpose>` object or rerun the failed capture step according to `failure-recovery.md`

S16 structured error:

- tool failures return stderr JSON with stable `code`
- coordinator routes by `code`, not by natural-language stderr
- accepted signal namespace is documented in `failure-recovery.md`
- blocked tasks record the signal code and deterministic action in coordinator state

## Coordinator DAG

The provider DAG is fixed:

1. `operator-access-preflight`
2. `discover-manifest`
3. `validate-manifest`
4. `capture-fixtures`
5. `propose-cleaning-chain`
6. `scaffold`
7. `implement-provider`
8. `shared-integration`
9. `snapshot-expected`
10. `manifest-sync-back`
11. `provider-local-acceptance`
12. `global-lint`
13. `merge-ready`

`operator-access-preflight` validates `onboarding/access-reviews/<name>.yml` before discovery. `validate-manifest` validates schema plus discovery proof sufficiency for `table`、`formula`、`supplementary`; null optional purposes are accepted only after an exhausted candidate search is recorded and local evidence does not contradict it. `propose-cleaning-chain` runs after fixture capture and before scaffold, writing compact proposal/evidence artifacts bound to fixture digests. `start --provider` includes all 13 tasks and writes discovery plus implementation briefs. `start --manifest` skips `discover-manifest`, reads provider identity from manifest YAML, and still starts with the access preflight gate.

## Required Verification

Merge-ready requires machine checks, not narrative review:

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_provider_manifest_schema.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_manifest_bundle_sync.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_provider_bundle_completeness.py tests/unit/test_provider_owner_reuse.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_provider_markdown_review_contract.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_provider_route_contract.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_human_docs_drift.py -q
python3 scripts/validate_extraction_rules.py
```

For local operator execution, `python3 scripts/onboard_from_manifests.py run-checks --provider <name> --all-local` runs the access, manifest, review/provider-local, shared integration, and global lint gates without triggering GitHub CI.

For end-to-end local orchestration, `python3 scripts/onboard_from_manifests.py run --provider <name> --domain <domain> --output-dir .paper-fetch-runs/<name>-onboarding` executes the serial DAG and dispatches worker steps through local `codex exec` by default, or through `PROVIDER_ONBOARDING_AGENT_CLI` when the operator sets an override. It still cannot approve access, solve challenges, or mark semantic review complete.

Provider-local acceptance commands must come from the generated task brief. They include `check-cleaning-proposal` freshness validation and `scripts/propose_cleaning_chain.py --provider <name> --check-contract`; warning-only sentinel/cross-route findings pass, while stale digests or blocking contract drift fail with `MARKDOWN_CONTRACT_DRIFT`. Hard-constraint grep checks must be listed in the brief or `hard-constraints.md`; non-empty forbidden central-provider matches fail acceptance.

## File Index

| File | Authority |
|---|---|
| [`agent-task-brief.md`](./agent-task-brief.md) | Required fields for discovery and implementation worker briefs |
| [`operator-prompts.md`](./operator-prompts.md) | Operator-facing prompt templates for coordinator session and worker dispatch |
| [`coordinator-spec.md`](./coordinator-spec.md) | Coordinator invocation, DAG, state machine, retry and worker isolation |
| [`automation-roadmap.md`](./automation-roadmap.md) | 可自动化项、不可自动化边界、runner、worker dispatch、live gate 和 failure recovery 映射 |
| [`failure-recovery.md`](./failure-recovery.md) | Structured JSON error `code` to deterministic recovery action |
| [`hard-constraints.md`](./hard-constraints.md) | Worker scope, provider logic boundary, pytest and grep acceptance |
| [`instruction.md`](./instruction.md) | 通用 `/goal follow onboarding/instruction.md 添加 <provider> provider` 执行入口 |
| [`runbook.md`](./runbook.md) | 场景化入口索引：从零实现、继续已有 manifest、查漏补缺、quality repair 和 blocked 恢复 |
| [`manifest-discovery.md`](./manifest-discovery.md) | Discovery worker input, evidence requirements, schema output and retry rules |
| [`acceptance.md`](./acceptance.md) | Machine-verifiable merge-ready definition |
| [`access-review.schema.json`](./access-review.schema.json) | Operator access preflight JSON Schema |
| [`provider-review.schema.json`](./provider-review.schema.json) | Fixture representativeness and Markdown semantic review JSON Schema |
| [`provider-manifest.md`](./provider-manifest.md) | Provider manifest field reference |
| [`provider-manifest.schema.json`](./provider-manifest.schema.json) | Provider manifest JSON Schema |
| [`onboarding-state.schema.json`](./onboarding-state.schema.json) | Coordinator state JSON Schema |
| [`known-providers.yml`](./known-providers.yml) | Provider manifest index and status registry |
| [`cleaning-chain-proposals/`](./cleaning-chain-proposals/) | Fixture-derived cleaning proposal artifacts; proposals do not modify provider implementation or semantic review signoff |
| [`manifests/elsevier.yml`](./manifests/elsevier.yml) | Elsevier provider manifest |
| [`manifests/springer.yml`](./manifests/springer.yml) | Springer provider manifest |
| [`manifests/wiley.yml`](./manifests/wiley.yml) | Wiley provider manifest |
| [`manifests/science.yml`](./manifests/science.yml) | Science provider manifest |
| [`manifests/pnas.yml`](./manifests/pnas.yml) | PNAS provider manifest |
| [`manifests/ieee.yml`](./manifests/ieee.yml) | IEEE provider manifest |
| [`manifests/arxiv.yml`](./manifests/arxiv.yml) | arXiv provider manifest |
| [`manifests/copernicus.yml`](./manifests/copernicus.yml) | Copernicus provider manifest |
| [`manifests/ams.yml`](./manifests/ams.yml) | AMS provider manifest |

## Legacy Human References

`docs/provider-development.md` and `docs/adding-a-provider.md` remain human references. They can describe background API usage, but AI/coordinator provider onboarding must use this directory as the input source. Drift between human references and AI authority is checked by `tests/unit/test_human_docs_drift.py`.
