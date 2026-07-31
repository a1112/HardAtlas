import json
from datetime import UTC, datetime

import httpx
import pytest
from hardatlas_data import (
    KnowledgeRepository,
    LexicalSearchBackend,
    OpenSearchBackend,
    SearchPublicationService,
)
from hardatlas_domain import KnowledgeEntity
from sqlalchemy import create_engine


def entity(
    *,
    slug: str,
    canonical_name: str,
    alias: str,
    type_id: str = "type-animal",
    taxonomy_node_id: str = "tax-animals",
    citation_tier: str | None = None,
) -> KnowledgeEntity:
    citations = (
        [
            {
                "id": f"citation-{slug}",
                "sourceId": "source-test",
                "sourceTitle": "Test source",
                "sourceTier": citation_tier,
                "retrievedAt": datetime(2026, 7, 29, tzinfo=UTC),
            }
        ]
        if citation_tier
        else []
    )
    return KnowledgeEntity.model_validate(
        {
            "ref": {
                "id": f"entity-{slug}",
                "slug": slug,
                "typeId": type_id,
                "canonicalName": canonical_name,
            },
            "names": [{"locale": "zh-CN", "value": canonical_name}],
            "aliases": [{"locale": "la", "value": alias}],
            "description": [{"locale": "zh-CN", "value": f"{canonical_name}的描述"}],
            "taxonomyNodeIds": [taxonomy_node_id],
            "claims": [],
            "sections": [],
            "relationships": [],
            "citations": citations,
            "revision": {
                "revisionId": f"revision-{slug}",
                "dataVersion": "atlas-test",
                "schemaVersion": "schema-test",
                "policyVersion": "policy-test",
                "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
            },
            "publicationStatus": "published",
        }
    )


def test_lexical_backend_searches_aliases_and_filters_types() -> None:
    backend = LexicalSearchBackend(
        [
            entity(
                slug="snow-leopard",
                canonical_name="雪豹",
                alias="Panthera uncia",
            ),
            entity(
                slug="ne555",
                canonical_name="NE555 定时器",
                alias="555 timer",
                type_id="type-electronic-component",
            ),
        ]
    )
    hits = backend.search("Panthera uncia")
    assert len(hits) == 1
    assert hits[0].slug == "snow-leopard"
    assert hits[0].matched_field == "alias"
    assert backend.search("555", type_id="type-animal") == []


def test_lexical_backend_browses_with_real_facets_and_evidence_filters() -> None:
    backend = LexicalSearchBackend(
        [
            entity(
                slug="snow-leopard",
                canonical_name="雪豹",
                alias="Panthera uncia",
                citation_tier="authoritative",
            ),
            entity(
                slug="ne555",
                canonical_name="NE555 定时器",
                alias="555 timer",
                type_id="type-electronic-component",
                taxonomy_node_id="tax-electronics",
                citation_tier="primary",
            ),
        ],
        entity_type_spaces={
            "type-animal": "space-life",
            "type-electronic-component": "space-engineering",
        },
    )

    page = backend.search_page(
        "",
        space_id="space-life",
        taxonomy_node_id="tax-animals",
        locale="la",
        source_tier="authoritative",
        minimum_sources=1,
    )

    assert page.total == 1
    assert page.hits[0].slug == "snow-leopard"
    assert page.hits[0].matched_field == "browse"
    assert page.facets["spaceId"] == {"space-life": 1}
    assert page.facets["sourceTier"] == {"authoritative": 1}


def test_opensearch_search_builds_filters_and_reads_highlight() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_score": 7.25,
                            "_source": {
                                "slug": "snow-leopard",
                                "canonical_name": "雪豹",
                                "aliases": ["Panthera uncia"],
                            },
                            "highlight": {"aliases": ["Panthera uncia"]},
                        }
                    ]
                }
            },
        )

    backend = OpenSearchBackend(
        base_url="http://search.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    hits = backend.search(
        "Panthera uncia",
        type_id="type-animal",
        space_id="space-life",
        taxonomy_node_id="tax-animals",
        locale="la",
        source_tier="authoritative",
        limit=7,
        offset=14,
    )
    assert hits[0].matched_field == "alias"
    payload = json.loads(requests[0].content)
    assert payload["size"] == 7
    assert payload["from"] == 14
    assert {"term": {"type_id": "type-animal"}} in payload["query"]["bool"]["filter"]
    assert {"term": {"space_id": "space-life"}} in payload["query"]["bool"]["filter"]
    assert {
        "term": {"taxonomy_node_ids": "tax-animals"}
    } in payload["query"]["bool"]["filter"]
    assert {"term": {"locales": "la"}} in payload["query"]["bool"]["filter"]
    assert {
        "term": {"source_tiers": "authoritative"}
    } in payload["query"]["bool"]["filter"]
    assert set(payload["aggs"]) == {
        "typeId",
        "spaceId",
        "taxonomyNodeId",
        "locale",
        "sourceTier",
    }


def test_opensearch_publication_creates_bulk_index_and_switches_alias() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/_bulk":
            return httpx.Response(200, json={"errors": False, "items": []})
        return httpx.Response(200, json={"acknowledged": True})

    backend = OpenSearchBackend(
        base_url="http://search.test",
        index_alias="atlas-read",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    index_name = backend.publish(
        entities=[
            entity(
                slug="snow-leopard",
                canonical_name="雪豹",
                alias="Panthera uncia",
            )
        ],
        data_version="Atlas 2026.07.29 RC1",
    )
    assert index_name == "atlas-knowledge-atlas-2026-07-29-rc1"
    assert [request.url.path for request in requests] == [
        f"/{index_name}",
        "/_bulk",
        f"/{index_name}/_refresh",
        "/_aliases",
    ]
    bulk_lines = requests[1].content.decode().strip().splitlines()
    assert json.loads(bulk_lines[0])["index"]["_index"] == index_name
    assert json.loads(bulk_lines[1])["slug"] == "snow-leopard"


def test_opensearch_resolves_the_single_read_alias_index() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "atlas-knowledge-atlas-test": {
                    "aliases": {"atlas-read": {}}
                }
            },
        )

    backend = OpenSearchBackend(
        base_url="http://search.test",
        index_alias="atlas-read",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert backend.current_index() == "atlas-knowledge-atlas-test"
    assert requests[0].url.path == "/_alias/atlas-read"


def test_search_publication_consumes_entity_outbox_after_success() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    snow_leopard = entity(
        slug="snow-leopard",
        canonical_name="雪豹",
        alias="Panthera uncia",
    )
    repository.save_entity(snow_leopard)
    backend = LexicalSearchBackend([])
    result = SearchPublicationService(
        repository=repository,
        search_backend=backend,
    ).publish_snapshot(data_version="atlas-test")
    assert result.entity_count == 1
    assert result.outbox_event_count == 1
    assert result.index_name == "memory:atlas-test"
    assert repository.pending_outbox_ids() == []
    assert backend.search("Panthera uncia")[0].slug == "snow-leopard"


def test_source_definitions_and_snapshots_are_immutable() -> None:
    from hardatlas_domain import SourceDefinition, SourceSnapshot

    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    source = SourceDefinition.model_validate(
        {
            "id": "source-test",
            "version": "1.0.0",
            "name": "Test source",
            "kind": "api",
            "baseUrl": "https://api.example.org/data",
            "allowedHosts": ["api.example.org"],
            "trustTier": "primary",
            "licenseId": "open-test",
            "licenseStatus": "allowed",
            "robotsPolicy": "explicit-api",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        }
    )
    repository.save_source_definition(source)
    assert repository.get_source_definition("source-test") == source

    changed = source.model_copy(update={"name": "Mutated"})
    with pytest.raises(ValueError, match="immutable"):
        repository.save_source_definition(changed)

    snapshot = SourceSnapshot.model_validate(
        {
            "id": "snapshot-test",
            "sourceId": "source-test",
            "sourceVersion": "1.0.0",
            "url": "https://api.example.org/data",
            "contentSha256": "a" * 64,
            "storageKey": f"sources/source-test/{'a' * 64}",
            "mediaType": "application/json",
            "byteSize": 2,
            "httpStatus": 200,
            "licenseId": "open-test",
            "captureStatus": "captured",
        }
    )
    repository.save_source_snapshot(snapshot)
    assert repository.list_source_snapshots(source_id="source-test") == [snapshot]
    changed_snapshot = snapshot.model_copy(update={"byte_size": 3})
    with pytest.raises(ValueError, match="immutable"):
        repository.save_source_snapshot(changed_snapshot)
