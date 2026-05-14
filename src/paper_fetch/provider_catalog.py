"""Static provider identity, routing, and capability catalog."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Callable, Literal, get_args

AssetDefault = Literal["none", "body", "all"]
MetadataProbeShortCircuit = Callable[[str], dict | None]


@dataclass(frozen=True)
class BodyTextThresholds:
    min_chars: int = 800
    short_body_min_chars: int = 300
    short_body_min_words: int = 60
    single_block_min_words: int = 90
    cjk_min_chars: int = 120
    single_block_min_cjk_chars: int = 180
    cjk_min_ratio: float = 0.20


DEFAULT_BODY_TEXT_THRESHOLDS = BodyTextThresholds()
ATYPON_DEFAULT_PDF_PATH_TEMPLATES = (
    "/doi/epdf/{doi}",
    "/doi/pdf/{doi}",
)


@dataclass(frozen=True)
class PdfSourcePathTemplate:
    domain: str
    path_prefix: str
    path_template: str


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    official: bool
    domains: tuple[str, ...]
    doi_prefixes: tuple[str, ...]
    publisher_aliases: tuple[str, ...]
    asset_default: AssetDefault
    probe_capability: str
    provider_managed_abstract_only: bool
    client_factory_path: str
    status_order: int
    domain_suffixes: tuple[str, ...] = ()
    base_domains: tuple[str, ...] = ()
    html_path_templates: tuple[str, ...] = ()
    pdf_path_templates: tuple[str, ...] = ()
    pdf_source_path_templates: tuple[PdfSourcePathTemplate, ...] = ()
    crossref_pdf_position: int = 0
    api_hosts: tuple[str, ...] = ()
    api_url_templates: tuple[tuple[str, str], ...] = ()
    sensitive_headers: tuple[str, ...] = ()
    metadata_probe_short_circuit: MetadataProbeShortCircuit | str | None = None
    persist_provider_html: bool = False
    xml_root_tags: tuple[str, ...] = ()
    xml_file_tokens: tuple[str, ...] = ()
    emits_html_managed_marker: bool = True
    body_text_thresholds: BodyTextThresholds = DEFAULT_BODY_TEXT_THRESHOLDS


_METADATA_PROBE_SHORT_CIRCUITS: dict[str, MetadataProbeShortCircuit] = {}


PROVIDER_CATALOG: dict[str, ProviderSpec] = {
    "crossref": ProviderSpec(
        name="crossref",
        display_name="Crossref",
        official=False,
        domains=(),
        doi_prefixes=(),
        publisher_aliases=(),
        asset_default="none",
        probe_capability="metadata_api",
        provider_managed_abstract_only=False,
        client_factory_path="paper_fetch.providers.crossref:CrossrefClient",
        status_order=0,
        sensitive_headers=("cr-clickthrough-client-token",),
    ),
    "elsevier": ProviderSpec(
        name="elsevier",
        display_name="Elsevier",
        official=True,
        domains=("sciencedirect.com", "elsevier.com"),
        doi_prefixes=("10.1016/",),
        publisher_aliases=("elsevier", "elsevier bv", "elsevier ltd", "elsevier masson sas"),
        asset_default="none",
        probe_capability="metadata_api",
        provider_managed_abstract_only=False,
        client_factory_path="paper_fetch.providers.elsevier:ElsevierClient",
        status_order=1,
        api_hosts=("scopus.com", "www.scopus.com"),
        sensitive_headers=("x-els-apikey", "x-els-insttoken"),
        xml_root_tags=("full-text-retrieval-response",),
        xml_file_tokens=("elsevier", "10.1016"),
    ),
    "springer": ProviderSpec(
        name="springer",
        display_name="Springer",
        official=True,
        domains=("springer.com", "springernature.com", "nature.com", "biomedcentral.com"),
        doi_prefixes=("10.1038/", "10.1007/", "10.1186/"),
        publisher_aliases=(
            "springer",
            "springer nature",
            "springer science and business media llc",
        ),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=True,
        client_factory_path="paper_fetch.providers.springer:SpringerClient",
        status_order=2,
        base_domains=("link.springer.com",),
        pdf_path_templates=("/content/pdf/{doi_quoted}.pdf",),
        pdf_source_path_templates=(
            PdfSourcePathTemplate(
                domain="nature.com",
                path_prefix="/articles/",
                path_template="{source_path}.pdf",
            ),
        ),
        persist_provider_html=True,
        xml_root_tags=("article",),
        xml_file_tokens=("springer", "nature", "10.1038", "10.1007", "10.1186"),
    ),
    "wiley": ProviderSpec(
        name="wiley",
        display_name="Wiley",
        official=True,
        domains=("onlinelibrary.wiley.com", "wiley.com", "www.wiley.com"),
        doi_prefixes=("10.1002/", "10.1111/"),
        publisher_aliases=("wiley", "wiley blackwell", "john wiley and sons", "john wiley sons"),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=True,
        client_factory_path="paper_fetch.providers.wiley:WileyClient",
        status_order=3,
        base_domains=("onlinelibrary.wiley.com",),
        html_path_templates=("/doi/full/{doi}", "/doi/{doi}"),
        pdf_path_templates=(
            *ATYPON_DEFAULT_PDF_PATH_TEMPLATES,
            "/doi/pdfdirect/{doi}",
            "/wol1/doi/{doi}/fullpdf",
        ),
        crossref_pdf_position=1,
        api_url_templates=(
            ("tdm_pdf", "https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"),
        ),
        sensitive_headers=("wiley-tdm-client-token",),
    ),
    "science": ProviderSpec(
        name="science",
        display_name="Science",
        official=True,
        domains=("www.science.org", "science.org"),
        doi_prefixes=("10.1126/",),
        publisher_aliases=("american association for the advancement of science", "aaas"),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=True,
        client_factory_path="paper_fetch.providers.science:ScienceClient",
        status_order=4,
        base_domains=("www.science.org", "science.org"),
        html_path_templates=("/doi/full/{doi}", "/doi/{doi}"),
        pdf_path_templates=(
            *ATYPON_DEFAULT_PDF_PATH_TEMPLATES,
            "/doi/pdf/{doi}?download=true",
        ),
    ),
    "pnas": ProviderSpec(
        name="pnas",
        display_name="PNAS",
        official=True,
        domains=("www.pnas.org", "pnas.org"),
        doi_prefixes=("10.1073/",),
        publisher_aliases=(
            "proceedings of the national academy of sciences",
            "proceedings of the national academy of sciences of the united states of america",
        ),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=True,
        client_factory_path="paper_fetch.providers.pnas:PnasClient",
        status_order=5,
        base_domains=("www.pnas.org", "pnas.org"),
        html_path_templates=("/doi/{doi}", "/doi/full/{doi}"),
        pdf_path_templates=(
            "/doi/epdf/{doi}",
            "/doi/pdf/{doi}?download=true",
            "/doi/pdf/{doi}",
        ),
    ),
    "ieee": ProviderSpec(
        name="ieee",
        display_name="IEEE",
        official=True,
        domains=("ieeexplore.ieee.org",),
        doi_prefixes=("10.1109/",),
        publisher_aliases=(
            "ieee",
            "institute of electrical and electronics engineers",
        ),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=True,
        client_factory_path="paper_fetch.providers.ieee:IeeeClient",
        status_order=6,
    ),
    "arxiv": ProviderSpec(
        name="arxiv",
        display_name="arXiv",
        official=True,
        domains=("arxiv.org",),
        doi_prefixes=("10.48550/",),
        publisher_aliases=("arxiv",),
        asset_default="body",
        probe_capability="metadata_api",
        provider_managed_abstract_only=False,
        client_factory_path="paper_fetch.providers.arxiv:ArxivClient",
        status_order=7,
        metadata_probe_short_circuit="paper_fetch.providers._arxiv_metadata:arxiv_metadata_probe_short_circuit",
        persist_provider_html=True,
    ),
    "copernicus": ProviderSpec(
        name="copernicus",
        display_name="Copernicus",
        official=True,
        domains=(),
        doi_prefixes=("10.5194/",),
        publisher_aliases=(
            "copernicus",
            "copernicus publications",
            "copernicus gmbh",
        ),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=False,
        client_factory_path="paper_fetch.providers.copernicus:CopernicusClient",
        status_order=8,
        domain_suffixes=("copernicus.org",),
        emits_html_managed_marker=False,
        xml_root_tags=("article",),
        xml_file_tokens=("copernicus", "10.5194"),
        body_text_thresholds=BodyTextThresholds(min_chars=500),
    ),
    "ams": ProviderSpec(
        name="ams",
        display_name="AMS",
        official=True,
        domains=("journals.ametsoc.org", "ametsoc.org"),
        doi_prefixes=("10.1175/",),
        publisher_aliases=(
            "american meteorological society",
            "ams",
            "american meteorological society (ams)",
        ),
        asset_default="body",
        probe_capability="routing_signal",
        provider_managed_abstract_only=True,
        client_factory_path="paper_fetch.providers.ams:AmsClient",
        status_order=9,
        base_domains=("journals.ametsoc.org",),
        # AMS is Atypon-hosted, but does not use the shared /doi/pdf routes.
        # Its PDF fallback is built from Crossref/source URL candidates instead.
        crossref_pdf_position=0,
    ),
}

SOURCE_PROVIDER_MAP: dict[str, str] = {
    "crossref_meta": "crossref",
    "elsevier_xml": "elsevier",
    "elsevier_pdf": "elsevier",
    "springer_html": "springer",
    "springer_pdf": "springer",
    "wiley_browser": "wiley",
    "science": "science",
    "pnas": "pnas",
    "ieee_html": "ieee",
    "ieee_pdf": "ieee",
    "arxiv_html": "arxiv",
    "arxiv_pdf": "arxiv",
    "copernicus_xml": "copernicus",
    "copernicus_pdf": "copernicus",
    "ams_html": "ams",
    "ams_pdf": "ams",
}


def _normalize_catalog_token(value: str | None) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _normalize_hostname(value: str | None) -> str:
    normalized = _normalize_catalog_token(value)
    if not normalized:
        return ""
    if "://" in normalized:
        from urllib.parse import urlparse

        return _normalize_catalog_token(urlparse(normalized).hostname)
    if "/" in normalized:
        normalized = normalized.split("/", 1)[0]
    if "@" in normalized:
        normalized = normalized.rsplit("@", 1)[-1]
    if normalized.startswith("["):
        return normalized.strip("[]")
    if ":" in normalized:
        normalized = normalized.split(":", 1)[0]
    return normalized


def host_matches_domain(hostname: str | None, domain: str | None) -> bool:
    host = _normalize_hostname(hostname)
    normalized_domain = _normalize_catalog_token(domain)
    return bool(host and normalized_domain and (host == normalized_domain or host.endswith(f".{normalized_domain}")))


def provider_domains(provider_name: str | None) -> tuple[str, ...]:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.domains + spec.domain_suffixes if spec is not None else ()


def provider_base_domains(provider_name: str | None) -> tuple[str, ...]:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    if spec is None:
        return ()
    return spec.base_domains or spec.domains


def provider_html_path_templates(provider_name: str | None) -> tuple[str, ...]:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.html_path_templates if spec is not None else ()


def provider_pdf_path_templates(provider_name: str | None) -> tuple[str, ...]:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.pdf_path_templates if spec is not None else ()


def provider_pdf_source_path_templates(provider_name: str | None) -> tuple[PdfSourcePathTemplate, ...]:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.pdf_source_path_templates if spec is not None else ()


def provider_crossref_pdf_position(provider_name: str | None) -> int:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return int(spec.crossref_pdf_position) if spec is not None else 0


def matching_provider_domain(provider_name: str | None, hostname: str | None) -> str | None:
    for domain in provider_domains(provider_name):
        if host_matches_domain(hostname, domain):
            return domain
    return None


def provider_domain_matches(provider_name: str | None, hostname: str | None) -> bool:
    return matching_provider_domain(provider_name, hostname) is not None


def api_like_hosts() -> frozenset[str]:
    return frozenset(
        _normalize_hostname(host)
        for spec in PROVIDER_CATALOG.values()
        for host in spec.api_hosts
        if _normalize_hostname(host)
    )


def is_declared_api_host(hostname: str | None) -> bool:
    return _normalize_hostname(hostname) in api_like_hosts()


def provider_api_url_template(provider_name: str | None, template_name: str) -> str | None:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    if spec is None:
        return None
    for name, template in spec.api_url_templates:
        if name == template_name:
            return template
    return None


def provider_sensitive_header_names() -> frozenset[str]:
    return frozenset(
        _normalize_catalog_token(header)
        for spec in PROVIDER_CATALOG.values()
        for header in spec.sensitive_headers
        if _normalize_catalog_token(header)
    )


def _load_callable(callback_path: str) -> MetadataProbeShortCircuit:
    module_path, _, attribute = callback_path.partition(":")
    if not module_path or not attribute:
        raise ValueError(f"Invalid provider callback path: {callback_path!r}")
    module = importlib.import_module(module_path)
    callback = getattr(module, attribute)
    if not callable(callback):
        raise TypeError(f"Provider callback path is not callable: {callback_path!r}")
    return callback


def register_metadata_probe_short_circuit(
    provider_name: str,
    callback: MetadataProbeShortCircuit,
) -> None:
    normalized = _normalize_catalog_token(provider_name)
    if not normalized:
        raise ValueError("Provider name is required for metadata probe short-circuit registration.")
    if not callable(callback):
        raise TypeError("Metadata probe short-circuit must be callable.")
    _METADATA_PROBE_SHORT_CIRCUITS[normalized] = callback


def provider_metadata_probe_short_circuit(
    provider_name: str | None,
) -> MetadataProbeShortCircuit | None:
    normalized = _normalize_catalog_token(provider_name)
    if not normalized:
        return None
    callback = _METADATA_PROBE_SHORT_CIRCUITS.get(normalized)
    if callback is not None:
        return callback
    spec = PROVIDER_CATALOG.get(normalized)
    declared = spec.metadata_probe_short_circuit if spec is not None else None
    if declared is None:
        return None
    if isinstance(declared, str):
        callback = _load_callable(declared)
    else:
        callback = declared
    _METADATA_PROBE_SHORT_CIRCUITS[normalized] = callback
    return callback


def provider_persists_provider_html(provider_name: str | None) -> bool:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return bool(spec and spec.persist_provider_html)


def provider_for_xml_source(root_tag: str | None, xml_path: str | None) -> str:
    root_name = _normalize_catalog_token(root_tag)
    lower_path = str(xml_path or "").lower()
    for spec in ordered_provider_specs():
        if any(token and token.lower() in lower_path for token in spec.xml_file_tokens):
            return spec.name
    for spec in ordered_provider_specs():
        if root_name and root_name in {_normalize_catalog_token(tag) for tag in spec.xml_root_tags}:
            return spec.name
    return "unknown"


def provider_emits_html_managed_marker(provider_name: str | None) -> bool:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return bool(spec and spec.official and spec.emits_html_managed_marker)


def provider_body_text_thresholds(provider_name: str | None) -> BodyTextThresholds:
    normalized = _normalize_catalog_token(provider_name)
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.body_text_thresholds if spec is not None else DEFAULT_BODY_TEXT_THRESHOLDS


def sources_by_provider() -> dict[str, frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for source, provider in SOURCE_PROVIDER_MAP.items():
        grouped.setdefault(provider, set()).add(source)
    return {provider: frozenset(sources) for provider, sources in grouped.items()}


def ordered_provider_specs() -> tuple[ProviderSpec, ...]:
    return tuple(sorted(PROVIDER_CATALOG.values(), key=lambda spec: spec.status_order))


def provider_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in ordered_provider_specs())


def official_provider_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in ordered_provider_specs() if spec.official)


def provider_status_order() -> tuple[str, ...]:
    return provider_names()


def is_official_provider(provider_name: str | None) -> bool:
    normalized = str(provider_name or "").strip().lower()
    spec = PROVIDER_CATALOG.get(normalized)
    return bool(spec and spec.official)


def provider_managed_abstract_only_names() -> frozenset[str]:
    return frozenset(
        spec.name
        for spec in PROVIDER_CATALOG.values()
        if spec.provider_managed_abstract_only
    )


def provider_display_names() -> dict[str, str]:
    return {spec.name: spec.display_name for spec in PROVIDER_CATALOG.values()}


def default_asset_profile_for_provider(provider_name: str | None) -> AssetDefault:
    normalized = str(provider_name or "").strip().lower()
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.asset_default if spec is not None else "none"


def provider_for_source(source_name: str | None) -> str | None:
    normalized = str(source_name or "").strip().lower()
    return SOURCE_PROVIDER_MAP.get(normalized)


def known_article_source_names() -> frozenset[str]:
    return frozenset(SOURCE_PROVIDER_MAP)


def default_asset_profile_for_source(source_name: str | None) -> AssetDefault:
    provider_name = provider_for_source(source_name)
    return default_asset_profile_for_provider(provider_name)


def provider_probe_capability(provider_name: str | None) -> str:
    normalized = str(provider_name or "").strip().lower()
    spec = PROVIDER_CATALOG.get(normalized)
    return spec.probe_capability if spec is not None else ""


def provider_supports_metadata_api_probe(provider_name: str | None) -> bool:
    return provider_probe_capability(provider_name) == "metadata_api"


def doi_prefix_provider_map() -> dict[str, str]:
    return {
        prefix: spec.name
        for spec in ordered_provider_specs()
        for prefix in spec.doi_prefixes
    }


def url_provider_tokens() -> dict[str, tuple[str, ...]]:
    return {
        spec.name: spec.domains + spec.domain_suffixes
        for spec in ordered_provider_specs()
        if spec.domains or spec.domain_suffixes
    }


def _validate_source_kind_catalog_sync() -> None:
    from .models.schema import SourceKind

    source_kind_names = frozenset(get_args(SourceKind))
    known_names = known_article_source_names()
    if source_kind_names != known_names:
        missing_from_literal = sorted(known_names - source_kind_names)
        missing_from_catalog = sorted(source_kind_names - known_names)
        raise RuntimeError(
            "SourceKind Literal and SOURCE_PROVIDER_MAP are out of sync: "
            f"missing_from_literal={missing_from_literal!r}, "
            f"missing_from_catalog={missing_from_catalog!r}"
        )


_validate_source_kind_catalog_sync()
