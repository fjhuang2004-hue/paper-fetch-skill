# CLI Fallback

If MCP is unavailable, use:

```bash
paper-fetch --query "<DOI | URL | title>"
```

Useful options:

- `--format markdown|json|both`: serialization format for stdout or `--output` (default: `markdown`).
- `--output -|<path>`: formatted output destination. `-` means stdout.
- `--output-dir <dir>`: provider HTML/PDF/asset directory. If `--format` is explicitly set and `--output` remains stdout, the CLI also saves a same-format copy here (`<doi>.md`, `<doi>.json`, or `<doi>.both.json`).
- `--no-download`
- `--save-markdown`: extra full-text Markdown save step; only writes when full text was retrieved.
- `--include-refs none|top10|all`
- `--asset-profile none|body|all`
- `--max-tokens full_text|<positive-int>` (default `full_text`)

Output contract:

- `--format markdown`: prints AI-friendly Markdown.
- `--format json`: prints `ArticleModel` JSON.
- `--format both`: prints `{"article": ..., "markdown": ...}`.
- With explicit `--format` + `--output-dir` + stdout output, the same serialized payload is also written under `--output-dir`.
- Runtime fetch failures from `PaperFetchFailure` or `ProviderFailure` write JSON to `stderr`; argument parsing errors still use argparse's standard stderr format.
