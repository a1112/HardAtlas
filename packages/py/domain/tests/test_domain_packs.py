from pathlib import Path

from hardatlas_domain import load_domain_pack, load_domain_registry


def test_reference_domain_packs_share_the_kernel_contract() -> None:
    root = Path(__file__).parents[4]
    packs = [
        load_domain_pack(root / "domain-packs/animals"),
        load_domain_pack(root / "domain-packs/plants"),
        load_domain_pack(root / "domain-packs/electronics"),
        load_domain_pack(root / "domain-packs/astronomy"),
    ]
    assert {pack.manifest.id for pack in packs} == {
        "animals",
        "plants",
        "electronics",
        "astronomy",
    }
    assert all(pack.manifest.kernel_version == "2.0" for pack in packs)
    assert all(
        pack.entity_types and pack.relationship_types and pack.views
        for pack in packs
    )


def test_runtime_domain_registry_merges_identical_cross_pack_definitions() -> None:
    root = Path(__file__).parents[4]
    registry = load_domain_registry(
        [
            root / "domain-packs/core",
            root / "domain-packs/animals",
            root / "domain-packs/plants",
            root / "domain-packs/electronics",
            root / "domain-packs/astronomy",
        ]
    )
    assert set(registry.packs) == {
        "atlas-core-spaces",
        "animals",
        "plants",
        "electronics",
        "astronomy",
    }
    assert set(registry.spaces) == {
        "space-life",
        "space-engineering",
        "space-earth",
        "space-humanities",
        "space-mathematics",
        "space-medicine",
        "space-astronomy",
    }
    assert set(registry.taxonomy_nodes) == {
        "tax-animals",
        "tax-plants",
        "tax-electronics",
        "tax-geography",
        "tax-geology",
        "tax-history",
        "tax-language",
        "tax-mathematics",
        "tax-medicine",
        "tax-astronomy",
        "tax-stars",
        "tax-planets",
    }
    assert set(registry.entity_types) == {
        "type-animal",
        "type-plant",
        "type-electronic-component",
        "type-geography",
        "type-geology",
        "type-history",
        "type-language",
        "type-mathematics",
        "type-medicine",
        "type-celestial-body",
    }
    assert set(registry.relationship_types) == {
        "rel-taxonomic-parent",
        "rel-distributed-in",
        "rel-variant-of",
        "rel-used-in",
        "rel-discovered-by",
    }
    assert (
        registry.entity_types["type-animal"].default_view_definition_id
        == "view-animal"
    )
