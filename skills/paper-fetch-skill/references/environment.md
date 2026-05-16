# Environment

- `PAPER_FETCH_ENV_FILE`: Optional path to an explicit environment file. The default user config file is resolved with `platformdirs` and is outside the repo; repo-local config files are not auto-loaded.
- `PAPER_FETCH_SKILL_USER_AGENT`: Optional custom HTTP `User-Agent`; when unset, runtime uses `paper_fetch.config.DEFAULT_USER_AGENT`.
- `CROSSREF_MAILTO`: Recommended contact email for Crossref polite pool requests.
- `ELSEVIER_API_KEY`: Required for official Elsevier full-text access.
- `ELSEVIER_INSTTOKEN`: Optional institution token for Elsevier entitlement.
- `ELSEVIER_AUTHTOKEN`: Optional Elsevier bearer token credential.
- `ELSEVIER_CLICKTHROUGH_TOKEN`: Optional Elsevier clickthrough credential.
- `WILEY_TDM_CLIENT_TOKEN`: Optional Wiley Text and Data Mining client token for the official Wiley PDF lane; browser PDF/ePDF fallback can still run without it when the local runtime is ready.
- `CLOAKBROWSER_HEADLESS`: Optional override (`true`/`false`) for the CloakBrowser browser runtime. Defaults to `true`.
- `CLOAKBROWSER_TIMEOUT_MS`: Optional override for CloakBrowser per-request timeout. Defaults to `120000`.
- IEEE dynamic HTML / direct HTTP PDF / seeded-browser PDF fallback does not use an IEEE API key; full text availability still depends on the current environment's lawful IEEE Xplore access context. The browser PDF fallback only runs after non-PDF PDF candidates and fails closed on access, challenge, or temporary unavailable pages.
- `PAPER_FETCH_DOWNLOAD_DIR`: Overrides the default CLI/MCP download directory; otherwise downloads use the user data directory, with CLI falling back to `live-downloads` only if that directory cannot be created.
- `XDG_DATA_HOME`: Changes the user data base used for default downloads and formula tools; otherwise the platform default from `platformdirs` is used.
- `PAPER_FETCH_FORMULA_TOOLS_DIR`: Overrides the directory used to find optional formula backends.
- `PAPER_FETCH_RUN_LIVE`: Test-only flag for live publisher integration checks.
- `PAPER_FETCH_MCP_PYTHON_BIN`: Optional override used by `scripts/run-codex-paper-fetch-mcp.sh` to choose the Python interpreter for the host-side MCP server. Defaults to `python3`.
- Formula backend env such as `MATHML_CONVERTER_BACKEND`, `TEXMATH_BIN`, `MATHML_TO_LATEX_NODE_BIN`, and `MATHML_TO_LATEX_SCRIPT` only affects MathML-to-LaTeX conversion backends. The default backend is `texmath`; when not explicitly selected, `texmath` failure falls back to `mathml-to-latex`. Shared LaTeX normalization for common publisher macros runs independently of these variables.
- Advanced `mml2tex` support exists behind `MATHML_CONVERTER_BACKEND=mml2tex` plus `MML2TEX_*` Java/XSLT env vars; the default installer does not prepare that toolchain.
