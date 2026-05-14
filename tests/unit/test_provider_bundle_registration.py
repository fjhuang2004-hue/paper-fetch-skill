from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

import paper_fetch.providers  # noqa: F401
from paper_fetch.extraction.html.provider_rules import PROVIDER_HTML_RULES
from paper_fetch.provider_catalog import PROVIDER_CATALOG, SOURCE_PROVIDER_MAP
from paper_fetch.providers._registry import (
    ProviderBundle,
    iter_provider_bundles,
    provider_bundle,
)


def test_each_provider_bundle_is_registered_once() -> None:
    bundles = tuple(iter_provider_bundles())
    names = tuple(bundle.catalog.name for bundle in bundles)

    assert len(names) == len(set(names))
    assert set(names) == set(PROVIDER_CATALOG)
    assert len(names) >= 10


@pytest.mark.parametrize("name", tuple(PROVIDER_CATALOG))
def test_provider_bundle_round_trips_catalog_and_rules(name: str) -> None:
    bundle = provider_bundle(name)

    assert bundle.catalog == PROVIDER_CATALOG[name]
    for source in bundle.sources:
        assert SOURCE_PROVIDER_MAP[source] == name
    if bundle.html_rules is not None:
        assert PROVIDER_HTML_RULES[bundle.html_rules.name] == bundle.html_rules


def test_provider_bundle_fields_are_typed_and_frozen() -> None:
    bundle = provider_bundle("ieee")
    field_names = {field.name for field in fields(ProviderBundle)}

    assert {"catalog", "html_rules", "asset_retry", "metadata_merge", "sources"} <= field_names
    assert isinstance(bundle.metadata_merge, tuple)
    assert isinstance(bundle.sources, tuple)

    with pytest.raises(FrozenInstanceError):
        bundle.sources = ()  # type: ignore[misc]


def test_provider_bundle_rejects_mutable_sequence_fields() -> None:
    catalog = PROVIDER_CATALOG["crossref"]

    with pytest.raises(TypeError):
        ProviderBundle(catalog=catalog, metadata_merge=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProviderBundle(catalog=catalog, sources=["crossref_meta"])  # type: ignore[arg-type]
