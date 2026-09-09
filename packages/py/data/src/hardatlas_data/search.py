import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from hardatlas_domain import KnowledgeEntity


class SearchBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    slug: str
    matched_text: str
    matched_field: str
    score: float


@dataclass(frozen=True)
class SearchResultPage:
    hits: list[SearchHit]
    total: int
    facets: dict[str, dict[str, int]]


class SearchBackend(Protocol):
    name: str

    def ready(self) -> bool: ...

    def current_index(self) -> str | None: ...

    def search(
        self,
        query: str,
        *,
        type_id: str | None = None,
        space_id: str | None = None,
        taxonomy_node_id: str | None = None,
        locale: str | None = None,
        source_tier: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SearchHit]: ...

    def search_page(
        self,
        query: str,
        *,
        type_id: str | None = None,
        space_id: str | None = None,
        taxonomy_node_id: str | None = None,
        locale: str | None = None,
        source_tier: str | None = None,
        minimum_sources: int = 0,
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResultPage: ...

    def publish(
        self,
        *,
        entities: list[KnowledgeEntity],
        data_version: str,
    ) -> str: ...

    def stage(
        self,
        *,
        entities: list[KnowledgeEntity],
        data_version: str,
    ) -> str: ...

    def activate(self, index_name: str) -> None: ...


def entity_search_document(
    entity: KnowledgeEntity,
    entity_type_spaces: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "entity_id": entity.ref.id,
        "slug": entity.ref.slug,
        "type_id": entity.ref.type_id,
        "space_id": (entity_type_spaces or {}).get(entity.ref.type_id),
        "canonical_name": entity.ref.canonical_name,
        "names": [item.value for item in entity.names],
        "aliases": [item.value for item in entity.aliases],
        "descriptions": [item.value for item in entity.description],
        "claim_values": [
            item.value
            for claim in entity.claims
            for item in claim.display_value
        ],
        "taxonomy_node_ids": entity.taxonomy_node_ids,
        "source_tiers": sorted({citation.source_tier for citation in entity.citations}),
        "citation_count": len(entity.citations),
        "locales": sorted(
            {item.locale for item in [*entity.names, *entity.aliases, *entity.description]}
        ),
        "publication_status": entity.publication_status,
        "data_version": entity.revision.data_version,
    }


class LexicalSearchBackend:
    name = "memory-lexical"

    def __init__(
        self,
        entities: list[KnowledgeEntity],
        *,
        entity_type_spaces: dict[str, str] | None = None,
    ) -> None:
        self.active_index = "memory:bootstrap"
        self._staged: dict[str, list[KnowledgeEntity]] = {}
        self.entity_type_spaces = entity_type_spaces or {}
        self.replace_all(entities)

    def replace_all(self, entities: list[KnowledgeEntity]) -> None:
        self.documents = {
            entity.ref.slug: entity_search_document(
                entity,
                self.entity_type_spaces,
            )
            for entity in entities
        }

    def ready(self) -> bool:
        return True

    def current_index(self) -> str:
        return self.active_index

    def search(
        self,
        query: str,
        *,
        type_id: str | None = None,
        space_id: str | None = None,
        taxonomy_node_id: str | None = None,
        locale: str | None = None,
        source_tier: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SearchHit]:
        return self.search_page(
            query,
            type_id=type_id,
            space_id=space_id,
            taxonomy_node_id=taxonomy_node_id,
            locale=locale,
            source_tier=source_tier,
            limit=limit,
            offset=offset,
        ).hits

    def search_page(
        self,
        query: str,
        *,
        type_id: str | None = None,
        space_id: str | None = None,
        taxonomy_node_id: str | None = None,
        locale: str | None = None,
        source_tier: str | None = None,
        minimum_sources: int = 0,
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResultPage:
        normalized = query.casefold().strip()
        field_weights = [
            ("canonicalName", "canonical_name", 1.0),
            ("name", "names", 0.98),
            ("alias", "aliases", 0.92),
            ("claim", "claim_values", 0.8),
            ("description", "descriptions", 0.72),
        ]
        hits: list[SearchHit] = []
        for document in self.documents.values():
            if document["publication_status"] != "published":
                continue
            if type_id and document["type_id"] != type_id:
                continue
            if space_id and document["space_id"] != space_id:
                continue
            if (
                taxonomy_node_id
                and taxonomy_node_id not in document["taxonomy_node_ids"]
            ):
                continue
            if locale and locale not in document["locales"]:
                continue
            if source_tier and source_tier not in document["source_tiers"]:
                continue
            if int(document["citation_count"]) < minimum_sources:
                continue
            if not normalized:
                hits.append(
                    SearchHit(
                        slug=str(document["slug"]),
                        matched_text=str(document["canonical_name"]),
                        matched_field="browse",
                        score=1.0,
                    )
                )
                continue
            best: SearchHit | None = None
            for public_field, field, weight in field_weights:
                raw_values = document[field]
                values = raw_values if isinstance(raw_values, list) else [raw_values]
                for value in values:
                    normalized_value = str(value).casefold()
                    if normalized not in normalized_value:
                        continue
                    exact_bonus = 0.08 if normalized == normalized_value else 0
                    prefix_bonus = 0.04 if normalized_value.startswith(normalized) else 0
                    hit = SearchHit(
                        slug=str(document["slug"]),
                        matched_text=str(value),
                        matched_field=public_field,
                        score=min(weight + exact_bonus + prefix_bonus, 1.0),
                    )
                    if best is None or hit.score > best.score:
                        best = hit
            if best:
                hits.append(best)
        ordered = sorted(hits, key=lambda item: (-item.score, item.slug))
        filtered_documents = [self.documents[hit.slug] for hit in ordered]
        facets: dict[str, dict[str, int]] = {
            "typeId": {},
            "spaceId": {},
            "taxonomyNodeId": {},
            "locale": {},
            "sourceTier": {},
        }
        for document in filtered_documents:
            facet_values: dict[str, list[str]] = {
                "typeId": [str(document["type_id"])],
                "spaceId": (
                    [str(document["space_id"])] if document["space_id"] else []
                ),
                "taxonomyNodeId": [
                    str(value) for value in document["taxonomy_node_ids"]
                ],
                "locale": [str(value) for value in document["locales"]],
                "sourceTier": [str(value) for value in document["source_tiers"]],
            }
            for facet, values in facet_values.items():
                for value in values:
                    facets[facet][value] = facets[facet].get(value, 0) + 1
        return SearchResultPage(
            hits=ordered[offset : offset + limit],
            total=len(ordered),
            facets=facets,
        )

    def publish(
        self,
        *,
        entities: list[KnowledgeEntity],
        data_version: str,
    ) -> str:
        index_name = self.stage(entities=entities, data_version=data_version)
        self.activate(index_name)
        return index_name

    def stage(
        self,
        *,
        entities: list[KnowledgeEntity],
        data_version: str,
    ) -> str:
        index_name = f"memory:{data_version}"
        self._staged[index_name] = [
            entity.model_copy(deep=True) for entity in entities
        ]
        return index_name

    def activate(self, index_name: str) -> None:
        try:
            entities = self._staged.pop(index_name)
        except KeyError as error:
            if index_name == self.active_index:
                return
            raise SearchBackendError(
                f"staged memory index does not exist: {index_name}"
            ) from error
        self.replace_all(entities)
        self.active_index = index_name


class OpenSearchBackend:
    name = "opensearch"

    def __init__(
        self,
        *,
        base_url: str,
        index_alias: str = "atlas-knowledge-read",
        entity_type_spaces: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_alias = index_alias
        self.entity_type_spaces = entity_type_spaces or {}
        self.client = client or httpx.Client(timeout=10)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            raise SearchBackendError(
                f"OpenSearch {method} {path} failed: {response.status_code} {response.text[:300]}"
            )
        return response

    def ready(self) -> bool:
        try:
            response = self.client.head(
                f"{self.base_url}/{self.index_alias}",
                timeout=2,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def current_index(self) -> str | None:
        response = self._request(
            "GET",
            f"/_alias/{self.index_alias}",
        )
        indexes = sorted(response.json())
        if not indexes:
            return None
        if len(indexes) != 1:
            raise SearchBackendError(
                f"search alias points to {len(indexes)} indexes"
            )
        return indexes[0]

    def search(
        self,
        query: str,
        *,
        type_id: str | None = None,
        space_id: str | None = None,
        taxonomy_node_id: str | None = None,
        locale: str | None = None,
        source_tier: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SearchHit]:
        return self.search_page(
            query,
            type_id=type_id,
            space_id=space_id,
            taxonomy_node_id=taxonomy_node_id,
            locale=locale,
            source_tier=source_tier,
            limit=limit,
            offset=offset,
        ).hits

    def search_page(
        self,
        query: str,
        *,
        type_id: str | None = None,
        space_id: str | None = None,
        taxonomy_node_id: str | None = None,
        locale: str | None = None,
        source_tier: str | None = None,
        minimum_sources: int = 0,
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResultPage:
        filters: list[dict[str, Any]] = [{"term": {"publication_status": "published"}}]
        if type_id:
            filters.append({"term": {"type_id": type_id}})
        if space_id:
            filters.append({"term": {"space_id": space_id}})
        if taxonomy_node_id:
            filters.append({"term": {"taxonomy_node_ids": taxonomy_node_id}})
        if locale:
            filters.append({"term": {"locales": locale}})
        if source_tier:
            filters.append({"term": {"source_tiers": source_tier}})
        if minimum_sources:
            filters.append({"range": {"citation_count": {"gte": minimum_sources}}})
        query_clause: dict[str, Any]
        if query.strip():
            query_clause = {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "canonical_name^6",
                        "names^5",
                        "aliases^4",
                        "claim_values^2",
                        "descriptions",
                    ],
                    "type": "best_fields",
                    "operator": "and",
                    "fuzziness": "AUTO",
                }
            }
        else:
            query_clause = {"match_all": {}}
        body = {
            "size": limit,
            "from": offset,
            "track_total_hits": True,
            "_source": [
                "slug",
                "canonical_name",
                "names",
                "aliases",
                "claim_values",
                "descriptions",
            ],
            "query": {
                "bool": {
                    "must": [query_clause],
                    "filter": filters,
                }
            },
            "aggs": {
                "typeId": {"terms": {"field": "type_id", "size": 100}},
                "spaceId": {"terms": {"field": "space_id", "size": 100}},
                "taxonomyNodeId": {
                    "terms": {"field": "taxonomy_node_ids", "size": 500}
                },
                "locale": {"terms": {"field": "locales", "size": 100}},
                "sourceTier": {"terms": {"field": "source_tiers", "size": 20}},
            },
            "highlight": {
                "fields": {
                    "canonical_name": {},
                    "names": {},
                    "aliases": {},
                    "claim_values": {},
                    "descriptions": {},
                },
                "pre_tags": [""],
                "post_tags": [""],
            },
        }
        response = self._request(
            "POST",
            f"/{self.index_alias}/_search",
            json=body,
        )
        payload = response.json()
        hits: list[SearchHit] = []
        public_fields = {
            "canonical_name": "canonicalName",
            "names": "name",
            "aliases": "alias",
            "claim_values": "claim",
            "descriptions": "description",
        }
        for raw_hit in payload.get("hits", {}).get("hits", []):
            source = raw_hit.get("_source", {})
            highlights = raw_hit.get("highlight", {})
            if not query.strip():
                matched_field = "browse"
                matched_text = str(source.get("canonical_name", ""))
            else:
                matched_index_field = next(
                    (field for field in public_fields if highlights.get(field)),
                    "canonical_name",
                )
                highlighted_values = highlights.get(matched_index_field)
                if highlighted_values:
                    matched_text = str(highlighted_values[0])
                else:
                    source_value = source.get(matched_index_field, "")
                    if isinstance(source_value, list):
                        matched_text = str(source_value[0] if source_value else "")
                    else:
                        matched_text = str(source_value)
                matched_field = public_fields[matched_index_field]
            hits.append(
                SearchHit(
                    slug=str(source["slug"]),
                    matched_text=matched_text,
                    matched_field=matched_field,
                    score=float(raw_hit.get("_score") or 0),
                )
            )
        total_payload = payload.get("hits", {}).get("total", 0)
        total = (
            int(total_payload.get("value", 0))
            if isinstance(total_payload, dict)
            else int(total_payload)
        )
        facets: dict[str, dict[str, int]] = {}
        for facet_name, aggregation in payload.get("aggregations", {}).items():
            facets[facet_name] = {
                str(bucket["key"]): int(bucket["doc_count"])
                for bucket in aggregation.get("buckets", [])
            }
        return SearchResultPage(hits=hits, total=total, facets=facets)

    @staticmethod
    def versioned_index_name(data_version: str) -> str:
        safe_version = re.sub(r"[^a-z0-9]+", "-", data_version.casefold()).strip("-")
        return f"atlas-knowledge-{safe_version}"

    def create_versioned_index(self, data_version: str) -> str:
        index_name = self.versioned_index_name(data_version)
        mapping = {
            "settings": {
                "index": {"number_of_shards": 1, "number_of_replicas": 0},
                "analysis": {
                    "normalizer": {
                        "atlas_lowercase": {
                            "type": "custom",
                            "filter": ["lowercase", "asciifolding"],
                        }
                    }
                },
            },
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "entity_id": {"type": "keyword"},
                    "slug": {"type": "keyword"},
                    "type_id": {"type": "keyword"},
                    "space_id": {"type": "keyword"},
                    "canonical_name": {
                        "type": "text",
                        "fields": {
                            "keyword": {
                                "type": "keyword",
                                "normalizer": "atlas_lowercase",
                            }
                        },
                    },
                    "names": {"type": "text"},
                    "aliases": {"type": "text"},
                    "claim_values": {"type": "text"},
                    "descriptions": {"type": "text"},
                    "taxonomy_node_ids": {"type": "keyword"},
                    "source_tiers": {"type": "keyword"},
                    "citation_count": {"type": "integer"},
                    "locales": {"type": "keyword"},
                    "publication_status": {"type": "keyword"},
                    "data_version": {"type": "keyword"},
                },
            },
        }
        self._request("PUT", f"/{index_name}", json=mapping)
        return index_name

    def bulk_index(
        self,
        *,
        index_name: str,
        entities: list[KnowledgeEntity],
    ) -> None:
        lines: list[str] = []
        for entity in entities:
            lines.append(
                json.dumps(
                    {"index": {"_index": index_name, "_id": entity.ref.id}},
                    ensure_ascii=False,
                )
            )
            lines.append(
                json.dumps(
                    entity_search_document(entity, self.entity_type_spaces),
                    ensure_ascii=False,
                )
            )
        if not lines:
            return
        response = self._request(
            "POST",
            "/_bulk",
            content="\n".join(lines) + "\n",
            headers={"content-type": "application/x-ndjson"},
        )
        payload = response.json()
        if payload.get("errors"):
            failures = [
                item for item in payload.get("items", []) if item.get("index", {}).get("error")
            ]
            raise SearchBackendError(f"OpenSearch bulk indexing reported {len(failures)} failures")

    def activate_index(self, index_name: str) -> None:
        self._request("POST", f"/{index_name}/_refresh")
        self._request(
            "POST",
            "/_aliases",
            json={
                "actions": [
                    {
                        "remove": {
                            "index": "*",
                            "alias": self.index_alias,
                            "must_exist": False,
                        }
                    },
                    {"add": {"index": index_name, "alias": self.index_alias}},
                ]
            },
        )

    def publish(
        self,
        *,
        entities: list[KnowledgeEntity],
        data_version: str,
    ) -> str:
        index_name = self.stage(entities=entities, data_version=data_version)
        self.activate(index_name)
        return index_name

    def stage(
        self,
        *,
        entities: list[KnowledgeEntity],
        data_version: str,
    ) -> str:
        index_name = self.create_versioned_index(data_version)
        self.bulk_index(index_name=index_name, entities=entities)
        return index_name

    def activate(self, index_name: str) -> None:
        self.activate_index(index_name)
