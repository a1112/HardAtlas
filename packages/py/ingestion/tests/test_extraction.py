import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hardatlas_domain import (
    ExtractionFieldMapping,
    ExtractionParserDefinition,
    SourceDefinition,
    SourceSnapshot,
)
from hardatlas_ingestion import (
    ExtractionError,
    ParserRegistry,
    extract_snapshot,
    load_parser_registry,
)

RETRIEVED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def parser_definition(
    *,
    format: str = "json",
    media_type: str = "application/json",
    **overrides: object,
) -> ExtractionParserDefinition:
    document: dict[str, object] = {
        "id": f"parser-{format}",
        "version": "1.0.0",
        "format": format,
        "mediaTypes": [media_type],
        "entityTypeId": "entity-type-animal",
        "locale": "zh-CN",
        "recordsPath": "/items" if format == "json" else None,
        "recordSelector": "article.entry" if format == "html" else None,
        "externalIdPath": "id" if format != "html" else "@data-id",
        "labelPath": "name" if format != "html" else "h2",
        "fieldMappings": [
            {
                "sourcePath": "scientificName"
                if format != "html"
                else "[data-field=scientific-name]",
                "targetPath": "/claims/scientific-name",
                "attributeDefinitionId": "attr-scientific-name",
                "confidence": 0.92,
                "required": True,
            }
        ],
        "maxRecords": 10,
    }
    document.update(overrides)
    return ExtractionParserDefinition.model_validate(document)


def source(
    definition: ExtractionParserDefinition,
    *,
    media_type: str,
) -> SourceDefinition:
    kind = "website" if definition.format == "html" else "api"
    return SourceDefinition(
        id="source-fixture",
        version="1.0.0",
        name="Fixture source",
        kind=kind,
        base_url="https://knowledge.example.org/data",
        allowed_hosts=["knowledge.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="respect" if kind == "website" else "explicit-api",
        robots_status="allowed",
        allowed_media_types=[media_type],
        parser_id=definition.id,
        parser_version=definition.version,
        status="active",
    )


def snapshot(content: bytes, *, media_type: str) -> SourceSnapshot:
    digest = hashlib.sha256(content).hexdigest()
    return SourceSnapshot(
        id=f"snapshot-source-fixture-{digest[:16]}",
        source_id="source-fixture",
        source_version="1.0.0",
        url="https://knowledge.example.org/data",
        content_sha256=digest,
        storage_key=f"sources/source-fixture/{digest}",
        media_type=media_type,
        byte_size=len(content),
        http_status=200,
        license_id="CC-BY-4.0",
        capture_status="captured",
        retrieved_at=RETRIEVED_AT,
    )


def test_json_parser_creates_deterministic_provenance_bound_candidates() -> None:
    content = (
        '{"items":[{"id":"panthera-uncia","name":"雪豹",'
        '"scientificName":"Panthera uncia"}]}'
    ).encode()
    definition = parser_definition()
    registered_source = source(definition, media_type="application/json")
    captured = snapshot(content, media_type="application/json")

    batch, candidates = extract_snapshot(
        definition,
        source=registered_source,
        snapshot=captured,
        content=content,
    )
    repeated_batch, repeated_candidates = extract_snapshot(
        definition,
        source=registered_source,
        snapshot=captured,
        content=content,
    )

    assert batch == repeated_batch
    assert candidates == repeated_candidates
    assert batch.candidate_count == 1
    candidate = candidates[0]
    assert candidate.snapshot_id == captured.id
    assert candidate.parser_version == "1.0.0"
    assert candidate.external_id == "panthera-uncia"
    assert candidate.labels[0].value == "雪豹"
    assert candidate.fields[0].proposed_value == "Panthera uncia"
    assert candidate.fields[0].locator.value == "/items/0::scientificName"
    assert candidate.citation.quote_hash


def test_csv_and_html_adapters_use_restricted_declarative_mappings() -> None:
    csv_content = "id,name,scientificName\ninkgo,银杏,Ginkgo biloba\n".encode()
    csv_definition = parser_definition(format="csv", media_type="text/csv")
    _, csv_candidates = extract_snapshot(
        csv_definition,
        source=source(csv_definition, media_type="text/csv"),
        snapshot=snapshot(csv_content, media_type="text/csv"),
        content=csv_content,
    )
    assert csv_candidates[0].record_locator.value == "2"
    assert csv_candidates[0].fields[0].proposed_value == "Ginkgo biloba"

    html_content = b"""
      <main>
        <article class="entry" data-id="ne555">
          <h2>NE555 timer</h2>
          <span data-field="scientific-name">Bipolar timer IC</span>
        </article>
      </main>
    """
    html_definition = parser_definition(format="html", media_type="text/html")
    _, html_candidates = extract_snapshot(
        html_definition,
        source=source(html_definition, media_type="text/html"),
        snapshot=snapshot(html_content, media_type="text/html"),
        content=html_content,
    )
    assert html_candidates[0].external_id == "ne555"
    assert html_candidates[0].labels[0].value == "NE555 timer"
    assert html_candidates[0].fields[0].proposed_value == "Bipolar timer IC"


def test_parser_rejects_malformed_missing_and_tampered_input() -> None:
    definition = parser_definition()
    registered_source = source(definition, media_type="application/json")
    malformed = b'{"items":'
    with pytest.raises(ExtractionError, match="invalid JSON"):
        extract_snapshot(
            definition,
            source=registered_source,
            snapshot=snapshot(malformed, media_type="application/json"),
            content=malformed,
        )

    missing = b'{"items":[{"id":"x","name":"Missing field"}]}'
    with pytest.raises(ExtractionError, match="required field"):
        extract_snapshot(
            definition,
            source=registered_source,
            snapshot=snapshot(missing, media_type="application/json"),
            content=missing,
        )

    captured = snapshot(b'{"items":[]}', media_type="application/json")
    with pytest.raises(ExtractionError, match="content hash"):
        extract_snapshot(
            definition,
            source=registered_source,
            snapshot=captured,
            content=b'{"items":{}}',
        )


def test_registry_requires_pinned_immutable_parser_versions() -> None:
    definition = parser_definition()
    registry = ParserRegistry([definition])
    assert (
        registry.resolve_for_source(
            source(definition, media_type="application/json")
        )
        == definition
    )

    changed = definition.model_copy(
        update={
            "field_mappings": [
                ExtractionFieldMapping(
                    source_path="other",
                    target_path="/description",
                )
            ]
        }
    )
    with pytest.raises(ExtractionError, match="immutable"):
        registry.register(changed)


def test_load_parser_registry_discovers_nested_parser_definitions(tmp_path: Path) -> None:
    root = tmp_path / "parsers"
    nested = root / "nested"
    nested.mkdir(parents=True)

    root_definition = parser_definition()
    nested_definition = parser_definition(format="csv", media_type="text/csv")

    (root / "root.json").write_text(
        root_definition.model_dump_json(by_alias=True),
        encoding="utf-8",
    )
    (nested / "nested.json").write_text(
        nested_definition.model_dump_json(by_alias=True),
        encoding="utf-8",
    )
    registry = load_parser_registry(str(root))

    assert (
        registry.resolve_for_source(source(root_definition, media_type="application/json"))
        == root_definition
    )
    assert (
        registry.resolve_for_source(
            source(
                nested_definition,
                media_type="text/csv",
            )
        )
        == nested_definition
    )


def test_load_parser_registry_supports_absolute_glob_paths(tmp_path: Path) -> None:
    root = tmp_path / "parsers"
    nested = root / "nested"
    nested.mkdir(parents=True)

    nested_definition = parser_definition(
        format="jsonl",
        media_type="application/x-jsonlines",
    )
    (nested / "nested.json").write_text(
        nested_definition.model_dump_json(by_alias=True),
        encoding="utf-8",
    )
    registry = load_parser_registry(str(root / "*" / "*.json"))

    assert (
        registry.resolve_for_source(
            source(nested_definition, media_type="application/x-jsonlines")
        )
        == nested_definition
    )


def test_load_parser_registry_supports_glob_to_directories(tmp_path: Path) -> None:
    root = tmp_path / "parsers"
    nested = root / "nested"
    nested.mkdir(parents=True)

    nested_definition = parser_definition(
        id="parser-glob-directory",
        format="json",
        media_type="application/json",
    )
    (nested / "nested.json").write_text(
        nested_definition.model_dump_json(by_alias=True),
        encoding="utf-8",
    )
    registry = load_parser_registry(str(root / "*"))

    assert (
        registry.resolve_for_source(
            source(nested_definition, media_type="application/json")
        )
        == nested_definition
    )


def test_load_parser_registry_rejects_unmatched_path(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ExtractionError, match="no match"):
        load_parser_registry(str(root / "missing" / "*.json"))


def test_load_parser_registry_rejects_empty_directory(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ExtractionError, match="no match"):
        load_parser_registry(str(empty_dir))


def test_load_parser_registry_rejects_glob_to_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()
    with pytest.raises(ExtractionError, match="no match"):
        load_parser_registry(f"{tmp_path}/*-dir")
