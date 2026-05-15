# AI Provider Onboarding Authority

`docs/ai-onboarding/` 是 AI/coordinator provider onboarding 的唯一权威输入目录。Worker 输入由 coordinator task brief、provider manifest、hard constraints 和 failure recovery signal 组成；`docs/provider-development.md`、`docs/adding-a-provider.md`、README、audit 文件和聊天记录不得作为 AI worker 的 provider 行为输入。

## Authority Boundary

- `scripts/onboard_from_manifests.py` 负责 coordinator DAG、state、task brief 和 verification plan。
- `docs/ai-onboarding/provider-manifest.schema.json` 定义 provider manifest schema。
- `docs/ai-onboarding/manifests/<name>.yml` 是单 provider 的 routing、fixture、probe、asset profile、sync-back 和 docs fact base。
- `scripts/capture_fixture.py --from-manifest` 负责按 manifest DOI sample 捕获 fixture，并用 structured JSON error code 报告不可用样本。
- `scripts/scaffold_provider.py --from-manifest` 负责从 manifest 生成 provider-owned skeleton。
- `scripts/manifest_sync_back.py --provider <name> --manifest <path>` 是 `extraction_hints` 和 `success_criteria` sync-back 字段的唯一 writer。
- `docs/ai-onboarding/failure-recovery.md` 定义 coordinator 对 structured JSON error code 的恢复动作。

## S14-S16 Toolchain

S14 coordinator:

- local entrypoint: `python3 scripts/onboard_from_manifests.py`
- supported actions: `start`, `next`, `verify`, `advance`
- provider execution model: one active provider, serial task DAG, retry counters per task
- worker dispatch input: generated task brief plus manifest/hard-constraints material

S15 manifest capture/retry:

- discovery output: `docs/ai-onboarding/manifests/<name>.yml`
- capture entrypoint: `python3 scripts/capture_fixture.py --from-manifest docs/ai-onboarding/manifests/<name>.yml`
- fixture retry routing: `UNSUITABLE_DOI_SAMPLE`, `HTTP_FORBIDDEN`, `HTTP_RATE_LIMITED`, `CHALLENGE_DETECTED`, `NON_PDF_FALLBACK_CONTENT`, `ACCESS_GATE_CAPTURED`, `EMPTY_ARTICLE_SHELL`, `NETWORK_TRANSIENT`
- retry target: replace only the failed `fixtures.doi_samples.<purpose>` object or rerun the failed capture step according to `failure-recovery.md`

S16 structured error:

- tool failures return stderr JSON with stable `code`
- coordinator routes by `code`, not by natural-language stderr
- accepted signal namespace is documented in `failure-recovery.md`
- blocked tasks record the signal code and deterministic action in coordinator state

## Coordinator DAG

The provider DAG is fixed:

1. `discover-manifest`
2. `validate-manifest`
3. `capture-fixtures`
4. `scaffold`
5. `implement-provider`
6. `snapshot-expected`
7. `manifest-sync-back`
8. `provider-local-acceptance`
9. `global-lint`
10. `merge-ready`

`start --provider` includes all tasks and writes discovery plus implementation briefs. `start --manifest` skips `discover-manifest`, reads provider identity from manifest YAML, and writes the implementation brief.

## Required Verification

Merge-ready requires machine checks, not narrative review:

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_provider_manifest_schema.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_manifest_bundle_sync.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_provider_bundle_completeness.py tests/unit/test_provider_owner_reuse.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_human_docs_drift.py -q
python3 scripts/validate_extraction_rules.py
```

Provider-local acceptance commands must come from the generated task brief. Hard-constraint grep checks must be listed in the brief or `hard-constraints.md`; non-empty forbidden central-provider matches fail acceptance.

## File Index

| File | Authority |
|---|---|
| [`agent-task-brief.md`](./agent-task-brief.md) | Required fields for discovery and implementation worker briefs |
| [`operator-prompts.md`](./operator-prompts.md) | Operator-facing prompt templates for coordinator session and worker dispatch |
| [`coordinator-spec.md`](./coordinator-spec.md) | Coordinator invocation, DAG, state machine, retry and worker isolation |
| [`failure-recovery.md`](./failure-recovery.md) | Structured JSON error `code` to deterministic recovery action |
| [`hard-constraints.md`](./hard-constraints.md) | Worker scope, provider logic boundary, pytest and grep acceptance |
| [`manifest-discovery.md`](./manifest-discovery.md) | Discovery worker input, evidence requirements, schema output and retry rules |
| [`acceptance.md`](./acceptance.md) | Machine-verifiable merge-ready definition |
| [`provider-manifest.md`](./provider-manifest.md) | Provider manifest field reference |
| [`provider-manifest.schema.json`](./provider-manifest.schema.json) | Provider manifest JSON Schema |
| [`onboarding-state.schema.json`](./onboarding-state.schema.json) | Coordinator state JSON Schema |
| [`known-providers.yml`](./known-providers.yml) | Provider manifest index and status registry |
| [`manifests/arxiv.yml`](./manifests/arxiv.yml) | arXiv provider manifest |
| [`manifests/copernicus.yml`](./manifests/copernicus.yml) | Copernicus provider manifest |
| [`manifests/wiley.yml`](./manifests/wiley.yml) | Wiley provider manifest |

## Legacy Human References

`docs/provider-development.md` and `docs/adding-a-provider.md` remain human references. They can describe background API usage, but AI/coordinator provider onboarding must use this directory as the input source. Drift between human references and AI authority is checked by `tests/unit/test_human_docs_drift.py`.
