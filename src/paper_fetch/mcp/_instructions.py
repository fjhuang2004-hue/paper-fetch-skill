"""Canonical MCP and skill-facing instruction snippets."""

from __future__ import annotations

from ..reason_codes import ERROR, NO_ACCESS, RATE_LIMITED

DEFAULT_FETCH_VALUES: tuple[tuple[str, str], ...] = (
    ("modes", '["article", "markdown"]'),
    ("strategy.asset_profile", "null (provider default)"),
    ("strategy.allow_metadata_only_fallback", "true"),
    ("include_refs", "null"),
    ("max_tokens", '"full_text"'),
    ("prefer_cache", "false"),
    ("no_download", "false"),
    ("artifact_mode", '"markdown-assets"'),
    ("save_markdown", "false"),
    ("markdown_output_dir", "null"),
    ("markdown_filename", "null"),
)

DEFAULT_FETCH_NOTES: tuple[str, ...] = (
    "`include_refs=null` behaves like `all` when `max_tokens=\"full_text\"`.",
    "When `max_tokens` is a positive integer, `include_refs=null` behaves like `top10`.",
)

SKILL_ENVIRONMENT_VARIABLES: tuple[tuple[str, str], ...] = (
    ("ELSEVIER_API_KEY", "Required for official Elsevier full-text access."),
    ("ELSEVIER_INSTTOKEN", "Optional institution token for Elsevier entitlement."),
    ("WILEY_TDM_CLIENT_TOKEN", "Optional Wiley Text and Data Mining client token for the official Wiley PDF lane; browser PDF/ePDF fallback can still run without it when the local runtime is ready."),
    ("CLOAKBROWSER_HEADLESS", "Optional override (true/false) for the CloakBrowser browser runtime. Defaults to true."),
    ("CLOAKBROWSER_TIMEOUT_MS", "Optional override for CloakBrowser per-request timeout. Defaults to 120000."),
    ("PAPER_FETCH_DOWNLOAD_DIR", "Overrides the default CLI/MCP download directory."),
    ("PAPER_FETCH_RUN_LIVE", "Test-only flag for live publisher integration checks."),
)

ERROR_CONTRACT: tuple[tuple[str, str], ...] = (
    ("ambiguous", "Contains `candidates`; prompt the user to choose and retry."),
    (NO_ACCESS, "Credentials or entitlements are missing; inspect `missing_env` when present, then retry."),
    (RATE_LIMITED, "Back off and retry later."),
    (ERROR, "Any other failure; inspect `reason`."),
)


def format_defaults_markdown() -> str:
    lines = ["Recommended defaults:"]
    lines.extend(f"- `{key}={value}`" for key, value in DEFAULT_FETCH_VALUES)
    lines.extend(f"- {note}" for note in DEFAULT_FETCH_NOTES)
    return "\n".join(lines)


def format_environment_markdown() -> str:
    lines = []
    for name, description in SKILL_ENVIRONMENT_VARIABLES:
        lines.append(f"- `{name}`: {description}")
    return "\n".join(lines)


def format_error_contract_markdown() -> str:
    lines = []
    for status, description in ERROR_CONTRACT:
        lines.append(f"- `{status}`: {description}")
    lines.append("- These fields appear in MCP `structuredContent` and in CLI stderr JSON for runtime fetch failures.")
    lines.append("- MCP error payloads may also include `missing_env=[...]` when credentials or required env vars are known.")
    lines.append("- CLI runtime fetch exit codes remain `ambiguous=2`, `no_access=3`, `rate_limited=4`; argparse validation errors also exit `2` but are not ambiguity results.")
    return "\n".join(lines)


def server_instructions() -> str:
    return (
        "Resolve or fetch a specific paper by DOI, landing URL, or title query. "
        "Use resolve_paper when the query may be ambiguous; it accepts either a raw query or "
        "structured title/authors/year fields. Use fetch_paper when you need "
        "structured article metadata, AI-friendly markdown, or both. "
        "The server also publishes `summarize_paper` and `verify_citation_list` prompt templates "
        "for cache-first single-paper summaries and bibliography triage workflows. "
        "All MCP tools now publish JSON output schemas for clients that support tool-result "
        "validation and autocomplete. "
        "Defaults: modes=['article','markdown'], strategy.asset_profile omitted (provider default), "
        "strategy.allow_metadata_only_fallback=true, "
        "include_refs=null, max_tokens='full_text', prefer_cache=false, no_download=false, artifact_mode='markdown-assets', "
        "save_markdown=false. In full_text mode include_refs=null "
        "behaves like 'all'. When asset_profile is body/all, optional "
        "strategy.inline_image_budget can tune the default inline ImageContent caps of "
        "3 figures, 2 MiB each, and 8 MiB total. `provider_hint` and "
        "`preferred_providers` may include `elsevier`, `springer`, `wiley`, `science`, `pnas`, `ams`, `ieee`, `arxiv`, `copernicus`, or `crossref`. "
        "`elsevier` keeps an official XML route first and may then fall back to the "
        "official Elsevier API PDF lane before degrading to metadata-only, publishing "
        "`elsevier_xml` on XML success and `elsevier_pdf` on PDF fallback success. `springer` keeps a provider-managed direct HTML route "
        "with direct HTTP PDF fallback, publishing `springer_html` on HTML success and `springer_pdf` on PDF fallback success. `wiley` keeps "
        "the CloakBrowser HTML route, then seeded-browser publisher PDF/ePDF "
        "fallback, and may still continue into the official Wiley TDM API PDF lane "
        "when `WILEY_TDM_CLIENT_TOKEN` is configured while publishing `wiley_browser`. `science`, "
        "`pnas`, and `ams` require the local browser runtime but no legacy local "
        "rate-limit env vars; AMS publishes `ams_html` or `ams_pdf` and ignores `citation_xml_url`. `ieee` uses "
        "landing metadata, the Xplore dynamic HTML endpoint, and direct HTTP PDF fallback, "
        "publishing `ieee_html` or `ieee_pdf` when those routes return usable full text. `arxiv` uses "
        "arXiv ID-derived HTML first, optional API/HTML metadata merge, and text-only PDF fallback while publishing "
        "`arxiv_html` or `arxiv_pdf`. `copernicus` uses "
        "direct landing HTML to discover public NLM/JATS XML, then falls back to text-only PDF before metadata fallback, "
        "requires no browser runtime or provider credentials, and publishes `copernicus_xml` or `copernicus_pdf`. "
        "Elsevier PDF fallback currently returns text-only markdown even when "
        "`asset_profile` is `body` or `all`. On successful HTML/XML routes, "
        "`asset_profile='none'` disables local asset downloads but does not remove "
        "remote image links already present in rendered Markdown. "
        "`asset_profile='body'` means provider-cleaned body figure/table/formula assets only, "
        "while `asset_profile='all'` additionally downloads supplementary files. "
        "Inline ImageContent still only comes from body figures. Wiley/Science/PNAS/AMS support "
        "`asset_profile=body|all` on successful CloakBrowser HTML routes and "
        "prefer full-size/original figures before falling back to previews, while "
        "their PDF/ePDF fallback routes remain text-only. Springer, IEEE, arXiv, and Copernicus PDF fallback "
        "routes are also text-only in this version. "
        "On supporting clients, fetch_paper and batch tools also emit progress updates "
        "and structured log notifications."
    )


def fetch_tool_description() -> str:
    return (
        "Fetch AI-friendly paper content. Returns a fixed FetchEnvelope-style object with "
        "top-level provenance, `token_estimate_breakdown={abstract,body,refs}`, and optional "
        "article/markdown/metadata payloads. "
        "The MCP tool also publishes an output schema for clients that support structured "
        "result validation. "
        "Defaults: modes=['article','markdown'], strategy.asset_profile omitted (provider default), "
        "strategy.allow_metadata_only_fallback=true, "
        "include_refs=null, max_tokens='full_text', prefer_cache=false, no_download=false, artifact_mode='markdown-assets', "
        "save_markdown=false, markdown_output_dir=null, markdown_filename=null. Set "
        "prefer_cache=true to resolve the query to a DOI, then try a matching local cached "
        "FetchEnvelope sidecar before running the full fetch waterfall. Use artifact_mode='none' "
        "to disable provider artifacts and assets while keeping MCP fetch-envelope cache sidecars. Use "
        "no_download=true to avoid writing provider payloads, PDFs, HTML, assets, and "
        "fetch-envelope sidecars. Set save_markdown=true to write the rendered Markdown "
        "full text to disk; successful saves return saved_markdown_path, while "
        "metadata-only or abstract-only results add a warning and "
        "download:markdown_skipped_no_fulltext. Use strategy.asset_profile='none', "
        "'body', or 'all' to control local asset downloads; 'none' does not remove "
        "remote image links already present in rendered Markdown. "
        "With body/all profiles, key local figures may be returned as ImageContent "
        "alongside the JSON result; strategy.inline_image_budget can override the default "
        "caps of 3 figures, 2 MiB each, and 8 MiB total, and any resulting zero disables "
        "inline images. `elsevier` keeps an official XML route and may fall back to "
        "the official Elsevier API PDF lane before degrading to metadata-only, publishing "
        "`elsevier_xml` on XML success and `elsevier_pdf` on PDF fallback success. `springer` uses provider-managed direct HTML and direct "
        "HTTP PDF fallback, publishing `springer_html` or `springer_pdf`. `wiley` keeps "
        "CloakBrowser HTML first, then seeded-browser publisher PDF/ePDF "
        "fallback, and may still continue into the official Wiley TDM API PDF lane "
        "when `WILEY_TDM_CLIENT_TOKEN` is configured while publishing source "
        "`wiley_browser` on success. `science`, `pnas`, and `ams` routes use "
        "provider-managed browser runtime HTML plus seeded-browser publisher PDF/ePDF repo-local "
        "workflows; AMS publishes `ams_html` or `ams_pdf` and does not request `citation_xml_url` / `/doc/...xml`. `ieee` uses landing metadata, "
        "the Xplore dynamic HTML endpoint, and direct HTTP PDF fallback while publishing "
        "`ieee_html` or `ieee_pdf`. `arxiv` uses ID-derived official HTML first, optional API/HTML metadata merge, and text-only PDF "
        "fallback while publishing `arxiv_html` or `arxiv_pdf`. `copernicus` uses direct HTTP landing discovery, public NLM/JATS XML, "
        "and text-only PDF fallback before metadata fallback while publishing `copernicus_xml` or `copernicus_pdf`; it does not need browser runtime or credentials. Elsevier PDF "
        "fallback keeps body/all requests text-only. On successful HTML/XML routes, "
        "`asset_profile='none'` disables local asset downloads but keeps rendered "
        "remote Markdown image links when the provider can resolve them. "
        "`asset_profile='body'` means provider-cleaned body figure/table/formula assets only, "
        "while `asset_profile='all'` additionally downloads supplementary files; "
        "supplementary files are saved as assets but are not emitted as ImageContent. "
        "Wiley/Science/PNAS/AMS support body/all assets on successful CloakBrowser HTML routes while keeping "
        "PDF/ePDF fallback text-only, and Springer/IEEE/arXiv/Copernicus PDF fallback is also text-only "
        "in this version. Set "
        "download_dir to isolate task-local downloads; the MCP server can also surface "
        "scoped cache resources for that directory during the current session."
    )
