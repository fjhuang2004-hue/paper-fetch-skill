#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
DOI_RE = re.compile(r"^10\.[^/\s]+/.+")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _doi_slug(doi: str) -> str:
    return doi.replace("/", "_")


def _class_name(provider_name: str) -> str:
    return "".join(part.capitalize() for part in provider_name.split("_")) + "Client"


def _parse_html_capable(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise argparse.ArgumentTypeError("--html-capable must be true or false")


def _write_new(path: Path, content: str = "") -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"samples": {}}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest root must be an object: {path}")
    samples = manifest.setdefault("samples", {})
    if not isinstance(samples, dict):
        raise ValueError(f"manifest samples must be an object: {path}")
    return manifest


def _html_module_content(
    *,
    name: str,
    doi: str,
    source: str,
    fulltext_client: bool,
    html_capable: bool,
) -> str:
    display_name = name.replace("_", " ").title()
    doi_prefix = doi.split("/", 1)[0] + "/"
    client_factory_path = (
        f"paper_fetch.providers.{name}:{_class_name(name)}" if fulltext_client else ""
    )
    catalog_lines = [
        "        catalog=ProviderSpec(",
        f'            name="{name}",',
        f'            display_name="{display_name}",',
        "            official=True,",
        "            domains=(),",
        f'            doi_prefixes=("{doi_prefix}",),',
        f'            publisher_aliases=("{source}",),',
        '            asset_default="none",',
        '            probe_capability="routing_signal",',
        "            provider_managed_abstract_only=False,",
        f'            client_factory_path="{client_factory_path}",',
        "            status_order=999,",
    ]
    if not html_capable:
        catalog_lines.append("            html_capable=False,")
    catalog_lines.append("        ),")

    bundle_lines = [*catalog_lines]
    if html_capable:
        bundle_lines.extend(
            [
                "        html_rules=ProviderHtmlRules(",
                f'            name="{name}",',
                "            availability=AvailabilityPolicy(",
                f'                name="{name}",',
                "                no_signals=True,",
                "            ),",
                "        ),",
            ]
        )
    bundle_lines.append(f'        sources=("{source}",),')

    imports = [
        '"""Provider scaffold for TODO: fill provider-specific HTML extraction rules."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
    ]
    if html_capable:
        imports.extend(
            [
                "",
                "from ..extraction.html.availability_policy import AvailabilityPolicy",
                "from ..extraction.html.provider_rules import ProviderHtmlRules",
            ]
        )
    if fulltext_client:
        imports.extend(
            [
                "from ..reason_codes import NOT_SUPPORTED",
                "from .base import ProviderFailure",
            ]
        )
    imports.extend(
        [
            "from ..provider_catalog import ProviderSpec",
            "from ._registry import ProviderBundle, register_provider_bundle",
            "",
            "",
            "register_provider_bundle(",
            "    ProviderBundle(",
            *bundle_lines,
            "    )",
            ")",
            "",
            "",
            f"def {name}_before_block_normalization(container: Any) -> Any:",
            "    return container",
            "",
            "",
            f"def {name}_normalize_markdown(text: str) -> str:",
            "    return text",
            "",
            "",
            "def extract_authors(html_text: str) -> list[str]:",
            "    return []",
            "",
        ]
    )
    if fulltext_client:
        imports.extend(
            [
                "",
                "",
                f"def {name}_fetch_landing_step(client: object, doi: str, metadata: dict[str, object], *, context: object | None = None):",
                "    del client, doi, metadata, context",
                f'    raise ProviderFailure(NOT_SUPPORTED, "{display_name} landing fallback is not implemented yet.")',
                "",
                "",
                f"def {name}_fetch_html_step(client: object, doi: str, metadata: dict[str, object], *, context: object | None = None):",
                "    del client, doi, metadata, context",
                f'    raise ProviderFailure(NOT_SUPPORTED, "{display_name} HTML fallback is not implemented yet.")',
                "",
                "",
                f"def {name}_fetch_xml_step(client: object, doi: str, metadata: dict[str, object], *, context: object | None = None):",
                "    del client, doi, metadata, context",
                f'    raise ProviderFailure(NOT_SUPPORTED, "{display_name} XML fallback is not implemented yet.")',
                "",
                "",
                f"def {name}_fetch_pdf_step(client: object, doi: str, metadata: dict[str, object], *, context: object | None = None):",
                "    del client, doi, metadata, context",
                f'    raise ProviderFailure(NOT_SUPPORTED, "{display_name} PDF fallback is not implemented yet.")',
                "",
            ]
        )
    return "\n".join(imports)


def _client_module_content(name: str) -> str:
    class_name = _class_name(name)
    return "\n".join(
        [
            f'"""TODO: fill {name} full-text client implementation."""',
            "",
            "from __future__ import annotations",
            "",
            f"from . import _{name}_html as _provider_rules",
            "from ._waterfall import DEFAULT_WATERFALL_CONTINUE_CODES, WaterfallStep",
            "from .base import ProviderClient",
            "",
            "",
            f"class {class_name}(ProviderClient):",
            f'    name = "{name}"',
            "    waterfall_steps = (",
            "        WaterfallStep(",
            '            label="landing",',
            f"            run=_provider_rules.{name}_fetch_landing_step,",
            f'            failure_marker="fulltext:{name}_landing_failed",',
            f'            success_markers=("fulltext:{name}_landing_ok",),',
            "            continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,",
            "        ),",
            "        WaterfallStep(",
            '            label="html",',
            f"            run=_provider_rules.{name}_fetch_html_step,",
            f'            failure_marker="fulltext:{name}_html_failed",',
            f'            success_markers=("fulltext:{name}_html_ok",),',
            "            continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,",
            "        ),",
            "        WaterfallStep(",
            '            label="xml",',
            f"            run=_provider_rules.{name}_fetch_xml_step,",
            f'            failure_marker="fulltext:{name}_xml_failed",',
            f'            success_markers=("fulltext:{name}_xml_ok",),',
            "            continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,",
            "        ),",
            "        WaterfallStep(",
            '            label="pdf",',
            f"            run=_provider_rules.{name}_fetch_pdf_step,",
            f'            failure_marker="fulltext:{name}_pdf_failed",',
            f'            success_markers=("fulltext:{name}_pdf_ok",),',
            "            continue_codes=DEFAULT_WATERFALL_CONTINUE_CODES,",
            "        ),",
            "    )",
            "",
            "",
            f"__all__ = [\"{class_name}\"]",
            "",
        ]
    )


def _test_module_content(name: str, doi: str, *, html_capable: bool) -> str:
    slug = _doi_slug(doi)
    html_rule_assertions = [
        "    assert bundle.html_rules is not None",
        f'    assert bundle.html_rules.name == "{name}"',
    ]
    if not html_capable:
        html_rule_assertions = [
            "    assert bundle.html_rules is None",
            "    assert bundle.catalog.html_capable is False",
        ]
    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            "import pytest",
            "",
            "from paper_fetch.provider_catalog import PROVIDER_CATALOG",
            "from paper_fetch.providers._registry import provider_bundle",
            f"import paper_fetch.providers._{name}_html  # noqa: F401",
            "",
            "",
            "def test_provider_bundle_round_trip() -> None:",
            f'    bundle = provider_bundle("{name}")',
            f'    assert bundle.catalog.name == "{name}"',
            *html_rule_assertions,
            "",
            "",
            "def test_provider_catalog_is_readable() -> None:",
            f'    assert PROVIDER_CATALOG["{name}"].name == "{name}"',
            "",
            "",
            "@pytest.mark.skip(reason=\"TODO: add recorded golden fixture assets before enabling replay\")",
            "def test_provider_golden_replay_placeholder() -> None:",
            f'    assert "{slug}"',
            "",
        ]
    )


def _manifest_entry(*, name: str, doi: str, html_capable: bool) -> dict[str, object]:
    route_kind = "html" if html_capable else "official"
    content_type = "text/html" if html_capable else "application/octet-stream"
    return {
        "doi": doi,
        "publisher": name,
        "title": "TODO: fill golden criteria title",
        "source_url": "",
        "landing_url": "",
        "route_kind": route_kind,
        "content_type": content_type,
        "origin_kind": "placeholder",
        "usage_kind": "content",
        "fixture_family": "golden",
        "expected_outcome": "pending",
        "assets": {},
    }


def scaffold(args: argparse.Namespace) -> list[Path]:
    root = Path(args.output_dir).resolve()
    name = args.name
    source = args.source or name
    slug = _doi_slug(args.doi)

    if not NAME_RE.fullmatch(name):
        raise ValueError("--name must be snake_case starting with a lowercase letter")
    if not NAME_RE.fullmatch(source):
        raise ValueError("--source must be snake_case when provided")
    if not DOI_RE.fullmatch(args.doi):
        raise ValueError("--doi must look like a DOI, for example 10.1234/sample")

    html_module = root / "src" / "paper_fetch" / "providers" / f"_{name}_html.py"
    client_module = root / "src" / "paper_fetch" / "providers" / f"{name}.py"
    test_module = root / "tests" / "unit" / f"test_{name}_provider.py"
    fixture_keep = (
        root / "tests" / "fixtures" / "golden_criteria" / slug / ".gitkeep"
    )
    manifest_path = root / "tests" / "fixtures" / "golden_criteria" / "manifest.json"

    planned = [html_module, test_module, fixture_keep]
    if args.fulltext_client:
        planned.append(client_module)
    for path in planned:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing path: {path}")

    manifest = _load_manifest(manifest_path)
    samples = manifest["samples"]
    if slug in samples:
        raise FileExistsError(f"manifest sample already exists: {slug}")

    written: list[Path] = []
    _write_new(
        html_module,
        _html_module_content(
            name=name,
            doi=args.doi,
            source=source,
            fulltext_client=args.fulltext_client,
            html_capable=args.html_capable,
        ),
    )
    written.append(html_module)
    if args.fulltext_client:
        _write_new(client_module, _client_module_content(name))
        written.append(client_module)
    _write_new(fixture_keep)
    written.append(fixture_keep)
    _write_new(
        test_module,
        _test_module_content(name, args.doi, html_capable=args.html_capable),
    )
    written.append(test_module)

    samples[slug] = _manifest_entry(
        name=name,
        doi=args.doi,
        html_capable=args.html_capable,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    written.append(manifest_path)
    return written


def _print_checklist(paths: list[Path], root: Path) -> None:
    print("PR-checklist TODO:")
    print("- Fill ProviderSpec domains, aliases, routing templates, and status_order.")
    print("- Replace placeholder HTML rules with provider-owned cleanup and availability signals.")
    print("- Add recorded golden fixture assets and enable the generated replay test.")
    print("- Add the provider entry module to provider discovery in the same PR.")
    print("- Run python3 scripts/validate_extraction_rules.py and targeted pytest.")
    print("Generated files:")
    for path in paths:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        print(f"- {rel.as_posix()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scaffold provider-owned bundle, tests, and golden fixture placeholders."
    )
    parser.add_argument("--name", required=True, help="provider name in snake_case")
    parser.add_argument("--doi", required=True, help="placeholder golden DOI")
    parser.add_argument(
        "--source",
        help="public source name to register; defaults to --name",
    )
    parser.add_argument(
        "--fulltext-client",
        action="store_true",
        help="also generate src/paper_fetch/providers/NAME.py client skeleton",
    )
    parser.add_argument(
        "--html-capable",
        type=_parse_html_capable,
        default=True,
        metavar="true|false",
        help="set to false to skip ProviderHtmlRules placeholder",
    )
    parser.add_argument(
        "--output-dir",
        default=_repo_root(),
        help="repo root to write into; defaults to this checkout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = scaffold(args)
    except (FileExistsError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    _print_checklist(paths, Path(args.output_dir).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
