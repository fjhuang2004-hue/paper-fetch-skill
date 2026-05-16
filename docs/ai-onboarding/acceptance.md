# AI Onboarding Acceptance

This file defines machine-verifiable merge-ready gates for AI/coordinator provider onboarding.

## Manifest Gates

- `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_manifest_schema.py -q`
- `PYTHONPATH=src python3 -m pytest tests/unit/test_manifest_bundle_sync.py -q`
- Manifest status for merge-ready provider is `ready` or stricter sync-back status accepted by `tests/unit/_manifest_sync.py`.
- `docs/ai-onboarding/known-providers.yml` entry contains an existing `manifest_path`.

## Fixture Gates

- Every required DOI purpose in `docs/ai-onboarding/provider-manifest.schema.json` is present in `fixtures.doi_samples`.
- `structure`, `figure`, and `references` DOI values are non-null.
- Capture failures use structured JSON stderr with `code` from `failure-recovery.md`.
- `UNSUITABLE_DOI_SAMPLE` changes only `fixtures.doi_samples.<purpose>` for the failed purpose.

## Implementation Gates

- Provider-local pytest from `briefs/implement-provider.yml` passes.
- `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_markdown_review_contract.py -q`
- `PYTHONPATH=src python3 -m pytest tests/unit/test_provider_bundle_completeness.py tests/unit/test_provider_owner_reuse.py -q`
- `python3 scripts/validate_extraction_rules.py`
- `manifest_sync_back.py` is the only writer for `extraction_hints` and `success_criteria` sync-back fields.

## Markdown Review Gates

- Every non-null `fixtures.doi_samples.<purpose>` is represented in `tests/unit/test_<provider>_provider.py` by purpose name or DOI slug.
- Provider-local tests do not contain scaffold skipped placeholders or Markdown review-loop placeholders.
- Provider-local tests include at least one positive Markdown assertion and at least one negative site-chrome / access-noise / boilerplate assertion.
- Worker completion summary includes `reviewed_fixtures` entries for every non-null purpose.

## Drift Gates

- `PYTHONPATH=src python3 -m pytest tests/unit/test_human_docs_drift.py -q`
- `git grep -n "Human reference only" -- docs/provider-development.md docs/adding-a-provider.md`
- `git grep -n "docs/ai-onboarding" -- docs/provider-development.md docs/adding-a-provider.md`
- Legacy human-guide banned-token grep over `docs/ai-onboarding/` returns no matches.

## Structured Error Gates

Acceptance fails when any tool reports a JSON stderr `code` that maps to `retryable: false` in `failure-recovery.md`.

Acceptance remains blocked after retry budget exhaustion for:

- `DISCOVERY_RETRY_EXHAUSTED`
- `TASK_RETRY_EXHAUSTED`
- `MANIFEST_PROVIDER_CONFLICT`
- `BROWSER_RUNTIME_REQUIRED`
