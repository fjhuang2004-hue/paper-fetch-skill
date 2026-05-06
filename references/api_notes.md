# Publisher Route Notes

This file is a human-maintained route reference for v1. Runtime behavior is authoritative in `src/paper_fetch/providers/`, `src/paper_fetch/publisher_identity.py`, and `src/paper_fetch/workflow/`. Provider fallback order is now composed through the internal `_waterfall` runner, while provider steps keep their own payloads, warnings, and source markers. Elsevier is the primary structured XML/API full-text route; Wiley additionally has an optional TDM API PDF lane. Springer, Wiley HTML/browser PDF, Science, and PNAS are provider-managed routes with the constraints below.

## Elsevier

- Official source: Elsevier Developer Portal and related Search/Article APIs.
- Status: the primary publisher XML/API full-text route in this runtime.
- Current implementation:
  - Metadata: `https://api.elsevier.com/content/abstract/doi/{doi}`
  - Full text: `https://api.elsevier.com/content/article/doi/{doi}`
  - Full-text retrieval requests `text/xml` first so the fetcher can parse Elsevier `objects` and `attachment-metadata-doc` sections.
  - When Elsevier XML contains object or attachment metadata and an output directory is provided, the fetcher also downloads linked figures and supplementary files.
  - Structured Elsevier bibliography is preferred over Crossref reference fallback when available; numbered labels, authors, titles, source, pages, year, and DOI are preserved as far as the XML provides them.
  - Complex table spans are semantically expanded into rectangular Markdown cells; layout degradation is reported separately from semantic content loss.
  - Formula LaTeX is normalized after conversion, including upright Greek aliases and `\mspace{Nmu}` spacing macros.
  - Required env: `ELSEVIER_API_KEY`
  - Optional entitlement env: `ELSEVIER_INSTTOKEN`, `ELSEVIER_AUTHTOKEN`, `ELSEVIER_CLICKTHROUGH_TOKEN`
- Route when:
  - The landing-page domain or Crossref publisher-name signal maps to `elsevier`, or
  - The DOI uses the strongly indicative prefix `10.1016/`.
- Common constraints:
  - API key is typically required.
  - Some endpoints are entitlement-gated.
- Reference URL:
  - `https://dev.elsevier.com/`

## Springer

- Runtime status: supported, but not through Springer Nature publisher APIs.
- Current implementation:
  - Metadata comes from Crossref merge and landing-page signals.
  - Full text is fetched from the publisher landing page HTML.
  - Preferred landing URL comes from merged metadata; if missing, the runtime resolves `https://doi.org/{doi}` and follows the final landing page.
  - HTML extraction is provider-owned and reuses the existing HTML parsing stack internally.
  - Springer / Nature HTML cleanup removes site chrome such as save actions, aims/scope blocks, duplicate title headings, preview notices, and figure download-control text.
  - Nature / Springer inline table pages are injected back into the body; known image-only Extended Data Tables can be retained as table image assets or explicit `[Table body unavailable: ...]` placeholders.
  - Raw `span.mathjax-tex` content is normalized through the shared LaTeX macro normalizer before Markdown rendering.
- Route when:
  - The landing-page domain or Crossref publisher-name signal maps to `springer`, or
  - The DOI uses a supported Springer-pattern prefix such as `10.1038/`, `10.1007/`, or `10.1186/`.
- Common constraints:
  - The runtime does not use Springer publisher endpoints or credentials.
  - Springer full-text success depends on the landing HTML being directly readable enough for extraction.

## Wiley

- Runtime status: supported via provider-managed HTML plus an optional Wiley TDM API PDF lane.
- Current implementation:
  - Metadata comes from Crossref merge and landing-page signals.
  - Full text uses provider-managed `FlareSolverr HTML -> seeded-browser publisher PDF/ePDF -> Wiley TDM API PDF -> abstract-only / metadata-only`.
  - Candidate URLs prefer Crossref or landing-page URLs and fall back to DOI resolution when needed.
  - HTML is fetched through repo-local FlareSolverr; publisher PDF/ePDF fallback uses Playwright; the optional Wiley TDM API lane uses `WILEY_TDM_CLIENT_TOKEN`.
- Route when:
  - The landing-page domain or Crossref publisher-name signal maps to `wiley`, or
  - The DOI uses a strongly indicative Wiley prefix such as `10.1002/` or `10.1111/`.
- Common constraints:
  - The HTML and browser PDF/ePDF paths depend on repo-local FlareSolverr readiness and explicit local rate-limit settings.
  - The Wiley TDM API PDF lane is optional; if no token is configured, the runtime can still attempt browser PDF/ePDF when the local runtime is ready.
  - If `WILEY_TDM_CLIENT_TOKEN` is configured, the TDM API PDF lane can still be attempted after browser PDF/ePDF fallback failure or when the local browser runtime is not ready.

## Science / PNAS

- Runtime status: supported via the same local browser workflow family as Wiley.
- Current implementation:
  - Metadata comes from Crossref merge and landing-page signals.
  - Full text uses provider-managed `FlareSolverr HTML -> seeded-browser publisher PDF/ePDF -> abstract-only / metadata-only`.
  - HTML is fetched through repo-local FlareSolverr; publisher PDF/ePDF fallback uses Playwright.
  - HTML asset downloads prefer full-size/original images. Browser-workflow providers now cache repeated figure-page / image-candidate URLs per download attempt and fetch image payloads with fixed limited parallelism before writing files in input order.
  - If direct image fetch returns challenge HTML or a browser image shell, Science / PNAS may use Playwright image-document export before accepting preview fallback; FlareSolverr recovery only accepts recognizable `solution.imagePayload` values, including browser-exported PNG pixels and raw top-level SVG, not screenshot cropping or challenge HTML.
  - Preview images are only treated as acceptable degradation when saved dimensions meet the runtime threshold; otherwise they remain asset-download issues in warnings/source trail.
- Common constraints:
  - The runtime does not use publisher APIs for these providers.
  - These routes depend on repo-local FlareSolverr readiness and explicit local rate-limit settings.

## Copernicus

- Runtime status: planned design note only; not currently wired into the provider catalog, router, registry, CLI, MCP status surface, or tests.
- Intended default behavior:
  - Use `fulltext_first` when the route resolves to Copernicus.
  - Treat Copernicus as an open-access direct HTTP provider.
  - Fall back to provider-managed `abstract_only` or generic `metadata_only` when XML/HTML/PDF discovery or extraction fails.
- Proposed implementation:
  - Metadata starts from Crossref merge and landing-page signals.
  - Route by Copernicus journal domains, publisher names such as `Copernicus Publications`, and DOI prefix `10.5194/`.
  - Fetch the landing page first and discover `citation_xml_url` or article XML download links.
  - Prefer article XML as the primary full-text source; Copernicus XML is typically NLM/JATS style and may include OASIS tables, MathML, references, figures, and supplementary links.
  - Use direct full-text HTML as the first fallback when XML is absent or malformed.
  - Use PDF only as an opportunistic text-only fallback; publisher-side PDF throttling must not fail an otherwise successful XML/HTML article.
  - Consider OAI-PMH as a future bulk/discovery aid, not as the mandatory first step for a single DOI fetch.
- Common constraints:
  - This route should not depend on FlareSolverr or a seeded browser workflow by default.
  - Validation should distinguish XML/HTML full text from publisher status pages, temporary PDF restriction pages, and metadata-only pages.

## MDPI

- Runtime status: planned design note only; not currently wired into the provider catalog, router, registry, CLI, MCP status surface, or tests.
- Intended default behavior:
  - Use `fulltext_first` when the route resolves to MDPI.
  - Treat MDPI as an open-access provider whose article XML/HTML/PDF are public, while allowing CDN transport failures to degrade cleanly.
  - Fall back to provider-managed `abstract_only` or generic `metadata_only` when public article retrieval, validation, or extraction fails.
- Proposed implementation:
  - Metadata starts from Crossref merge and landing-page signals.
  - Route by `mdpi.com`, publisher names such as `MDPI` / `MDPI AG`, and DOI prefix `10.3390/`.
  - Discover article XML from landing-page links or article notes; use fixed `/xml` route construction only as a secondary candidate.
  - Prefer XML -> Markdown as the primary path, then provider-cleaned article HTML, then direct Playwright HTML if plain direct HTTP is blocked by CDN behavior.
  - Use PDF only as a text-only fallback.
  - Download body assets and supplementary files from XML/HTML-discovered links according to `asset_profile=body|all`.
- Common constraints:
  - Plain HTTP `403` or CDN denial on a public article should be treated as transport failure, not as publisher entitlement failure.
  - Direct Playwright fallback is acceptable for public MDPI pages; FlareSolverr should not be introduced unless a concrete Cloudflare challenge exists.
  - Validation should reject CDN error pages, bot-block pages, empty shells, menu-only pages, and abstract-only fragments before Markdown conversion.

## IEEE

- Runtime status: planned design note only; not currently wired into the provider catalog, router, registry, CLI, MCP status surface, or tests.
- Intended default behavior:
  - Use `fulltext_first` when the route resolves to IEEE.
  - Assume the operator already has lawful IEEE Xplore access in the current environment, such as institution IP/VPN, authenticated browser cookies, or a personal subscription.
  - Treat full-text retrieval as a best-effort default attempt, not as a guarantee.
  - Fall back to provider-managed `abstract_only` or generic `metadata_only` when access, response shape, validation, extraction, or network checks fail.
- Proposed implementation:
  - Metadata still starts from Crossref merge and landing-page signals.
  - Route by `ieeexplore.ieee.org`, Crossref publisher names such as `IEEE` / `Institute of Electrical and Electronics Engineers`, and DOI prefix `10.1109/`.
  - Resolve the IEEE article number from the landing URL or page metadata.
  - Fetch dynamic full-text HTML from `https://ieeexplore.ieee.org/rest/document/{article_number}/?logAccess=true`.
  - Send page-context headers such as `Accept: application/json, text/plain, */*`, the document `Referer`, `x-security-request: required`, and a browser-like user agent.
  - Parse the response as HTML, even when the endpoint looks like a REST path; observed successful responses use `text/html;charset=utf-8`.
  - Validate full-text markers before extraction, for example `#article`, section containers, meaningful paragraph counts, and IEEE figure/table blocks.
  - Reject login pages, access-gate pages, challenge pages, abstract-only pages, empty shells, and unrelated error HTML before Markdown conversion.
- Common constraints:
  - Do not bypass IEEE access controls, solve CAPTCHA flows, or fabricate entitlement state.
  - The provider may use access context already present in the operator's environment, but must degrade cleanly when that context is missing.
  - Dynamic HTML asset URLs can later be mapped into the normal `asset_profile=body|all` behavior; Markdown success should not depend on every asset being downloadable.

## Crossref

- Official source: Crossref REST API documentation.
- Role in this skill: universal metadata provider, routing signal source, and metadata-only fallback provider.
- Current implementation:
  - Metadata: `https://api.crossref.org/works/{doi}` or `https://api.crossref.org/works`
  - Recommended env: `CROSSREF_MAILTO`
  - Crossref metadata links may be used for routing and provider handoff; unsupported publishers do not fall through to a generic full-text downloader.
- Route when:
  - No supported publisher route can be chosen with enough confidence.
  - A metadata-only or abstract-level degraded result is still useful after publisher full-text retrieval fails.
- Reference URL:
  - `https://www.crossref.org/documentation/retrieve-metadata/rest-api/`
