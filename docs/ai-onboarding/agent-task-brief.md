# Agent Task Brief

Task brief 是 coordinator 派给 worker 的唯一输入。Worker 必须只按 brief 中的字段执行，不从 README、audit 文档或临时聊天记录推断额外写入范围。

## discover-manifest

`discover-manifest` brief 必须是 YAML object，并且必须包含下列 required keys：

```yaml
task_id: mdpi-discover-manifest
current_step: discover-manifest
runtime: coding-agent-subagent
provider_seed:
  name: mdpi
  domain: mdpi.com
  doi_prefix_hint: null
output_manifest: docs/ai-onboarding/manifests/mdpi.yml
schema: docs/ai-onboarding/provider-manifest.schema.json
hard_constraints: docs/ai-onboarding/hard-constraints.md
search_requirements:
  routing:
    - doi_prefixes
    - domains
    - domain_suffixes
    - crossref_publisher
  doi_sample_purposes:
    - structure
    - table
    - formula
    - figure
    - supplementary
    - references
    - pdf_fallback
    - abstract_only
    - access_gate
    - empty_shell
files_allowed_to_modify:
  - docs/ai-onboarding/manifests/mdpi.yml
files_must_not_modify:
  - src/
  - tests/
  - docs/providers.md
  - CHANGELOG.md
no_commit: true
```

### Required Keys

- `task_id` must be `<provider>-discover-manifest`.
- `current_step` must be `discover-manifest`.
- `runtime` must be `coding-agent-subagent`.
- `provider_seed.name` must be the normalized provider id.
- `provider_seed.domain` may be null, but the key must exist.
- `provider_seed.doi_prefix_hint` may be null, but the key must exist.
- `output_manifest` must be the exact manifest path the worker may write.
- `schema` must be `docs/ai-onboarding/provider-manifest.schema.json`.
- `hard_constraints` must be `docs/ai-onboarding/hard-constraints.md`.
- `search_requirements.routing` must contain `doi_prefixes`, `domains`, `domain_suffixes`, and `crossref_publisher`.
- `search_requirements.doi_sample_purposes` must contain `structure`, `table`, `formula`, `figure`, `supplementary`, `references`, `pdf_fallback`, `abstract_only`, `access_gate`, and `empty_shell`.
- `files_allowed_to_modify` must contain exactly one path, equal to `output_manifest`.
- `files_must_not_modify` must contain `src/`, `tests/`, `docs/providers.md`, and `CHANGELOG.md`.
- `no_commit` must be `true`.

### Forbidden Writes

Discovery worker must not write any path outside `output_manifest`.

Forbidden paths include:

- `src/`
- `tests/`
- `docs/providers.md`
- `CHANGELOG.md`
- fixture directories
- provider implementation modules
- shared onboarding docs

Coordinator must treat any forbidden write as `WORKER_MODIFIED_FORBIDDEN_FILE` and discard that worker result before retrying.

## scaffold/from-manifest

`scaffold` worker 必须把已校验的 provider manifest 作为唯一 provider 输入源。Brief 必须包含：

```yaml
task_id: mdpi-scaffold
current_step: scaffold
runtime: coding-agent-subagent
input_manifest: docs/ai-onboarding/manifests/mdpi.yml
schema: docs/ai-onboarding/provider-manifest.schema.json
scaffold_command:
  - python3
  - scripts/scaffold_provider.py
  - --from-manifest
  - docs/ai-onboarding/manifests/mdpi.yml
files_allowed_to_modify:
  - src/paper_fetch/providers/_mdpi_html.py
  - src/paper_fetch/providers/mdpi.py
  - tests/unit/test_mdpi_provider.py
  - tests/fixtures/golden_criteria/
  - tests/fixtures/golden_criteria/manifest.json
  - docs/ai-onboarding/capture-commands/mdpi.txt
  - docs/providers.md
  - docs/extraction-rules.md
  - CHANGELOG.md
files_must_not_modify:
  - docs/ai-onboarding/manifests/mdpi.yml
no_commit: true
```

### scaffold/from-manifest Rules

- `input_manifest` 必须等于传给 `scripts/scaffold_provider.py --from-manifest` 的路径。
- `--from-manifest` 不能和 legacy scaffold 输入混用，包括 `--name`、`--doi`、`--source`、`--fulltext-client` 或 `--html-capable`。
- Worker 不得修改 `input_manifest`；schema 修复属于 `discover-manifest`。
- 命令 stdout 是 JSON artifact summary。Coordinator 应记录其中的 `generated_files` 和 `docs_files`。
- 如果 scaffold 以 `MANIFEST_SCHEMA_INVALID` 退出，coordinator 应把 JSON stderr 回派给 manifest repair，而不是要求 scaffold patch manifest。
