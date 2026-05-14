# ProviderManifest v1 字段说明

本文面向实现和维护 onboarding 工具的工程读者。`ProviderManifest` 是 implementation worker 的输入合约，字段必须能追溯到 runtime catalog、现有 fixture、公开 evidence 或后续 sync-back。

## 顶层字段

| 字段 | Type | Required | 约束 | 决策依据 |
|---|---|---:|---|---|
| `schema_version` | integer | 是 | `1` | 让后续 schema 版本可以并存。 |
| `name` | string | 是 | regex `^[a-z][a-z0-9_]*$`；等于文件名 stem | provider 模块名、bundle name、manifest 文件名使用同一稳定 key。 |
| `display_source` | string | 是 | regex `^[a-z][a-z0-9_]*$` | 映射到运行时公开 source，例如 `wiley_browser`、`copernicus_xml`。 |
| `generation` | object | 是 | 见下表 | 记录 manifest 由 discovery 生成还是由现有 provider 回放生成。 |
| `routing` | object | 是 | 见下表 | scaffold 和路由同步检查的输入。 |
| `main_path` | array[string] | 是 | item enum `landing_html` / `article_html` / `xml` / `pdf_fallback` / `abstract_only` / `metadata_only`；`minItems: 1` | implementation worker 用它按顺序生成 provider 主链骨架。 |
| `success_criteria` | object | 是 | step key 到 object / array / `null`；每个 step value 标注 `x-sync-back: true` | 实现完成后由代码侧实际阈值回写。 |
| `asset_profile` | object | 是 | `none` / `body` / `all` 三组数组，item enum `figures` / `body_tables` / `formula_images` / `supplementary` / `multimedia` | 对齐运行时 asset profile 语义。 |
| `supplementary_scope` | object | 是 | `selector` / `url_pattern` 可为 string 或 `null` | 描述补充材料的 DOM 或 URL 边界。 |
| `abstract_only_strategy` | string | 是 | enum `provider_managed` / `metadata_only` / `not_supported` | 对齐 provider-managed fallback 行为。 |
| `probe` | object | 是 | 见下表 | provider status 和 live 运行依赖的输入。 |
| `fixtures` | object | 是 | 见下表 | 固定 DOI purpose 到 evidence object 的映射。 |
| `extraction_hints` | object | 是 | 各子字段允许 `null` / `[]` 起步；标注 `x-sync-back: true` | 实现完成后由 bundle/rules 反向序列化。 |
| `owner_reuse_exceptions` | array | 是 | item 需要 `owner` 和 `reason` | 只有通用 owner 无法复用时才记录例外。 |
| `docs` | object | 是 | 需要 `providers_md_capability_row` 和 `changelog_summary`，`extraction_rules_summary` 可为 string/null | scaffold 和 reviewer 使用的用户可见 docs 事实底稿。 |

## `generation`

| 字段 | Type | Required | 约束 | 决策依据 |
|---|---|---:|---|---|
| `generated_by` | string | 是 | enum `ai_discovery` / `manual_replay` | 新 provider 使用 discovery；现有 provider golden manifest 使用 replay。 |
| `generated_at` | string | 是 | JSON Schema `date-time` | 记录生成时间，便于审计 stale manifest。 |
| `source_queries` | array[string] | 是 | `minItems: 1` | 记录 discovery query 或 replay 输入来源。 |
| `confidence` | string | 是 | enum `high` / `medium` / `low` | 标识 manifest 初稿证据强度。 |

## `routing`

| 字段 | Type | Required | 约束 | 决策依据 |
|---|---|---:|---|---|
| `primary` | string | 是 | enum `doi_prefix` / `domain` / `publisher_alias` | 声明首选路由信号。 |
| `doi_prefixes` | array[string] | 是 | 可为空数组 | 对齐 `ProviderSpec.doi_prefixes`。 |
| `domains` | array[string] | 是 | 可为空数组 | 对齐 `ProviderSpec.domains`。 |
| `domain_suffixes` | array[string] | 是 | 可为空数组 | 对齐 `ProviderSpec.domain_suffixes`。 |
| `publisher_aliases` | array[string] | 是 | 可为空数组 | 对齐 publisher alias 路由。 |
| `crossref_publisher` | string/null | 是 | string 或 `null` | 只有 discovery 有可靠 Crossref publisher 证据时填写。 |

## `success_criteria`

`success_criteria` 是以 `main_path` step 或 provider 自定义 step 为 key 的 object。每个 step value 由 implementation worker 或 sync-back 工具回写，schema 上均带 `x-sync-back: true`。

| 字段 | Type | Required | 约束 | 决策依据 |
|---|---|---:|---|---|
| `<step>` | object/array/null | 否 | step key 可为空；value 是 sync-back 占位或实现后阈值对象 | 主路径正文质量阈值、success marker、figure/table/reference 数量等实现事实。 |

## `probe`

| 字段 | Type | Required | 约束 | 决策依据 |
|---|---|---:|---|---|
| `env_requirements` | array[string] | 是 | item string | provider status 和 live sample 所需环境变量。 |
| `requires_playwright` | boolean | 是 | boolean | 声明是否依赖 browser runtime。 |
| `requires_flaresolverr` | boolean | 是 | boolean | 声明是否依赖 FlareSolverr。 |
| `ping_url` | string/null | 否 | URI 或 `null` | status probe 或人工排查入口。 |

## `fixtures.doi_samples`

固定 purpose：`structure`、`table`、`formula`、`figure`、`supplementary`、`references`、`pdf_fallback`、`abstract_only`、`access_gate`、`empty_shell`。

每个 purpose 的 value 都是 evidence object：

| 字段 | Type | Required | 约束 | 决策依据 |
|---|---|---:|---|---|
| `doi` | string/null | 是 | DOI string 或 `null`；`structure` / `figure` / `references` 必须非空 | capture fixture 的主输入。 |
| `evidence_url` | string | 是 | URI | 指向 DOI landing page 或可审计页面。 |
| `evidence_reason` | string | 是 | 非空 | 解释此 DOI 覆盖该 purpose 的原因。 |
| `observed_signals` | array[string] | 是 | 可为空数组 | 页面或 fixture 中可观察的信号。 |
| `confidence` | string | 是 | enum `high` / `medium` / `low` | 标识该样本证据强度。 |

## `extraction_hints`

这些字段由 sync-back 回写，schema 上均带 `x-sync-back: true`，初稿可以是 `null` 或空数组。

| 字段 | Type | Required | 决策依据 |
|---|---|---:|---|
| `datalayer_signal_set` | object/array/null | 是 | 对齐 availability datalayer signal set。 |
| `text_marker_signal_set` | object/array/null | 是 | 对齐 availability text marker signal set。 |
| `front_matter` | object/array/null | 是 | 对齐 provider front matter rules。 |
| `asset_retry` | object/array/null | 是 | 对齐 provider asset retry policy。 |
| `metadata_merge` | object/array/null | 是 | 对齐 provider metadata merge rules。 |

## `docs`

| 字段 | Type | Required | 决策依据 |
|---|---|---:|---|
| `providers_md_capability_row` | string | 是 | `docs/providers.md` 能力矩阵行的事实来源。 |
| `changelog_summary` | string | 是 | `CHANGELOG.md` 用户可见摘要。 |
| `extraction_rules_summary` | string/null | 否 | 有新增用户可见 extraction rule 时写入摘要，否则为 `null`。 |
