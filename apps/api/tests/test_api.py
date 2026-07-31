import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from hardatlas_ai import (
    ModelGatewayConfig,
    OpenAICompatibleModelGateway,
    load_agent_pack,
)
from hardatlas_api import create_app
from hardatlas_api.app import build_model_gateway
from hardatlas_api.config import Settings
from hardatlas_data import KnowledgeRepository, LexicalSearchBackend
from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    EntityRef,
    GovernanceService,
    KnowledgeEntity,
    SourceAcquisitionJob,
    SourceDefinition,
    Relationship,
    RevisionContext,
    SourceSnapshot,
)
from hardatlas_ingestion import AcquisitionError
from hardatlas_worker.tasks import (
    dispatch_quality_maintenance_events,
    dispatch_maintenance_work_events,
    dispatch_source_acquisition_events,
    evaluate_governed_proposal_once,
    execute_source_acquisition_once,
    execute_agent_schedule_once,
    request_source_acquisition_for_work_once,
    schedule_quality_maintenance_task_once,
)
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def isolated_repository() -> KnowledgeRepository:
    return KnowledgeRepository(
        create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    )


def api_client(
    repository: KnowledgeRepository | None = None,
    *,
    roles: str = "admin",
    principal: str = "api-test-user",
    model_gateway=None,
    settings: Settings | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            repository or isolated_repository(),
            model_gateway=model_gateway,
            settings=settings,
        ),
        headers={
            "x-hardatlas-dev-principal": principal,
            "x-hardatlas-dev-display-name": "API Test User",
            "x-hardatlas-dev-roles": roles,
        },
    )


client = api_client()


def test_health_identifies_universal_encyclopedia_kernel() -> None:
    assert client.get("/api/v1/health").json() == {
        "status": "ok",
        "service": "atlas-encyclopedia-api",
    }
    build = client.get("/api/v1/build").json()
    assert build["dataVersion"] == "atlas-2026.07.29"
    assert build["schemaVersion"] == "schema-2.2.0"


def test_readiness_checks_required_runtime_components(monkeypatch) -> None:
    local_client = api_client()
    ready = local_client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"] == {
        "database": True,
        "search": True,
        "agentRegistry": True,
        "domainRegistry": True,
        "parserRegistry": True,
    }

    monkeypatch.setattr(
        local_client.app.state.repository,
        "ready",
        lambda: False,
    )
    unavailable = local_client.get("/api/v1/ready")
    assert unavailable.status_code == 503
    assert unavailable.json()["status"] == "not-ready"
    assert unavailable.json()["checks"]["database"] is False


def test_desktop_webview_origin_is_explicitly_allowed() -> None:
    response = client.options(
        "/api/v1/build",
        headers={
            "origin": "http://127.0.0.1:1420",
            "access-control-request-method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:1420"


def test_production_bootstrap_uses_domain_packs_without_fixture_entities() -> None:
    repository = isolated_repository()
    repository.create_schema()
    settings = Settings(
        environment="production",
        auth_mode="oidc",
        seed_fixture_content=False,
        database_url="postgresql+psycopg://atlas@db/atlas",
        search_backend="opensearch",
        auto_create_schema=False,
        enable_hardware_fixture_extension=False,
        cors_origins=(
            "https://atlas.example.com,https://admin.atlas.example.com,"
            "https://tauri.localhost,tauri://localhost"
        ),
    )
    production_app = create_app(
        repository,
        search_backend=LexicalSearchBackend([]),
        settings=settings,
    )
    assert production_app.state.domain_registry.spaces
    assert repository.list_spaces()
    assert repository.list_taxonomy_nodes()
    assert repository.list_entities() == []
    assert all(
        route.path != "/api/v1/extensions/hardware/compatibility-checks"
        for route in production_app.routes
    )


def test_production_rejects_fixture_content_seeding() -> None:
    settings = Settings(
        environment="production",
        auth_mode="oidc",
        seed_fixture_content=True,
    )
    try:
        create_app(isolated_repository(), settings=settings)
    except ValueError as error:
        assert str(error) == "fixture content seeding is forbidden in production"
    else:
        raise AssertionError("production fixture seeding must be rejected")


def test_production_rejects_insecure_or_local_only_runtime_settings() -> None:
    baseline = {
        "environment": "production",
        "auth_mode": "oidc",
        "seed_fixture_content": False,
        "database_url": "postgresql+psycopg://atlas@db/atlas",
        "search_backend": "opensearch",
        "auto_create_schema": False,
        "enable_hardware_fixture_extension": False,
        "cors_origins": "https://atlas.example.com",
    }
    invalid_cases = [
        ({"auth_mode": "development"}, "AUTH_MODE=oidc"),
        ({"database_url": "sqlite+pysqlite:///atlas.db"}, "PostgreSQL"),
        ({"search_backend": "memory"}, "SEARCH_BACKEND=opensearch"),
        ({"auto_create_schema": True}, "AUTO_CREATE_SCHEMA=false"),
        (
            {"enable_hardware_fixture_extension": True},
            "fixture-backed hardware extension",
        ),
        (
            {"cors_origins": "http://localhost:3000"},
            "CORS origins",
        ),
    ]
    for overrides, message in invalid_cases:
        settings = Settings(**{**baseline, **overrides})
        try:
            create_app(isolated_repository(), settings=settings)
        except ValueError as error:
            assert message in str(error)
        else:
            raise AssertionError(f"production setting must be rejected: {overrides}")


def test_model_gateway_secret_is_redacted_from_settings_representation() -> None:
    settings = Settings(model_gateway_api_key="do-not-log-this-secret")
    assert "do-not-log-this-secret" not in repr(settings)
    assert settings.model_gateway_api_key.get_secret_value() == "do-not-log-this-secret"


def test_model_gateway_build_without_url_is_noop() -> None:
    assert build_model_gateway(Settings()) is None


def test_model_gateway_requires_model_for_configured_url() -> None:
    try:
        build_model_gateway(
            Settings(
                model_gateway_url="https://model-gateway.atlas.internal",
            )
        )
    except ValueError as error:
        assert "model gateway model must not be empty" in str(error)
    else:
        raise AssertionError("model gateway URL requires an explicit model")


def test_agent_pack_paths_support_glob_input() -> None:
    local_client = api_client(
        repository=isolated_repository(),
        settings=Settings(agent_pack_paths="agent-packs/*"),
    )
    assert local_client.app.state.agent_registry.graphs


def test_agent_pack_paths_support_absolute_directory_input() -> None:
    local_client = api_client(
        repository=isolated_repository(),
        settings=Settings(agent_pack_paths=str(Path(__file__).resolve().parents[3] / "agent-packs" / "core")),
    )
    assert local_client.app.state.agent_registry.graphs


def test_agent_pack_path_misconfiguration_reports_resolved_path() -> None:
    try:
        create_app(
            isolated_repository(),
            settings=Settings(
                agent_pack_paths=f"{Path(__file__).resolve().parents[3] / 'agent-packs' / 'missing'}",
            ),
        )
    except ValueError as error:
        assert (
            "failed to resolve agent pack paths from" in str(error)
            and "does not exist" in str(error)
        )
    else:
        raise AssertionError("missing agent pack path must be rejected")


def test_agent_pack_path_empty_is_rejected() -> None:
    try:
        create_app(
            isolated_repository(),
            settings=Settings(agent_pack_paths=""),
        )
    except ValueError as error:
        assert (
            "failed to resolve agent pack paths from" in str(error)
            and "at least one agent pack path must be configured" in str(error)
        )
    else:
        raise AssertionError("empty agent pack path must be rejected")


def test_domain_pack_path_misconfiguration_reports_error(tmp_path: Path) -> None:
    (tmp_path / "not-a-domain-pack").mkdir()
    try:
        create_app(
            isolated_repository(),
            settings=Settings(
                domain_pack_paths=f"{tmp_path}/not-a-domain-pack",
            ),
        )
    except ValueError as error:
        assert (
            "failed to load domain packs from" in str(error)
            and "manifest" in str(error)
        )
    else:
        raise AssertionError("invalid domain pack path must be rejected")


def test_domain_pack_paths_support_absolute_directory_input() -> None:
    local_client = api_client(
        repository=isolated_repository(),
        settings=Settings(
            domain_pack_paths=str(
                Path(__file__).resolve().parents[3] / "domain-packs" / "core"
            ),
        ),
    )
    assert local_client.app.state.domain_registry.spaces


def test_parser_pack_path_misconfiguration_reports_context(tmp_path: Path) -> None:
    try:
        create_app(
            isolated_repository(),
            settings=Settings(
                parser_paths=f"{tmp_path}/missing/*.json",
            ),
        )
    except ValueError as error:
        assert (
            "failed to build parser registry from HARDATLAS_PARSER_PATHS="
            in str(error)
            and "no match" in str(error)
        )
    else:
        raise AssertionError("invalid parser path must be rejected")


def test_parser_pack_paths_supports_empty_glob_directory_input(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()
    try:
        create_app(
            isolated_repository(),
            settings=Settings(parser_paths=f"{tmp_path}/*-dir"),
        )
    except ValueError as error:
        assert (
            "failed to build parser registry from HARDATLAS_PARSER_PATHS=" in str(error)
            and "no match" in str(error)
        )
    else:
        raise AssertionError("glob to empty directory must be rejected")


def test_parser_pack_path_empty_is_rejected() -> None:
    try:
        create_app(
            isolated_repository(),
            settings=Settings(parser_paths=""),
        )
    except ValueError as error:
        assert "at least one extraction parser must be registered" in str(error)
    else:
        raise AssertionError("empty parser path must be rejected")


def test_parser_pack_path_supports_absolute_glob_input(tmp_path: Path) -> None:
    (tmp_path / "parser.json").write_text(
        (
            """
            {
                "id": "parser-absolute-json",
                "version": "1.0.0",
                "format": "json",
                "mediaTypes": ["application/json"],
                "entityTypeId": "entity-type-plant",
                "locale": "zh-CN",
                "recordsPath": "/items",
                "externalIdPath": "id",
                "labelPath": "name",
                "fieldMappings": []
            }
            """
        ).strip(),
        encoding="utf-8",
    )
    create_app(
        isolated_repository(),
        settings=Settings(parser_paths=f"{tmp_path}/*.json"),
    )


def test_parser_pack_paths_support_absolute_directory_input(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.json").write_text(
        """
        {
            "id": "parser-absolute-directory",
            "version": "1.0.0",
            "format": "json",
            "mediaTypes": ["application/json"],
            "entityTypeId": "entity-type-plant",
            "locale": "zh-CN",
            "recordsPath": "/items",
            "externalIdPath": "id",
            "labelPath": "name",
            "fieldMappings": []
        }
        """.strip(),
        encoding="utf-8",
    )
    create_app(
        isolated_repository(),
        settings=Settings(parser_paths=str(nested)),
    )


def test_parser_pack_paths_reject_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    try:
        create_app(
            isolated_repository(),
            settings=Settings(parser_paths=str(tmp_path / "empty")),
        )
    except ValueError as error:
        assert (
            "failed to build parser registry from HARDATLAS_PARSER_PATHS=" in str(error)
            and "no match" in str(error)
        )
    else:
        raise AssertionError("empty parser directory must be rejected")


def test_parser_pack_paths_support_glob_to_directory_input(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.json").write_text(
        (
            """
            {
                "id": "parser-api-directory-glob",
                "version": "1.0.0",
                "format": "json",
                "mediaTypes": ["application/json"],
                "entityTypeId": "entity-type-plant",
                "locale": "zh-CN",
                "recordsPath": "/items",
                "externalIdPath": "id",
                "labelPath": "name",
                "fieldMappings": []
            }
            """
        ).strip(),
        encoding="utf-8",
    )
    create_app(
        isolated_repository(),
        settings=Settings(parser_paths=f"{tmp_path}/*"),
    )


def test_spaces_and_taxonomy_are_dynamic_data() -> None:
    spaces = client.get("/api/v1/spaces").json()
    assert {item["slug"] for item in spaces} >= {"life", "engineering", "humanities"}
    life = client.get("/api/v1/taxonomy", params={"spaceId": "space-life"}).json()
    assert {item["slug"] for item in life} == {"animals", "plants"}


def test_domain_pack_paths_control_runtime_visibility() -> None:
    core_only = api_client(
        repository=isolated_repository(),
        settings=Settings(domain_pack_paths="domain-packs/core"),
    )
    assert all(item["slug"] != "astronomy" for item in core_only.get("/api/v1/spaces").json())

    with_astronomy = api_client(
        repository=isolated_repository(),
        settings=Settings(
            domain_pack_paths="domain-packs/core,domain-packs/astronomy",
        ),
    )
    assert any(
        item["slug"] == "astronomy" for item in with_astronomy.get("/api/v1/spaces").json()
    )


def test_entity_revision_history_has_stable_version_urls() -> None:
    current_response = client.get("/api/v1/entities/ginkgo")
    current = current_response.json()
    assert "max-age=60" in current_response.headers["cache-control"]
    assert (
        client.get(
            "/api/v1/entities/ginkgo",
            headers={"if-none-match": current_response.headers["etag"]},
        ).status_code
        == 304
    )
    revision_id = current["revision"]["revisionId"]
    history = client.get("/api/v1/entities/ginkgo/revisions")
    assert history.status_code == 200
    assert history.json()[0]["revision"]["revisionId"] == revision_id
    assert history.json()[0]["current"] is True
    pinned = client.get(f"/api/v1/entities/ginkgo/revisions/{revision_id}")
    assert pinned.status_code == 200
    assert pinned.json()["revision"] == current["revision"]
    assert "immutable" in pinned.headers["cache-control"]
    assert (
        client.get(
            f"/api/v1/entities/ginkgo/revisions/{revision_id}",
            headers={"if-none-match": pinned.headers["etag"]},
        ).status_code
        == 304
    )
    assert client.get("/api/v1/entities/ginkgo/revisions/revision-missing").status_code == 404


def test_personal_collections_are_workspace_isolated_and_version_pinned() -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    workspace_a = "00000000-0000-0000-0000-000000000001"
    workspace_b = "00000000-0000-0000-0000-000000000002"
    headers_a = {
        "x-hardatlas-dev-principal": "collector-a",
        "x-hardatlas-dev-workspace-id": workspace_a,
    }
    headers_b = {
        "x-hardatlas-dev-principal": "collector-b",
        "x-hardatlas-dev-workspace-id": workspace_b,
    }

    created = local_client.post(
        "/api/v1/collections",
        headers=headers_a,
        json={"name": "高山生态", "description": "重点物种条目"},
    )
    assert created.status_code == 201
    collection_id = created.json()["id"]
    assert created.json()["workspaceId"] == workspace_a
    assert (
        local_client.get(
            "/api/v1/collections",
            headers=headers_b,
        ).json()
        == []
    )
    current = local_client.get("/api/v1/entities/snow-leopard").json()
    current_revision_id = current["revision"]["revisionId"]

    saved = local_client.post(
        f"/api/v1/collections/{collection_id}/items",
        headers=headers_a,
        json={
            "entityId": "entity-snow-leopard",
            "entityRevisionId": current_revision_id,
            "note": "跟踪引用变更",
            "tags": ["动物", "动物", "高山"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["entityRevisionId"] == current_revision_id
    assert saved.json()["dataVersion"] == "atlas-2026.07.29"
    assert saved.json()["tags"] == ["动物", "高山"]
    assert (
        local_client.post(
            f"/api/v1/collections/{collection_id}/items",
            headers=headers_a,
            json={
                "entityId": "entity-snow-leopard",
                "entityRevisionId": "revision-missing",
            },
        ).status_code
        == 404
    )
    assert (
        local_client.get(
            f"/api/v1/collections/{collection_id}/items",
            headers=headers_b,
        ).status_code
        == 404
    )
    removed = local_client.delete(
        f"/api/v1/collections/{collection_id}/items/entity-snow-leopard",
        headers=headers_a,
    )
    assert removed.json() == {"removed": True}
    assert local_client.delete(
        f"/api/v1/collections/{collection_id}",
        headers=headers_a,
    ).json() == {"removed": True}


def test_domain_pack_can_extend_the_encyclopedia_without_code_changes() -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    pack = {
        "id": "astronomy",
        "name": "天文学扩展包",
        "version": "1.0.0",
        "kernelVersion": "2.0",
        "locales": ["zh-CN", "en"],
        "citationIds": ["citation-astronomy-ontology"],
        "spaces": [
            {
                "id": "space-astronomy",
                "slug": "astronomy",
                "name": [{"locale": "zh-CN", "value": "天文与空间"}],
                "description": [{"locale": "zh-CN", "value": "天体、观测与空间科学"}],
                "rootTaxonomyNodeIds": ["tax-celestial-objects"],
                "iconKey": "orbit",
                "status": "published",
            }
        ],
        "taxonomyNodes": [
            {
                "id": "tax-celestial-objects",
                "spaceId": "space-astronomy",
                "slug": "celestial-objects",
                "name": [{"locale": "zh-CN", "value": "天体"}],
                "parentIds": [],
                "childCount": 0,
                "entityCount": 0,
                "pathKeys": ["astronomy", "celestial-objects"],
            }
        ],
        "attributes": [
            {
                "id": "attr-discovery-year",
                "key": "discovery-year",
                "name": [{"locale": "zh-CN", "value": "发现年份"}],
                "dataType": "integer",
                "cardinality": "one",
                "required": False,
                "schemaVersion": "astronomy-1.0.0",
            }
        ],
        "relationshipTypes": [
            {
                "id": "rel-observed-with-component",
                "key": "observed-with-component",
                "name": [{"locale": "zh-CN", "value": "使用组件观测"}],
                "inverseName": [{"locale": "zh-CN", "value": "用于观测"}],
                "directed": True,
                "sourceEntityTypeIds": ["type-celestial-object"],
                "targetEntityTypeIds": ["type-electronic-component"],
                "sourceCardinality": "many",
                "targetCardinality": "many",
                "qualifierSchema": {"instrument-role": {"type": "string"}},
                "evidenceRequired": True,
                "schemaVersion": "astronomy-1.0.0",
            }
        ],
        "views": [
            {
                "id": "view-celestial-object",
                "entityTypeId": "type-celestial-object",
                "schemaVersion": "astronomy-1.0.0",
                "blocks": [
                    {"id": "hero", "type": "hero", "config": {}},
                    {"id": "facts", "type": "attribute-table", "config": {}},
                    {"id": "sources", "type": "citations", "config": {}},
                ],
            }
        ],
        "entityTypes": [
            {
                "id": "type-celestial-object",
                "spaceId": "space-astronomy",
                "key": "celestial-object",
                "name": [{"locale": "zh-CN", "value": "天体"}],
                "description": [{"locale": "zh-CN", "value": "可独立描述的天文对象"}],
                "allowedTaxonomyNodeIds": ["tax-celestial-objects"],
                "attributeDefinitionIds": ["attr-discovery-year"],
                "allowedRelationshipTypeIds": ["rel-observed-with-component"],
                "defaultViewDefinitionId": "view-celestial-object",
                "schemaVersion": "astronomy-1.0.0",
            }
        ],
    }

    validation = local_client.post("/api/v1/domain-packs/validate", json=pack)
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert validation.json()["counts"]["entityTypes"] == 1
    assert validation.json()["counts"]["relationshipTypes"] == 1

    draft = local_client.post("/api/v1/domain-packs", json=pack)
    assert draft.status_code == 201
    assert draft.json()["status"] == "draft"
    proposal_id = draft.json()["proposalId"]
    assert local_client.get("/api/v1/domain-packs").json()[0]["id"] == "astronomy"

    blocked = local_client.post("/api/v1/domain-packs/astronomy/1.0.0/publish")
    assert blocked.status_code == 409
    evaluated = local_client.post(f"/api/v1/proposals/{proposal_id}/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.json()["proposal"]["status"] == "human-review"
    first_review = local_client.post(
        f"/api/v1/proposals/{proposal_id}/reviews",
        headers={"x-hardatlas-dev-principal": "domain-reviewer-alice"},
        json={"decision": "approve", "comment": "本体引用和命名检查通过"},
    )
    assert first_review.json()["proposal"]["status"] == "human-review"
    second_review = local_client.post(
        f"/api/v1/proposals/{proposal_id}/reviews",
        headers={"x-hardatlas-dev-principal": "domain-reviewer-bob"},
        json={"decision": "approve", "comment": "分类与渲染回归检查通过"},
    )
    assert second_review.json()["proposal"]["status"] == "accepted"

    published = local_client.post("/api/v1/domain-packs/astronomy/1.0.0/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["publishedAt"]
    assert published.json()["rollbackSnapshot"]
    assert any(
        item["id"] == "space-astronomy" for item in local_client.get("/api/v1/spaces").json()
    )
    assert any(
        item["id"] == "type-celestial-object"
        for item in local_client.get("/api/v1/entity-types").json()
    )
    published_relation = next(
        item
        for item in local_client.get("/api/v1/schema-registry").json()["relationshipTypes"]
        if item["id"] == "rel-observed-with-component"
    )
    assert published_relation["sourceEntityTypeIds"] == ["type-celestial-object"]
    assert published_relation["targetEntityTypeIds"] == ["type-electronic-component"]
    assert repository.pending_outbox_ids(topic="domain-pack.published") == [
        "outbox-domain-pack-astronomy-1.0.0"
    ]
    assert (
        local_client.get(f"/api/v1/proposals/{proposal_id}").json()["proposal"]["status"]
        == "released"
    )
    diff = local_client.get("/api/v1/domain-packs/astronomy/1.0.0/diff")
    assert diff.status_code == 200
    assert diff.json()["fromVersion"] is None
    assert diff.json()["counts"] == {
        "added": 6,
        "removed": 0,
        "modified": 0,
        "unchanged": 0,
    }
    rollback_analysis = local_client.get("/api/v1/domain-packs/astronomy/1.0.0/rollback-analysis")
    assert rollback_analysis.status_code == 200
    assert rollback_analysis.json()["safe"] is True
    repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-test-comet",
                    "slug": "test-comet",
                    "typeId": "type-celestial-object",
                    "canonicalName": "测试彗星",
                },
                "names": [{"locale": "zh-CN", "value": "测试彗星"}],
                "aliases": [],
                "description": [
                    {
                        "locale": "zh-CN",
                        "value": "用于验证 Domain Pack 依赖阻断的测试实体。",
                    }
                ],
                "taxonomyNodeIds": ["tax-celestial-objects"],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-test-comet-001",
                    "dataVersion": "atlas-domain-pack-dependency-test",
                    "schemaVersion": "astronomy-1.0.0",
                    "policyVersion": "policy-2.0",
                },
                "publicationStatus": "published",
            }
        )
    )
    blocked_analysis = local_client.get("/api/v1/domain-packs/astronomy/1.0.0/rollback-analysis")
    assert blocked_analysis.status_code == 200
    assert blocked_analysis.json()["safe"] is False
    assert {blocker["code"] for blocker in blocked_analysis.json()["blockers"]} >= {
        "entity-type-in-use",
        "taxonomy-node-in-use",
    }
    blocked_rollback = local_client.post(
        "/api/v1/domain-packs/astronomy/1.0.0/rollback",
        json={
            "expectedPublishedAt": published.json()["publishedAt"],
            "comment": "存在已发布实体时必须阻断",
        },
    )
    assert blocked_rollback.status_code == 409
    assert blocked_rollback.json()["detail"]["code"] == ("domain-pack-rollback-blocked")
    repository.deactivate_entities(["entity-test-comet"])
    assert (
        local_client.get("/api/v1/domain-packs/astronomy/1.0.0/rollback-analysis").json()["safe"]
        is True
    )
    rolled_back = local_client.post(
        "/api/v1/domain-packs/astronomy/1.0.0/rollback",
        json={
            "expectedPublishedAt": published.json()["publishedAt"],
            "comment": "验证初始领域包可按发布前快照精确回滚",
        },
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["rolledBack"]["status"] == "rolled-back"
    assert rolled_back.json()["restored"] is None
    assert all(
        item["id"] != "space-astronomy" for item in local_client.get("/api/v1/spaces").json()
    )
    assert all(
        item["id"] != "type-celestial-object"
        for item in local_client.get("/api/v1/entity-types").json()
    )
    assert repository.pending_outbox_ids(topic="domain-pack.rolled-back") == [
        "outbox-domain-pack-rollback-astronomy-1.0.0"
    ]


def test_source_registry_exposes_machine_enforced_acquisition_policy() -> None:
    local_client = api_client()
    created = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-flora",
            "version": "1.0.0",
            "name": "Flora dataset",
            "kind": "dataset",
            "baseUrl": "https://data.example.org/flora.json",
            "allowedHosts": ["data.example.org"],
            "trustTier": "authoritative",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "parserId": "parser-animals-json",
            "parserVersion": "1.0.0",
            "maxBytes": 2000000,
            "locales": ["zh-CN", "en"],
            "schedule": "0 4 * * 1",
            "status": "active",
        },
    )
    assert created.status_code == 200
    assert created.json()["policy"]["allowed"] is True
    source_id = created.json()["source"]["id"]
    detail = local_client.get(f"/api/v1/sources/{source_id}")
    assert detail.status_code == 200
    assert detail.json()["source"]["licenseId"] == "CC-BY-4.0"
    assert detail.json()["source"]["parserId"] == "parser-animals-json"
    assert local_client.get(f"/api/v1/sources/{source_id}/snapshots").json() == []
    assert local_client.get(f"/api/v1/sources/{source_id}/extractions").json() == []
    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acquisition-api-001",
            "idempotencyKey": "acquisition-api-idempotency-001",
        },
    )
    assert acquisition.status_code == 200
    assert acquisition.json()["status"] == "queued"
    assert acquisition.json()["sourceVersion"] == "1.0.0"
    jobs = local_client.get(f"/api/v1/sources/{source_id}/acquisitions")
    assert [item["id"] for item in jobs.json()] == ["acquisition-api-001"]
    events = local_client.get("/api/v1/jobs/acquisition-api-001/events")
    assert events.status_code == 200
    assert "event: queued" in events.text
    assert '"jobType":"source-acquisition"' in events.text
    assert "evidence-verified" not in events.text
    source_repository = local_client.app.state.repository
    acquisition_job = source_repository.get_source_acquisition_job("acquisition-api-001")
    assert acquisition_job
    source_repository.mark_outbox_published(
        source_repository.pending_outbox_ids(topic="source.acquisition.requested")
    )
    dispatched_job = acquisition_job.model_copy(
        update={
            "status": "dispatched",
            "updated_at": datetime.now(UTC),
        }
    )
    source_repository.save_source_acquisition_job(dispatched_job)
    source_repository.save_source_acquisition_job(
        dispatched_job.model_copy(
            update={
                "status": "failed",
                "error": "ConnectionError: source temporarily unavailable",
                "updated_at": datetime.now(UTC),
            }
        )
    )
    pipeline = local_client.get(f"/api/v1/sources/{source_id}/pipeline")
    assert pipeline.status_code == 200
    assert pipeline.json()["stalledCount"] == 1
    assert pipeline.json()["items"][0]["replayable"] is True
    replay = local_client.post("/api/v1/source-acquisitions/acquisition-api-001/replay")
    assert replay.status_code == 200
    assert replay.json()["stage"] == "acquisition"
    assert source_repository.pending_outbox_ids(topic="source.acquisition.requested")
    assert local_client.get("/api/v1/jobs/job-missing/events").status_code == 404
    missing_batch = local_client.get("/api/v1/extractions/extraction-missing/candidates")
    assert missing_batch.status_code == 404

    review_required = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-unknown-license",
            "version": "1.0.0",
            "name": "Unknown license",
            "kind": "website",
            "baseUrl": "https://unknown.example.org",
            "allowedHosts": ["unknown.example.org"],
            "trustTier": "secondary",
            "licenseId": "unknown",
            "licenseStatus": "review-required",
            "robotsPolicy": "respect",
            "robotsStatus": "review-required",
            "allowedMediaTypes": ["text/html"],
            "status": "paused",
        },
    )
    assert review_required.status_code == 200
    assert review_required.json()["policy"]["allowed"] is False
    assert len(local_client.get("/api/v1/sources").json()) == 2
    parsers = local_client.get("/api/v1/source-parsers")
    assert parsers.status_code == 200
    assert {item["id"] for item in parsers.json()} >= {
        "parser-animals-json",
        "parser-animals-html",
    }

    invalid_parser = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-invalid-parser",
            "version": "1.0.0",
            "name": "Invalid parser reference",
            "kind": "api",
            "baseUrl": "https://invalid.example.org/data",
            "allowedHosts": ["invalid.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "explicit-api",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "parserId": "parser-missing",
            "parserVersion": "1.0.0",
            "status": "active",
        },
    )
    assert invalid_parser.status_code == 422


def test_source_registration_rejects_unsafe_base_urls() -> None:
    local_client = api_client()
    base_payload = {
        "version": "1.0.0",
        "name": "Unsafe URL source",
        "kind": "dataset",
        "trustTier": "secondary",
        "licenseId": "CC-BY-4.0",
        "licenseStatus": "allowed",
        "robotsPolicy": "not-applicable",
        "robotsStatus": "allowed",
        "allowedMediaTypes": ["application/json"],
        "status": "active",
    }
    invalid_cases = [
        ("https://user:secret@knowledge.example.org/species", "credentials", "knowledge.example.org"),
        ("https://knowledge.example.org/species?search=true", "query", "knowledge.example.org"),
        ("https://knowledge.example.org/species#section", "fragment", "knowledge.example.org"),
        ("http://127.0.0.1/species", "host is blocked", "127.0.0.1"),
    ]
    for index, (base_url, reason, allowed_host) in enumerate(invalid_cases):
        response = local_client.post(
            "/api/v1/sources",
            json={
                **base_payload,
                "id": f"source-unsafe-{index}",
                "baseUrl": base_url,
                "allowedHosts": [allowed_host],
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert isinstance(detail, list)
        assert any(reason in issue["msg"] for issue in detail)


def test_source_acquisition_rejects_unsafe_override_url() -> None:
    local_client = api_client()
    registered = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-test",
            "version": "1.0.0",
            "name": "Acquisition test source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/species.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert registered.status_code == 200
    source_id = registered.json()["source"]["id"]
    invalid_cases = [
        ("https://user:secret@knowledge.example.org/species?download=1#part", "credentials"),
        ("https://knowledge.example.org/species?download=true", "query"),
        ("https://knowledge.example.org/species#section", "fragment"),
        ("http://127.0.0.1/species", "host is not allow-listed"),
    ]
    for index, (target_url, reason) in enumerate(invalid_cases):
        response = local_client.post(
            f"/api/v1/sources/{source_id}/acquisitions",
            json={
                "id": f"acq-unsafe-{index}",
                "idempotencyKey": f"acq-unsafe-key-{index:03d}",
                "url": target_url,
            },
        )
        assert response.status_code == 422, response.text
        assert reason in response.json()["detail"]


def test_source_acquisition_with_override_url_is_recorded_for_dispatch() -> None:
    local_client = api_client()
    registered = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-dispatch",
            "version": "1.0.0",
            "name": "Dispatch source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/species.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert registered.status_code == 200
    source_id = registered.json()["source"]["id"]
    override_url = "https://knowledge.example.org/species/override.json"
    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-override-dispatch",
            "idempotencyKey": "acq-override-dispatch-key",
            "url": override_url,
        },
    )
    assert acquisition.status_code == 200
    job_id = acquisition.json()["id"]
    pending = local_client.app.state.repository.pending_outbox_records(
        topic="source.acquisition.requested"
    )
    event = next(item for item in pending if item.aggregate_id == job_id)
    assert event.payload["jobId"] == job_id
    assert event.payload["url"] == override_url


def test_source_acquisition_replay_preserves_override_url_in_outbox_payload() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay",
            "version": "1.0.0",
            "name": "Replay source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]
    override_url = "https://knowledge.example.org/records/override.json"
    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-url",
            "idempotencyKey": "acq-replay-url-key",
            "url": override_url,
        },
    )
    assert acquisition.status_code == 200
    payload = acquisition.json()

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job(payload["id"])
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={"status": "dispatched", "updated_at": datetime.now(UTC)}
        )
    )
    failed_job = repository.get_source_acquisition_job(payload["id"])
    assert failed_job
    failed_job = failed_job.model_copy(
        update={
            "status": "failed",
            "error": "temporary network issue",
            "updated_at": datetime.now(UTC),
            },
    )
    repository.save_source_acquisition_job(failed_job)

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-url/replay")
    assert replay.status_code == 200
    assert replay.json()["outboxEventId"]
    pending = repository.pending_outbox_records(topic="source.acquisition.requested")
    event = next(item for item in pending if item.aggregate_id == "acq-replay-url")
    assert event.payload["url"] == override_url


def test_source_acquisition_replay_preserves_default_url_in_outbox_payload() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-default",
            "version": "1.0.0",
            "name": "Replay default source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]

    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-default",
            "idempotencyKey": "acq-replay-default-key",
        },
    )
    assert acquisition.status_code == 200

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job("acq-replay-default")
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={"status": "dispatched", "updated_at": datetime.now(UTC)}
        )
    )
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "failed",
                "error": "temporary network issue",
                "updated_at": datetime.now(UTC),
            }
        )
    )

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-default/replay")
    assert replay.status_code == 200
    pending = repository.pending_outbox_records(topic="source.acquisition.requested")
    event = next(item for item in pending if item.aggregate_id == "acq-replay-default")
    assert event.payload["url"] is None


def test_source_acquisition_replay_records_audit_event() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-audit",
            "version": "1.0.0",
            "name": "Replay audit source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]
    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-audit",
            "idempotencyKey": "acq-replay-audit-key",
        },
    )
    assert acquisition.status_code == 200
    payload = acquisition.json()

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job(payload["id"])
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "dispatched",
                "updated_at": datetime.now(UTC),
            },
        )
    )
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "failed",
                "error": "temporary network issue",
                "updated_at": datetime.now(UTC),
            },
        )
    )

    audit_before = repository.audit_event_count()
    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-audit/replay")
    assert replay.status_code == 200

    audit_events = local_client.get("/api/v1/audit-events").json()
    assert len(audit_events) == audit_before + 1
    assert any(
        event["action"] == "source.acquisition.replay"
        and event["resourceId"] == "acq-replay-audit"
        and event["outcome"] == "success"
        for event in audit_events
    )
    assert repository.verify_audit_chain()
    events = local_client.get("/api/v1/jobs/acq-replay-audit/events")
    assert events.status_code == 200
    assert "event: failed" in events.text
    assert '"jobType":"source-acquisition"' in events.text


def test_source_acquisition_replay_is_forbidden_when_job_is_not_failed() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-denied",
            "version": "1.0.0",
            "name": "Replay denied source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]
    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-denied",
            "idempotencyKey": "acq-replay-denied-key",
        },
    )
    assert acquisition.status_code == 200

    replay = local_client.post(
        "/api/v1/source-acquisitions/acq-replay-denied/replay"
    )
    assert replay.status_code == 409
    assert replay.json()["detail"] == "only failed acquisitions can be replayed"


def test_source_acquisition_replay_is_rejected_when_job_is_missing() -> None:
    local_client = api_client()
    replay = local_client.post(
        "/api/v1/source-acquisitions/acq-replay-missing/replay"
    )
    assert replay.status_code == 404
    assert replay.json()["detail"] == "source acquisition not found"


def test_source_acquisition_replay_preserves_maintenance_work_item_id() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-maint-item",
            "version": "1.0.0",
            "name": "Replay with maintenance item source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]

    repository = local_client.app.state.repository
    repository.create_source_acquisition_job(
        SourceAcquisitionJob(
            id="acq-replay-maint-item",
            source_id=source_id,
            source_version="1.0.0",
            status="queued",
            requested_by="api-test-user",
            idempotency_key="acq-replay-maint-item-key",
            maintenance_work_item_id="work-item-replay-maint",
        )
    )
    job = repository.get_source_acquisition_job("acq-replay-maint-item")
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "dispatched",
                "updated_at": datetime.now(UTC),
            }
        )
    )
    job = repository.get_source_acquisition_job("acq-replay-maint-item")
    assert job is not None
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "failed",
                "error": "temporary network issue",
                "updated_at": datetime.now(UTC),
            }
        )
    )

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-maint-item/replay")
    assert replay.status_code == 200
    pending = repository.pending_outbox_records(topic="source.acquisition.requested")
    event = next(
        item for item in pending if item.aggregate_id == "acq-replay-maint-item"
    )
    assert event.payload["maintenanceWorkItemId"] == "work-item-replay-maint"


def test_source_acquisition_replay_is_rejected_when_job_is_canceled() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-canceled",
            "version": "1.0.0",
            "name": "Replay canceled source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]

    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-canceled",
            "idempotencyKey": "acq-replay-canceled-key",
        },
    )
    assert acquisition.status_code == 200

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job("acq-replay-canceled")
    assert job is not None
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "canceled",
                "error": "operator canceled",
                "updated_at": datetime.now(UTC),
            },
        )
    )

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-canceled/replay")
    assert replay.status_code == 409
    assert replay.json()["detail"] == "only failed acquisitions can be replayed"


def test_source_acquisition_replay_is_rejected_when_job_is_completed() -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-completed",
            "version": "1.0.0",
            "name": "Replay completed source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]

    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-completed",
            "idempotencyKey": "acq-replay-completed-key",
        },
    )
    assert acquisition.status_code == 200

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job("acq-replay-completed")
    assert job is not None
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "dispatched",
                "updated_at": datetime.now(UTC),
            },
        )
    )
    job = repository.get_source_acquisition_job("acq-replay-completed")
    assert job is not None
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "running",
                "updated_at": datetime.now(UTC),
            },
        )
    )
    job = repository.get_source_acquisition_job("acq-replay-completed")
    assert job is not None
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "completed",
                "snapshot_id": "snapshot-already",
                "updated_at": datetime.now(UTC),
            },
        )
    )

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-completed/replay")
    assert replay.status_code == 409
    assert replay.json()["detail"] == "only failed acquisitions can be replayed"


def test_source_acquisition_replay_can_be_driven_to_completion_by_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-worker",
            "version": "1.0.0",
            "name": "Replay worker source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]

    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-worker",
            "idempotencyKey": "acq-replay-worker-key",
        },
    )
    assert acquisition.status_code == 200

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job("acq-replay-worker")
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "dispatched",
                "updated_at": datetime.now(UTC),
            },
        )
    )
    job = repository.get_source_acquisition_job("acq-replay-worker")
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "failed",
                "error": "temporary network issue",
                "updated_at": datetime.now(UTC),
            },
        )
    )
    assert repository.get_source_acquisition_job("acq-replay-worker")

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-worker/replay")
    assert replay.status_code == 200
    assert replay.json()["accepted"] is True
    assert replay.json()["stage"] == "acquisition"
    assert replay.json()["resourceId"] == "acq-replay-worker"
    replay_event_id = replay.json()["outboxEventId"]
    assert replay_event_id in repository.pending_outbox_ids(
        topic="source.acquisition.requested"
    )
    outbox_ids = repository.pending_outbox_ids(topic="source.acquisition.requested")
    assert len(outbox_ids) == 1
    assert outbox_ids
    replay_event = next(
        item
        for item in repository.pending_outbox_records(
            topic="source.acquisition.requested",
            aggregate_id="acq-replay-worker",
        )
        if item.id == replay_event_id
    )
    assert replay_event.payload["maintenanceWorkItemId"] is None

    content = b'{"records":[{"id":"worker"}]}'
    digest = hashlib.sha256(content).hexdigest()
    snapshot = SourceSnapshot(
        id="snapshot-replay-worker",
        source_id="source-acquisition-replay-worker",
        source_version="1.0.0",
        url="https://knowledge.example.org/records.json",
        content_sha256=digest,
        storage_key="sources/replay/worker",
        media_type="application/json",
        byte_size=len(content),
        http_status=200,
        license_id="CC-BY-4.0",
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 15, 0, tzinfo=UTC),
    )

    def fake_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        assert selected_source.id == "source-acquisition-replay-worker"
        return SimpleNamespace(snapshot=snapshot, content=content)

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        fake_acquire,
    )

    class WorkerStore:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put(self, *, key: str, **_: object) -> None:
            self.keys.append(key)

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected read: {key}")

    store = WorkerStore()
    assert dispatch_source_acquisition_events(
        repository,
        lambda job_id: execute_source_acquisition_once(
            repository,
            store,
            job_id,
        ),
    ) == {"dispatched": 1, "failed": 0}
    assert repository.get_source_snapshot("snapshot-replay-worker") == snapshot
    completed = repository.get_source_acquisition_job("acq-replay-worker")
    assert completed
    assert completed.status == "completed"
    assert completed.snapshot_id == snapshot.id
    assert repository.pending_outbox_ids(topic="source.acquisition.requested") == []

    audit_events = local_client.get("/api/v1/audit-events").json()
    assert any(
        event["action"] == "source.acquisition.replay"
        and event["resourceId"] == "acq-replay-worker"
        and event["outcome"] == "success"
        for event in audit_events
    )
    assert any(
        event["action"] == "source.acquisition.execute"
        and event["resourceId"] == "acq-replay-worker"
        and event["outcome"] == "success"
        for event in audit_events
    )
    assert repository.verify_audit_chain()


def test_source_acquisition_replay_can_fail_and_succeed_on_second_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_client = api_client()
    source = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-acquisition-replay-worker-retry",
            "version": "1.0.0",
            "name": "Replay worker retry source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source"]["id"]

    acquisition = local_client.post(
        f"/api/v1/sources/{source_id}/acquisitions",
        json={
            "id": "acq-replay-worker-retry",
            "idempotencyKey": "acq-replay-worker-retry-key",
        },
    )
    assert acquisition.status_code == 200

    repository = local_client.app.state.repository
    job = repository.get_source_acquisition_job("acq-replay-worker-retry")
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "dispatched",
                "updated_at": datetime.now(UTC),
            },
        )
    )
    job = repository.get_source_acquisition_job("acq-replay-worker-retry")
    assert job
    repository.save_source_acquisition_job(
        job.model_copy(
            update={
                "status": "failed",
                "error": "temporary network issue",
                "updated_at": datetime.now(UTC),
            },
        )
    )

    def failing_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> None:
        raise AcquisitionError("transient network failure")

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        failing_acquire,
    )

    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-worker-retry/replay")
    assert replay.status_code == 200
    first_replay_event = replay.json()["outboxEventId"]
    replay_event = next(
        item
        for item in repository.pending_outbox_records(
            topic="source.acquisition.requested",
            aggregate_id="acq-replay-worker-retry",
        )
        if item.id == first_replay_event
    )
    assert replay_event.payload["maintenanceWorkItemId"] is None
    first_pending = repository.pending_outbox_ids(topic="source.acquisition.requested")
    assert len(first_pending) == 1
    assert first_replay_event in first_pending
    first_dispatch = dispatch_source_acquisition_events(
        repository,
        lambda job_id: execute_source_acquisition_once(
            repository,
            FailureStore(),
            job_id,
        ),
    )
    assert first_dispatch == {"dispatched": 0, "failed": 1}
    assert len(repository.pending_outbox_ids(topic="source.acquisition.requested")) == 1
    failed_job = repository.get_source_acquisition_job("acq-replay-worker-retry")
    assert failed_job
    assert failed_job.status == "failed"

    def success_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            snapshot=SourceSnapshot(
                id="snapshot-replay-worker-retry",
                source_id="source-acquisition-replay-worker-retry",
                source_version="1.0.0",
                url="https://knowledge.example.org/records.json",
                content_sha256=hashlib.sha256(b"ok").hexdigest(),
                storage_key="sources/replay/worker/retry",
                media_type="application/json",
                byte_size=2,
                http_status=200,
                license_id="CC-BY-4.0",
                capture_status="captured",
                retrieved_at=datetime(2026, 7, 29, 15, 5, tzinfo=UTC),
            ),
            content=b"ok",
        )

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        success_acquire,
    )
    replay = local_client.post("/api/v1/source-acquisitions/acq-replay-worker-retry/replay")
    assert replay.status_code == 200
    second_replay_event = replay.json()["outboxEventId"]
    assert second_replay_event == first_replay_event
    second_replay_record = next(
        item
        for item in repository.pending_outbox_records(
            topic="source.acquisition.requested",
            aggregate_id="acq-replay-worker-retry",
        )
        if item.id == second_replay_event
    )
    assert second_replay_record.payload["maintenanceWorkItemId"] is None
    second_pending = repository.pending_outbox_ids(topic="source.acquisition.requested")
    assert len(second_pending) == 1
    assert second_replay_event in second_pending
    second_dispatch = dispatch_source_acquisition_events(
        repository,
        lambda job_id: execute_source_acquisition_once(
            repository,
            SuccessStore(),
            job_id,
        ),
    )
    assert second_dispatch == {"dispatched": 1, "failed": 0}
    completed = repository.get_source_acquisition_job("acq-replay-worker-retry")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.snapshot_id == "snapshot-replay-worker-retry"
    assert repository.pending_outbox_ids(topic="source.acquisition.requested") == []

    audit_events = local_client.get("/api/v1/audit-events").json()
    assert sum(
        1
        for event in audit_events
        if event["action"] == "source.acquisition.replay"
        and event["resourceId"] == "acq-replay-worker-retry"
    ) == 2
    assert any(
        event["action"] == "source.acquisition.execute"
        and event["resourceId"] == "acq-replay-worker-retry"
        and event["outcome"] == "failed"
        for event in audit_events
    )
    assert any(
        event["action"] == "source.acquisition.execute"
        and event["resourceId"] == "acq-replay-worker-retry"
        and event["outcome"] == "success"
        for event in audit_events
    )
    assert repository.verify_audit_chain()


class FailureStore:
    def put(self, *, key: str, **_: object) -> None:
        raise AssertionError(f"unexpected put: {key}")

    def get(self, *, key: str) -> bytes:
        raise AssertionError(f"unexpected read: {key}")


class SuccessStore:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def put(self, *, key: str, **_: object) -> None:
        self.keys.append(key)

    def get(self, *, key: str) -> bytes:
        raise AssertionError(f"unexpected read: {key}")


def test_three_different_domains_share_one_entity_contract() -> None:
    cases = [
        ("snow-leopard", "type-animal"),
        ("ginkgo", "type-plant"),
        ("ne555", "type-electronic-component"),
    ]
    for slug, expected_type in cases:
        response = client.get(f"/api/v1/entities/{slug}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ref"]["typeId"] == expected_type
        assert payload["revision"]["schemaVersion"] == "schema-2.0.0"
        assert payload["citations"]


def test_relationship_registry_and_bidirectional_traversal() -> None:
    local_client = api_client()
    repository = local_client.app.state.repository
    ne555 = repository.get_entity("ne555")
    assert ne555
    lm555 = ne555.model_copy(
        deep=True,
        update={
            "ref": EntityRef(
                id="entity-lm555",
                slug="lm555",
                type_id="type-electronic-component",
                canonical_name="LM555 定时器",
            ),
            "relationships": [],
            "revision": RevisionContext(
                revision_id="rev-lm555-relation-test",
                data_version="relation-test",
                schema_version=ne555.revision.schema_version,
                policy_version=ne555.revision.policy_version,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            ),
        },
    )
    repository.save_entity(lm555)
    revised_ne555 = ne555.model_copy(
        deep=True,
        update={
            "relationships": [
                Relationship(
                    id="relation-ne555-lm555",
                    type_id="rel-variant-of",
                    source=ne555.ref,
                    target=lm555.ref,
                    qualifiers={"manufacturer": "fixture"},
                    confidence=0.98,
                    citation_ids=[ne555.citations[0].id],
                    revision_id="rev-ne555-relation-test",
                )
            ],
            "revision": RevisionContext(
                revision_id="rev-ne555-relation-test",
                data_version="relation-test",
                schema_version=ne555.revision.schema_version,
                policy_version=ne555.revision.policy_version,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            ),
        },
    )
    repository.save_entity(revised_ne555)

    registry = local_client.get("/api/v1/relationship-types")
    assert registry.status_code == 200
    assert {item["id"] for item in registry.json()} >= {
        "rel-distributed-in",
        "rel-variant-of",
    }
    outgoing = local_client.get(
        "/api/v1/entities/ne555/relationships",
        params={"direction": "outgoing"},
    )
    assert outgoing.status_code == 200
    assert outgoing.json()[0]["neighbor"]["slug"] == "lm555"
    assert outgoing.json()[0]["direction"] == "outgoing"
    incoming = local_client.get(
        "/api/v1/entities/lm555/relationships",
        params={"direction": "incoming"},
    )
    assert incoming.status_code == 200
    assert incoming.json()[0]["neighbor"]["slug"] == "ne555"
    assert incoming.json()[0]["relationshipType"]["inverseName"][0]["value"] == "具有变体"

    replacement = revised_ne555.model_copy(
        deep=True,
        update={
            "relationships": [
                revised_ne555.relationships[0].model_copy(
                    update={
                        "id": "relation-ne555-lm555-replaced",
                        "revision_id": "rev-ne555-relation-replaced",
                    }
                )
            ],
            "revision": RevisionContext(
                revision_id="rev-ne555-relation-replaced",
                data_version="relation-test-replaced",
                schema_version=ne555.revision.schema_version,
                policy_version=ne555.revision.policy_version,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            ),
        },
    )
    repository.save_entity(replacement)
    replaced = local_client.get(
        "/api/v1/entities/ne555/relationships",
        params={"direction": "outgoing"},
    )
    assert replaced.status_code == 200
    assert replaced.json()[0]["relationship"]["id"] == ("relation-ne555-lm555-replaced")


def test_multilingual_alias_search_returns_evidence_backed_entity() -> None:
    response = client.get("/api/v1/search", params={"q": "Panthera uncia"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["entity"]["ref"]["slug"] == "snow-leopard"
    assert payload["items"][0]["matchedField"] == "alias"
    status = client.get("/api/v1/search/status").json()
    assert status == {
        "backend": "memory-lexical",
        "ready": True,
        "indexAlias": None,
    }


def test_public_question_answer_is_cited_persisted_and_replayable() -> None:
    response = client.post(
        "/api/v1/answers",
        json={
            "question": "雪豹生活在什么环境？",
            "locale": "zh-CN",
            "mode": "auto",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["mode"] == "retrieval-synthesis"
    assert payload["entities"][0]["slug"] == "snow-leopard"
    assert payload["evidence"][0]["revisionId"].startswith("rev-snow-leopard")
    assert payload["citations"][0]["id"] == "cite-snow-leopard-001"
    assert payload["modelProvenance"] is None

    replay = client.get(f"/api/v1/answers/{payload['id']}")
    assert replay.status_code == 200
    assert replay.json() == payload
    assert client.get("/api/v1/answers/answer-missing").status_code == 404


def test_public_question_answer_fails_closed_when_evidence_is_missing() -> None:
    response = client.post(
        "/api/v1/answers",
        json={
            "question": "量子引力的完整理论是什么？",
            "locale": "zh-CN",
            "mode": "auto",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "insufficient-evidence"
    assert response.json()["evidence"] == []
    assert response.json()["citations"] == []


def test_public_answer_model_uses_only_the_configured_server_proxy() -> None:
    requests: list[httpx.Request] = []

    def answer_proxy(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "answer-proxy-request-1"},
            json={
                "id": "answer-proxy-request-1",
                "provider": "approved-proxy",
                "model": "atlas-answer-alias",
                "output_json": {
                    "answer": "雪豹适应寒冷、干燥和高海拔环境。[1]",
                    "evidenceIds": [
                        ("evidence:entity-snow-leopard:section:section-snow-leopard-overview")
                    ],
                    "confidence": 0.96,
                },
                "usage": {"input_tokens": 90, "output_tokens": 18},
            },
        )

    gateway = OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url="https://model-gateway.atlas.internal",
            model="atlas-answer-alias",
            gateway_id="atlas-approved-gateway",
        ),
        transport=httpx.MockTransport(answer_proxy),
    )
    local_client = api_client(
        model_gateway=gateway,
        settings=Settings(public_answer_model_enabled=True),
    )
    response = local_client.post(
        "/api/v1/answers",
        json={
            "question": "雪豹生活在什么环境？",
            "locale": "zh-CN",
            "mode": "auto",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["mode"] == "model-proxy"
    assert payload["modelProvenance"] == {
        "gatewayId": "atlas-approved-gateway",
        "model": "atlas-answer-alias",
        "requestId": "answer-proxy-request-1",
        "status": "succeeded",
        "errorCode": None,
        "inputTokens": 90,
        "outputTokens": 18,
    }
    assert len(requests) == 1
    assert str(requests[0].url) == ("https://model-gateway.atlas.internal/v1/responses")
    assert requests[0].headers["x-hardatlas-agent-id"] == ("public-answer-synthesizer")
    assert "model-gateway.atlas.internal" not in response.text


def test_discovery_search_supports_browse_filters_facets_and_pagination() -> None:
    response = client.get(
        "/api/v1/search",
        params={
            "q": "",
            "spaceId": "space-life",
            "taxonomyNodeId": "tax-animals",
            "locale": "zh-CN",
            "displayLocale": "en",
            "sourceTier": "authoritative",
            "minimumSources": 1,
            "limit": 1,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == ""
    assert payload["total"] == 1
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert payload["items"][0]["entity"]["ref"]["slug"] == "snow-leopard"
    assert payload["items"][0]["matchedField"] == "browse"
    assert payload["facets"]["spaces"] == [
        {"value": "space-life", "label": "Life Sciences", "count": 1}
    ]
    assert payload["facets"]["sourceTiers"] == [
        {"value": "authoritative", "label": "权威来源", "count": 1}
    ]


def test_view_definition_uses_allow_listed_blocks() -> None:
    payload = client.get("/api/v1/view-definitions/view-animal").json()
    block_types = {block["type"] for block in payload["blocks"]}
    assert {"hero", "classification", "map", "citations"} <= block_types


def test_schema_registry_drives_draft_validation() -> None:
    registry = client.get("/api/v1/schema-registry")
    assert registry.status_code == 200
    payload = registry.json()
    assert {item["id"] for item in payload["entityTypes"]} >= {
        "type-animal",
        "type-plant",
        "type-electronic-component",
    }
    assert {item["id"] for item in payload["attributeDefinitions"]} >= {
        "attr-scientific-name",
        "attr-supply-voltage",
    }

    invalid = client.post(
        "/api/v1/entity-drafts/validate",
        json={
            "id": "entity-red-panda",
            "slug": "Red Panda",
            "typeId": "type-animal",
            "names": [{"locale": "zh-CN", "value": "小熊猫"}],
            "description": [],
            "taxonomyNodeIds": ["tax-missing"],
            "attributeValues": {},
            "citations": [],
        },
    )
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert {issue["code"] for issue in invalid.json()["issues"]} >= {
        "invalid-slug",
        "unknown-taxonomy",
        "missing-evidence",
        "required",
    }
    wrong_space = {
        "id": "entity-red-panda",
        "slug": "red-panda",
        "typeId": "type-animal",
        "names": [{"locale": "zh-CN", "value": "小熊猫"}],
        "description": [{"locale": "zh-CN", "value": "测试"}],
        "taxonomyNodeIds": ["tax-electronics"],
        "attributeValues": {"attr-scientific-name": "Ailurus fulgens"},
        "citations": [
            {
                "id": "cite-red-panda",
                "sourceId": "source-test",
                "sourceTitle": "fixture",
                "sourceTier": "authoritative",
                "retrievedAt": "2026-07-29T12:00:00Z",
            }
        ],
    }
    mismatch = client.post("/api/v1/entity-drafts/validate", json=wrong_space)
    assert mismatch.status_code == 200
    assert "taxonomy-space-mismatch" in {issue["code"] for issue in mismatch.json()["issues"]}


def test_schema_change_analysis_classifies_impact_before_migration() -> None:
    local_client = api_client()
    current = next(
        item
        for item in local_client.get("/api/v1/schema-registry").json()["attributeDefinitions"]
        if item["id"] == "attr-scientific-name"
    )
    proposed = {
        **current,
        "dataType": "decimal",
        "schemaVersion": "schema-3.0.0",
    }
    response = local_client.post(
        "/api/v1/schema-changes/analyze",
        json={
            "schemaKind": "attribute-definition",
            "schemaId": "attr-scientific-name",
            "proposedDocument": proposed,
        },
    )
    assert response.status_code == 200
    analysis = response.json()
    assert analysis["classification"] == "breaking"
    assert analysis["requiresMigration"] is True
    assert analysis["reversible"] is False
    assert set(analysis["affectedEntityIds"]) >= {
        "entity-snow-leopard",
        "entity-ginkgo",
    }
    assert analysis["blockers"]

    same_version = local_client.post(
        "/api/v1/schema-changes/analyze",
        json={
            "schemaKind": "attribute-definition",
            "schemaId": "attr-scientific-name",
            "proposedDocument": current,
        },
    )
    assert same_version.status_code == 422


def test_schema_migration_requires_two_reviews_applies_atomically_and_rolls_back() -> None:
    local_client = api_client()
    current = next(
        item
        for item in local_client.get("/api/v1/schema-registry").json()["attributeDefinitions"]
        if item["id"] == "attr-conservation"
    )
    proposed = {
        **current,
        "enumValues": [value for value in current["enumValues"] if value != "EN"],
        "schemaVersion": "schema-2.0.1-migration-test",
    }
    before_entity = local_client.get("/api/v1/entities/snow-leopard").json()
    before_revision = before_entity["revision"]["revisionId"]
    planned = local_client.post(
        "/api/v1/schema-migrations",
        json={
            "id": "migration-conservation-en-cr",
            "proposalId": "proposal-migration-conservation-en-cr",
            "schemaKind": "attribute-definition",
            "schemaId": "attr-conservation",
            "proposedDocument": proposed,
            "operations": [
                {
                    "operation": "map-enum",
                    "attributeId": "attr-conservation",
                    "mapping": {"EN": "CR"},
                }
            ],
            "citationIds": ["citation-schema-policy"],
            "confidence": 0.99,
            "dataVersion": "atlas-schema-migration-test",
        },
    )
    assert planned.status_code == 200
    manifest = planned.json()
    assert manifest["status"] == "planned"
    assert manifest["analysis"]["classification"] == "migratory"
    assert manifest["frozenRevisionIds"] == {"entity-snow-leopard": before_revision}

    blocked = local_client.post("/api/v1/schema-migrations/migration-conservation-en-cr/apply")
    assert blocked.status_code == 409

    evaluated = local_client.post(
        "/api/v1/proposals/proposal-migration-conservation-en-cr/evaluate"
    )
    assert evaluated.json()["policyEvaluation"]["requiredApprovals"] == 2
    first = local_client.post(
        "/api/v1/proposals/proposal-migration-conservation-en-cr/reviews",
        headers={"x-hardatlas-dev-principal": "schema-reviewer-alice"},
        json={"decision": "approve", "comment": "conversion reviewed"},
    )
    assert first.json()["proposal"]["status"] == "human-review"
    second = local_client.post(
        "/api/v1/proposals/proposal-migration-conservation-en-cr/reviews",
        headers={"x-hardatlas-dev-principal": "schema-reviewer-bob"},
        json={"decision": "approve", "comment": "rollback reviewed"},
    )
    assert second.json()["proposal"]["status"] == "accepted"

    applied = local_client.post("/api/v1/schema-migrations/migration-conservation-en-cr/apply")
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert applied.json()["appliedRevisionIds"]["entity-snow-leopard"] != (before_revision)
    active_schema = local_client.get("/api/v1/attribute-definitions/attr-conservation").json()
    assert active_schema["schemaVersion"] == "schema-2.0.1-migration-test"
    migrated_entity = local_client.get("/api/v1/entities/snow-leopard").json()
    migrated_claim = next(
        claim
        for claim in migrated_entity["claims"]
        if claim["attributeDefinitionId"] == "attr-conservation"
    )
    assert migrated_claim["originalValue"] == "CR"

    rolled_back = local_client.post(
        "/api/v1/schema-migrations/migration-conservation-en-cr/rollback"
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["status"] == "rolled-back"
    restored_schema = local_client.get("/api/v1/attribute-definitions/attr-conservation").json()
    assert restored_schema["schemaVersion"] == current["schemaVersion"]
    restored_entity = local_client.get("/api/v1/entities/snow-leopard").json()
    assert restored_entity["revision"]["revisionId"] == before_revision
    restored_claim = next(
        claim
        for claim in restored_entity["claims"]
        if claim["attributeDefinitionId"] == "attr-conservation"
    )
    assert restored_claim["originalValue"] == "EN"


def test_new_entity_is_governed_published_searchable_and_rollback_safe() -> None:
    local_client = api_client()
    draft = {
        "id": "entity-red-panda",
        "slug": "red-panda",
        "typeId": "type-animal",
        "names": [{"locale": "zh-CN", "value": "小熊猫"}],
        "aliases": [{"locale": "en", "value": "Red panda"}],
        "description": [{"locale": "zh-CN", "value": "一种生活在亚洲山地森林的哺乳动物。"}],
        "taxonomyNodeIds": ["tax-animals"],
        "attributeValues": {
            "attr-scientific-name": "Ailurus fulgens",
            "attr-conservation": "EN",
        },
        "sections": [
            {
                "key": "overview",
                "heading": [{"locale": "zh-CN", "value": "概述"}],
                "body": [{"locale": "zh-CN", "value": "这是受治理的新条目测试夹具。"}],
                "citationIds": ["cite-red-panda-001"],
            }
        ],
        "citations": [
            {
                "id": "cite-red-panda-001",
                "sourceId": "source-red-panda-test",
                "sourceTitle": "Authoritative species fixture",
                "sourceTier": "authoritative",
                "retrievedAt": "2026-07-29T12:00:00Z",
            }
        ],
    }
    proposed = local_client.post(
        "/api/v1/entity-drafts/proposals",
        json={
            "proposalId": "proposal-create-red-panda",
            "agentRunId": "run-create-red-panda",
            "confidence": 0.98,
            "risk": "low",
            "draft": draft,
        },
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["proposal"]["operations"][0]["path"] == "/entity"
    assert local_client.get("/api/v1/entities/red-panda").status_code == 404

    evaluated = local_client.post("/api/v1/proposals/proposal-create-red-panda/evaluate")
    assert evaluated.json()["proposal"]["status"] == "policy-approved"
    accepted = local_client.post("/api/v1/proposals/proposal-create-red-panda/accept-policy")
    assert accepted.json()["proposal"]["status"] == "accepted"
    staged = local_client.post(
        "/api/v1/releases",
        json={
            "id": "release-create-red-panda",
            "proposalIds": ["proposal-create-red-panda"],
            "dataVersion": "atlas-create-red-panda",
            "schemaVersions": ["schema-2.0.1"],
            "previousReleaseId": "atlas-2026.07.28",
        },
    )
    assert staged.status_code == 200
    published = local_client.post("/api/v1/releases/release-create-red-panda/publish")
    assert published.status_code == 200
    assert published.json()["createdEntityIds"] == ["entity-red-panda"]
    entity = local_client.get("/api/v1/entities/red-panda")
    assert entity.status_code == 200
    assert entity.json()["publicationStatus"] == "published"
    search = local_client.get("/api/v1/search", params={"q": "Ailurus fulgens"})
    assert search.json()["items"][0]["entity"]["ref"]["slug"] == "red-panda"

    rolled_back = local_client.post("/api/v1/releases/release-create-red-panda/rollback")
    assert rolled_back.status_code == 200
    assert local_client.get("/api/v1/entities/red-panda").status_code == 404
    assert local_client.get("/api/v1/search", params={"q": "Ailurus fulgens"}).json()["total"] == 0


def test_existing_entity_authoring_draft_creates_optimistic_revision() -> None:
    local_client = api_client()
    context = local_client.get("/api/v1/entities/entity-ginkgo/authoring-draft")
    assert context.status_code == 200
    payload = context.json()
    draft = payload["draft"]
    base_revision_id = payload["baseRevisionId"]
    assert draft["id"] == "entity-ginkgo"
    assert payload["entityType"]["id"] == "type-plant"
    assert payload["viewDefinition"]["id"] == "view-plant"

    draft["description"][0]["value"] = "银杏是具有受治理修订历史的现存植物。"
    draft["citations"].append(
        {
            "id": "cite-ginkgo-editorial-001",
            "sourceId": "source-ginkgo-editorial",
            "sourceTitle": "Editorial botany reference",
            "sourceTier": "authoritative",
            "retrievedAt": "2026-07-29T15:00:00Z",
        }
    )
    draft["sections"][0]["body"][0]["value"] = "该修订由通用条目工作室提交。"
    draft["sections"][0]["citationIds"] = ["cite-ginkgo-editorial-001"]
    request = {
        "proposalId": "proposal-revise-ginkgo-editorial",
        "agentRunId": "human-authoring-ginkgo-editorial",
        "confidence": 0.98,
        "risk": "low",
        "baseRevisionId": base_revision_id,
        "draft": draft,
    }
    stale = local_client.post(
        "/api/v1/entities/entity-ginkgo/draft-proposals",
        json={**request, "baseRevisionId": "rev-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "base entity revision is stale"

    proposed = local_client.post(
        "/api/v1/entities/entity-ginkgo/draft-proposals",
        json=request,
    )
    assert proposed.status_code == 200, proposed.text
    operation_paths = {item["path"] for item in proposed.json()["proposal"]["operations"]}
    assert {
        "/description",
        "/sections",
        "/citations",
    } <= operation_paths
    before = local_client.get("/api/v1/entities/ginkgo").json()
    assert before["revision"]["revisionId"] == base_revision_id

    evaluated = local_client.post("/api/v1/proposals/proposal-revise-ginkgo-editorial/evaluate")
    assert evaluated.json()["proposal"]["status"] == "policy-approved"
    accepted = local_client.post("/api/v1/proposals/proposal-revise-ginkgo-editorial/accept-policy")
    assert accepted.json()["proposal"]["status"] == "accepted"
    staged = local_client.post(
        "/api/v1/releases",
        json={
            "id": "release-revise-ginkgo-editorial",
            "proposalIds": ["proposal-revise-ginkgo-editorial"],
            "dataVersion": "atlas-revise-ginkgo-editorial",
            "schemaVersions": ["schema-2.2.0"],
            "previousReleaseId": "atlas-2026.07.28",
        },
    )
    assert staged.status_code == 200
    published = local_client.post("/api/v1/releases/release-revise-ginkgo-editorial/publish")
    assert published.status_code == 200
    revised = local_client.get("/api/v1/entities/ginkgo").json()
    assert revised["revision"]["revisionId"] != base_revision_id
    assert revised["description"][0]["value"] == ("银杏是具有受治理修订历史的现存植物。")
    assert revised["sections"][0]["body"][0]["value"] == ("该修订由通用条目工作室提交。")
    assert "cite-ginkgo-editorial-001" in {item["id"] for item in revised["citations"]}

    rolled_back = local_client.post("/api/v1/releases/release-revise-ginkgo-editorial/rollback")
    assert rolled_back.status_code == 200
    assert (
        local_client.get("/api/v1/entities/ginkgo").json()["revision"]["revisionId"]
        == base_revision_id
    )


def test_private_authoring_draft_is_resumable_versioned_and_workspace_isolated() -> None:
    local_client = api_client()
    workspace_a = "00000000-0000-0000-0000-0000000000a1"
    workspace_b = "00000000-0000-0000-0000-0000000000b2"
    headers_a = {"x-hardatlas-dev-workspace-id": workspace_a}
    headers_b = {"x-hardatlas-dev-workspace-id": workspace_b}
    context = local_client.get(
        "/api/v1/entities/entity-ginkgo/authoring-draft",
        headers=headers_a,
    ).json()
    draft = context["draft"]
    created = local_client.post(
        "/api/v1/authoring-drafts",
        headers=headers_a,
        json={
            "id": "draft-ginkgo-workspace-a",
            "mode": "revise",
            "draft": draft,
            "baseRevisionId": context["baseRevisionId"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["workspaceId"] == workspace_a
    assert (
        local_client.get(
            "/api/v1/authoring-drafts/draft-ginkgo-workspace-a",
            headers=headers_b,
        ).status_code
        == 404
    )
    assert (
        local_client.get(
            "/api/v1/authoring-drafts",
            headers=headers_b,
        ).json()
        == []
    )

    draft["description"][0]["value"] = "跨会话恢复的银杏修订草稿。"
    updated = local_client.put(
        "/api/v1/authoring-drafts/draft-ginkgo-workspace-a",
        headers=headers_a,
        json={"expectedVersion": 1, "draft": draft},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["draft"]["description"][0]["value"] == "跨会话恢复的银杏修订草稿。"
    stale = local_client.put(
        "/api/v1/authoring-drafts/draft-ginkgo-workspace-a",
        headers=headers_a,
        json={"expectedVersion": 1, "draft": draft},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "authoring draft version conflict"

    submitted = local_client.post(
        "/api/v1/authoring-drafts/draft-ginkgo-workspace-a/submit",
        headers=headers_a,
        json={
            "expectedVersion": 2,
            "proposalId": "proposal-ginkgo-workspace-draft",
            "agentRunId": "human-workspace-draft-test",
            "confidence": 0.98,
            "risk": "medium",
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["draft"]["status"] == "submitted"
    assert submitted.json()["draft"]["version"] == 3
    assert submitted.json()["proposal"]["proposal"]["id"] == "proposal-ginkgo-workspace-draft"
    repeated = local_client.post(
        "/api/v1/authoring-drafts/draft-ginkgo-workspace-a/submit",
        headers=headers_a,
        json={
            "expectedVersion": 2,
            "proposalId": "proposal-ginkgo-workspace-draft",
            "agentRunId": "human-workspace-draft-test",
            "confidence": 0.98,
            "risk": "medium",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["draft"]["version"] == 3
    blocked_update = local_client.put(
        "/api/v1/authoring-drafts/draft-ginkgo-workspace-a",
        headers=headers_a,
        json={"expectedVersion": 3, "draft": draft},
    )
    assert blocked_update.status_code == 409
    assert blocked_update.json()["detail"] == ("only editing drafts can be updated")


def test_authoring_draft_validates_and_proposes_domain_pack_relationships() -> None:
    local_client = api_client()
    repository = local_client.app.state.repository
    ne555 = repository.get_entity("ne555")
    assert ne555
    lm555 = ne555.model_copy(
        deep=True,
        update={
            "ref": EntityRef(
                id="entity-lm555-authoring",
                slug="lm555-authoring",
                type_id="type-electronic-component",
                canonical_name="LM555 定时器",
            ),
            "relationships": [],
            "revision": RevisionContext(
                revision_id="rev-lm555-authoring",
                data_version="relationship-authoring",
                schema_version=ne555.revision.schema_version,
                policy_version=ne555.revision.policy_version,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            ),
        },
    )
    repository.save_entity(lm555)
    context = local_client.get("/api/v1/entities/entity-ne555/authoring-draft").json()
    draft = context["draft"]
    draft["relationships"] = [
        {
            "id": "relation-ne555-lm555-authoring",
            "typeId": "rel-variant-of",
            "source": ne555.ref.model_dump(mode="json", by_alias=True),
            "target": lm555.ref.model_dump(mode="json", by_alias=True),
            "qualifiers": {"manufacturer": "fixture"},
            "confidence": 0.98,
            "citationIds": [draft["citations"][0]["id"]],
            "revisionId": context["baseRevisionId"],
        }
    ]
    validation = local_client.post(
        "/api/v1/entity-drafts/validate",
        json=draft,
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True

    created = local_client.post(
        "/api/v1/authoring-drafts",
        json={
            "id": "draft-ne555-relationship-authoring",
            "mode": "revise",
            "draft": draft,
            "baseRevisionId": context["baseRevisionId"],
        },
    )
    assert created.status_code == 201, created.text
    submitted = local_client.post(
        "/api/v1/authoring-drafts/draft-ne555-relationship-authoring/submit",
        json={
            "expectedVersion": 1,
            "proposalId": "proposal-ne555-relationship-authoring",
            "agentRunId": "human-relationship-authoring",
            "confidence": 0.98,
            "risk": "medium",
        },
    )
    assert submitted.status_code == 200, submitted.text
    operations = submitted.json()["proposal"]["proposal"]["operations"]
    relationship_operation = next(
        operation for operation in operations if operation["path"] == "/relationships"
    )
    assert relationship_operation["after"][0]["target"]["id"] == ("entity-lm555-authoring")
    assert local_client.get("/api/v1/entities/ne555").json()["relationships"] == []

    invalid = draft | {
        "relationships": [
            draft["relationships"][0]
            | {"target": draft["relationships"][0]["target"] | {"canonicalName": "伪造名称"}}
        ]
    }
    invalid_validation = local_client.post(
        "/api/v1/entity-drafts/validate",
        json=invalid,
    ).json()
    assert invalid_validation["valid"] is False
    assert "relationship-target-reference-mismatch" in {
        issue["code"] for issue in invalid_validation["issues"]
    }


def test_agent_proposals_are_versioned_and_do_not_publish_directly() -> None:
    runs = client.get("/api/v1/agent-runs").json()
    proposals = client.get("/api/v1/proposals").json()
    assert runs[0]["policyVersion"] == "policy-1.0.0"
    assert proposals[0]["proposal"]["status"] == "human-review"
    assert proposals[0]["proposal"]["operations"][0]["citationIds"]


def test_api_refreshes_proposals_persisted_by_external_agent_worker() -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    proposal = AgentProposal(
        id="proposal-external-worker-refresh",
        entity_id="entity-ginkgo",
        proposal_type="content",
        operations=[
            ChangeOperation(
                operation="replace",
                path="/description",
                before="旧描述",
                after="外部 Worker 候选",
                citation_ids=["cite-ginkgo-001"],
                confidence=0.98,
            )
        ],
        risk="medium",
        status="proposed",
        agent_run_id="run-external-worker-refresh",
        impact={"entityCount": 1},
    )
    external_governance = GovernanceService([proposal], "policy-1.0.0")
    external = external_governance.list_proposals()[0]
    repository.save_governed_proposal(external)
    assert external.version == 1

    visible = local_client.get(
        "/api/v1/proposals/queue",
        params={"q": proposal.id},
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["items"][0]["governed"]["version"] == 1

    external_governance.proposals[proposal.id] = external
    external_governance.evaluate(proposal.id)
    repository.save_governed_proposal(external)
    assert external.version == 2

    refreshed = local_client.get(f"/api/v1/proposals/{proposal.id}")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["version"] == 2
    assert refreshed.json()["proposal"]["status"] == "human-review"


def test_proposal_queue_pages_filters_and_bulk_evaluates_with_full_preflight() -> None:
    local_client = api_client()
    governance = local_client.app.state.governance
    repository = local_client.app.state.repository

    def add_proposal(
        proposal_id: str,
        *,
        after: str,
        risk: str,
        path: str = "/description",
    ) -> None:
        governed = governance.add_proposal(
            AgentProposal(
                id=proposal_id,
                entity_id="entity-batch-eval-fixture",
                proposal_type="content",
                operations=[
                    ChangeOperation(
                        operation="replace",
                        path=path,
                        before="旧值",
                        after=after,
                        citation_ids=["cite-ginkgo-001"],
                        confidence=0.98,
                    )
                ],
                risk=risk,
                status="proposed",
                agent_run_id=f"run-{proposal_id}",
                impact={"entityCount": 1},
            )
        )
        repository.save_governed_proposal(governed)

    add_proposal(
        "proposal-batch-eval-a",
        after="冲突候选 A",
        risk="medium",
    )
    add_proposal(
        "proposal-batch-eval-b",
        after="冲突候选 B",
        risk="medium",
    )
    add_proposal(
        "proposal-batch-eval-c",
        after="独立低风险候选",
        risk="low",
        path="/aliases",
    )

    first_page = local_client.get(
        "/api/v1/proposals/queue",
        params={
            "q": "batch-eval",
            "status": "proposed",
            "limit": 1,
        },
    )
    assert first_page.status_code == 200, first_page.text
    queue = first_page.json()
    assert queue["total"] == 3
    assert queue["hasMore"] is True
    assert queue["items"][0]["governed"]["proposal"]["id"] in {
        "proposal-batch-eval-a",
        "proposal-batch-eval-b",
    }
    assert len(queue["items"][0]["conflictIds"]) == 1
    assert queue["totalConflicts"] >= 1
    assert queue["statusCounts"]["proposed"] >= 3

    conflicts_only = local_client.get(
        "/api/v1/proposals/queue",
        params={
            "q": "batch-eval",
            "conflictsOnly": "true",
        },
    ).json()
    assert conflicts_only["total"] == 2
    assert {item["governed"]["proposal"]["id"] for item in conflicts_only["items"]} == {
        "proposal-batch-eval-a",
        "proposal-batch-eval-b",
    }

    evaluated = local_client.post(
        "/api/v1/proposals/bulk-evaluations",
        json={
            "proposalIds": [
                "proposal-batch-eval-a",
                "proposal-batch-eval-c",
            ]
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    assert {
        item["proposal"]["id"]: item["proposal"]["status"] for item in evaluated.json()["proposals"]
    } == {
        "proposal-batch-eval-a": "human-review",
        "proposal-batch-eval-c": "policy-approved",
    }

    preflight_rejection = local_client.post(
        "/api/v1/proposals/bulk-evaluations",
        json={
            "proposalIds": [
                "proposal-batch-eval-b",
                "proposal-batch-eval-a",
            ]
        },
    )
    assert preflight_rejection.status_code == 409
    assert preflight_rejection.json()["detail"] == {
        "code": "proposal-not-awaiting-evaluation",
        "proposalId": "proposal-batch-eval-a",
        "status": "human-review",
    }
    assert governance.get_proposal("proposal-batch-eval-b").proposal.status == "proposed"
    duplicate = local_client.post(
        "/api/v1/proposals/bulk-evaluations",
        json={
            "proposalIds": [
                "proposal-batch-eval-b",
                "proposal-batch-eval-b",
            ]
        },
    )
    assert duplicate.status_code == 422


def test_conflicting_agent_proposals_block_approval_until_one_is_rejected() -> None:
    local_client = api_client()
    governance = local_client.app.state.governance
    repository = local_client.app.state.repository
    for proposal_id, after in (
        ("proposal-conflict-a", "候选描述 A"),
        ("proposal-conflict-b", "候选描述 B"),
    ):
        governed = governance.add_proposal(
            AgentProposal(
                id=proposal_id,
                entity_id="entity-ginkgo",
                proposal_type="content",
                operations=[
                    ChangeOperation(
                        operation="replace",
                        path="/description",
                        before="当前描述",
                        after=after,
                        citation_ids=["cite-ginkgo-001"],
                        confidence=0.98,
                    )
                ],
                risk="medium",
                status="proposed",
                agent_run_id=f"run-{proposal_id}",
                impact={"entityCount": 1},
            )
        )
        repository.save_governed_proposal(governed)
        evaluated = local_client.post(f"/api/v1/proposals/{proposal_id}/evaluate")
        assert evaluated.status_code == 200
        assert evaluated.json()["proposal"]["status"] == "human-review"

    conflicts = local_client.get("/api/v1/proposals/conflicts")
    assert conflicts.status_code == 200
    assert conflicts.json()[0]["proposalIds"] == [
        "proposal-conflict-a",
        "proposal-conflict-b",
    ]
    blocked = local_client.post(
        "/api/v1/proposals/proposal-conflict-a/reviews",
        json={"decision": "approve", "comment": "approve A"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "proposal-conflict"

    rejected = local_client.post(
        "/api/v1/proposals/bulk-reviews",
        json={
            "proposalIds": ["proposal-conflict-b"],
            "decision": "reject",
            "comment": "prefer proposal A",
        },
    )
    assert rejected.status_code == 200
    accepted = local_client.post(
        "/api/v1/proposals/bulk-reviews",
        json={
            "proposalIds": ["proposal-conflict-a"],
            "decision": "approve",
            "comment": "conflict resolved",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["proposals"][0]["proposal"]["status"] == "accepted"


def test_policy_approved_proposal_cannot_bypass_active_conflict() -> None:
    local_client = api_client()
    governance = local_client.app.state.governance
    repository = local_client.app.state.repository
    proposal_ids = [
        "proposal-low-risk-conflict-a",
        "proposal-low-risk-conflict-b",
    ]
    for proposal_id, after in zip(
        proposal_ids,
        ["低风险冲突 A", "低风险冲突 B"],
        strict=True,
    ):
        governed = governance.add_proposal(
            AgentProposal(
                id=proposal_id,
                entity_id="entity-ginkgo",
                proposal_type="content",
                operations=[
                    ChangeOperation(
                        operation="replace",
                        path="/description/0/value",
                        before="旧描述",
                        after=after,
                        citation_ids=["cite-ginkgo-001"],
                        confidence=0.98,
                    )
                ],
                risk="low",
                status="proposed",
                agent_run_id=f"run-{proposal_id}",
                impact={"entityCount": 1},
            )
        )
        repository.save_governed_proposal(governed)
        evaluated = local_client.post(f"/api/v1/proposals/{proposal_id}/evaluate")
        assert evaluated.status_code == 200
        assert evaluated.json()["proposal"]["status"] == "policy-approved"

    blocked = local_client.post(f"/api/v1/proposals/{proposal_ids[0]}/accept-policy")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "proposal-conflict"
    conflicts = local_client.get("/api/v1/proposals/conflicts").json()
    assert any(set(conflict["proposalIds"]) == set(proposal_ids) for conflict in conflicts)


def test_conflict_merge_supersedes_sources_and_reenters_policy_governance() -> None:
    local_client = api_client()
    governance = local_client.app.state.governance
    repository = local_client.app.state.repository
    for proposal_id, after in (
        ("proposal-merge-source-a", "合并候选描述 A"),
        ("proposal-merge-source-b", "合并候选描述 B"),
    ):
        governed = governance.add_proposal(
            AgentProposal(
                id=proposal_id,
                entity_id="entity-ginkgo",
                proposal_type="content",
                operations=[
                    ChangeOperation(
                        operation="replace",
                        path="/description",
                        before="当前描述",
                        after=after,
                        citation_ids=[f"cite-{proposal_id}"],
                        confidence=0.98,
                    )
                ],
                risk="medium",
                status="proposed",
                agent_run_id=f"run-{proposal_id}",
                impact={"entityCount": 1},
            )
        )
        repository.save_governed_proposal(governed)
        assert (
            local_client.post(f"/api/v1/proposals/{proposal_id}/evaluate").json()["proposal"][
                "status"
            ]
            == "human-review"
        )

    conflict = next(
        item
        for item in local_client.get("/api/v1/proposals/conflicts").json()
        if set(item["proposalIds"]) == {"proposal-merge-source-a", "proposal-merge-source-b"}
    )
    merged = local_client.post(
        f"/api/v1/proposals/conflicts/{conflict['id']}/merge",
        json={
            "proposalId": "proposal-merged-ginkgo-description",
            "agentRunId": "run-human-merge-ginkgo-description",
            "resolutions": [
                {
                    "path": "/description",
                    "proposalId": "proposal-merge-source-b",
                }
            ],
            "comment": "保留候选 B 的描述，同时保留两个来源提案的审计链",
        },
    )
    assert merged.status_code == 200, merged.text
    payload = merged.json()
    assert payload["proposal"]["proposal"]["status"] == "proposed"
    assert payload["proposal"]["sourceProposalIds"] == [
        "proposal-merge-source-a",
        "proposal-merge-source-b",
    ]
    assert payload["proposal"]["proposal"]["operations"][0]["after"] == ("合并候选描述 B")
    assert {item["proposal"]["status"] for item in payload["supersededProposals"]} == {"superseded"}
    assert all(
        item["supersededByProposalId"] == "proposal-merged-ginkgo-description"
        for item in payload["supersededProposals"]
    )
    assert (
        repository.get_governed_proposal("proposal-merge-source-a").proposal.status == "superseded"
    )
    assert all(
        conflict["id"] != item["id"]
        for item in local_client.get("/api/v1/proposals/conflicts").json()
    )

    evaluated = local_client.post("/api/v1/proposals/proposal-merged-ginkgo-description/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.json()["proposal"]["status"] == "human-review"
    assert evaluated.json()["policyEvaluation"]["requiredApprovals"] == 1
    audit_actions = {item["action"] for item in local_client.get("/api/v1/audit-events").json()}
    assert "proposal.conflict.merge" in audit_actions


def test_agent_governance_api_requires_policy_and_two_reviewers() -> None:
    local_client = api_client()
    before_release = local_client.get("/api/v1/entities/snow-leopard").json()
    before_revision = before_release["revision"]["revisionId"]
    evaluated = local_client.post("/api/v1/proposals/proposal-snow-leopard-status/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.json()["policyEvaluation"]["requiredApprovals"] == 2

    first = local_client.post(
        "/api/v1/proposals/proposal-snow-leopard-status/reviews",
        headers={"x-hardatlas-dev-principal": "alice"},
        json={"decision": "approve", "comment": "evidence verified"},
    )
    assert first.json()["proposal"]["status"] == "human-review"

    second = local_client.post(
        "/api/v1/proposals/proposal-snow-leopard-status/reviews",
        headers={"x-hardatlas-dev-principal": "bob"},
        json={"decision": "approve", "comment": "impact reviewed"},
    )
    assert second.json()["proposal"]["status"] == "accepted"

    staged = local_client.post(
        "/api/v1/releases",
        json={
            "id": "release-test-001",
            "proposalIds": ["proposal-snow-leopard-status"],
            "dataVersion": "atlas-test-001",
            "schemaVersions": ["schema-2.0.1"],
            "previousReleaseId": "atlas-2026.07.21",
        },
    )
    assert staged.status_code == 200
    published = local_client.post("/api/v1/releases/release-test-001/publish")
    assert published.json()["status"] == "published"
    assert published.json()["searchIndex"] == "memory:atlas-test-001"
    released_entity = local_client.get("/api/v1/entities/snow-leopard").json()
    conservation = next(
        claim
        for claim in released_entity["claims"]
        if claim["attributeDefinitionId"] == "attr-conservation"
    )
    assert conservation["originalValue"] == "VU"
    assert released_entity["revision"]["revisionId"] != before_revision
    pending_entity_events = local_client.app.state.repository.pending_outbox_records(
        topic="knowledge.entity.revised"
    )
    assert pending_entity_events
    assert all(
        event.payload.get("releaseId") != "release-test-001" for event in pending_entity_events
    )
    release_events = local_client.app.state.repository.pending_outbox_records(
        topic="release.published"
    )
    assert [event.aggregate_id for event in release_events] == ["release-test-001"]
    verified = local_client.post("/api/v1/releases/release-test-001/verify")
    assert verified.status_code == 200
    assert verified.json()["verification"]["status"] == "passed"
    assert {
        check["key"]
        for check in verified.json()["verification"]["checks"]
        if check["status"] == "passed"
    } == {
        "release-state",
        "search-readiness",
        "search-index",
        "entity-revisions",
        "citation-integrity",
        "relationship-integrity",
        "search-discoverability",
    }
    rolled_back = local_client.post("/api/v1/releases/release-test-001/rollback")
    assert rolled_back.json()["status"] == "rolled-back"
    restored_entity = local_client.get("/api/v1/entities/snow-leopard").json()
    restored_conservation = next(
        claim
        for claim in restored_entity["claims"]
        if claim["attributeDefinitionId"] == "attr-conservation"
    )
    assert restored_conservation["originalValue"] == "EN"
    assert restored_entity["revision"]["revisionId"] == before_revision


def test_release_activation_failure_remains_retryable_and_preserves_outbox(
    monkeypatch,
) -> None:
    local_client = api_client()
    local_client.post("/api/v1/proposals/proposal-snow-leopard-status/evaluate")
    for reviewer in ("activation-reviewer-a", "activation-reviewer-b"):
        approved = local_client.post(
            "/api/v1/proposals/proposal-snow-leopard-status/reviews",
            headers={"x-hardatlas-dev-principal": reviewer},
            json={"decision": "approve", "comment": "verified"},
        )
        assert approved.status_code == 200
    staged = local_client.post(
        "/api/v1/releases",
        json={
            "id": "release-activation-retry",
            "proposalIds": ["proposal-snow-leopard-status"],
            "dataVersion": "atlas-activation-retry",
            "schemaVersions": ["schema-2.0.1"],
            "previousReleaseId": "atlas-2026.07.28",
        },
    )
    assert staged.status_code == 200

    backend = local_client.app.state.search_backend
    original_activate = backend.activate
    attempts = 0

    def fail_first_publish_and_rollback_activation(index_name: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts in {1, 3}:
            raise ConnectionError("alias service unavailable")
        original_activate(index_name)

    monkeypatch.setattr(
        backend,
        "activate",
        fail_first_publish_and_rollback_activation,
    )
    failed = local_client.post("/api/v1/releases/release-activation-retry/publish")
    assert failed.status_code == 503
    assert "remains publishing" in failed.json()["detail"]
    releases = local_client.get("/api/v1/releases").json()
    persisted = next(item for item in releases if item["id"] == "release-activation-retry")
    assert persisted["status"] == "publishing"
    pending = local_client.app.state.repository.pending_outbox_records(
        topic="knowledge.entity.revised"
    )
    assert any(event.payload.get("releaseId") == "release-activation-retry" for event in pending)

    retried = local_client.post("/api/v1/releases/release-activation-retry/publish")
    assert retried.status_code == 200
    assert retried.json()["status"] == "published"
    assert attempts == 2
    remaining = local_client.app.state.repository.pending_outbox_records(
        topic="knowledge.entity.revised"
    )
    assert all(event.payload.get("releaseId") != "release-activation-retry" for event in remaining)

    failed_rollback = local_client.post("/api/v1/releases/release-activation-retry/rollback")
    assert failed_rollback.status_code == 503
    assert "remains rolling-back" in failed_rollback.json()["detail"]
    releases = local_client.get("/api/v1/releases").json()
    rolling_back = next(item for item in releases if item["id"] == "release-activation-retry")
    assert rolling_back["status"] == "rolling-back"
    rollback_pending = local_client.app.state.repository.pending_outbox_records(
        topic="knowledge.entity.revised"
    )
    assert any(
        event.payload.get("releaseId") == "release-activation-retry"
        and event.payload.get("rollback") is True
        for event in rollback_pending
    )

    rollback_retried = local_client.post("/api/v1/releases/release-activation-retry/rollback")
    assert rollback_retried.status_code == 200
    assert rollback_retried.json()["status"] == "rolled-back"
    assert attempts == 4


def test_governance_state_survives_app_recreation() -> None:
    repository = isolated_repository()
    first_client = api_client(repository)
    first_client.post("/api/v1/proposals/proposal-snow-leopard-status/evaluate")
    for reviewer_id in ("reviewer-a", "reviewer-b"):
        response = first_client.post(
            "/api/v1/proposals/proposal-snow-leopard-status/reviews",
            json={
                "decision": "approve",
                "comment": "verified",
            },
            headers={"x-hardatlas-dev-principal": reviewer_id},
        )
        assert response.status_code == 200

    restarted_client = api_client(repository)
    restored = restarted_client.get("/api/v1/proposals/proposal-snow-leopard-status").json()
    assert restored["proposal"]["status"] == "accepted"
    assert [review["reviewerId"] for review in restored["reviews"]] == [
        "reviewer-a",
        "reviewer-b",
    ]


def test_agent_graph_creates_a_governed_proposal_and_is_idempotent() -> None:
    local_client = api_client()
    definitions = local_client.get("/api/v1/agent-definitions")
    assert definitions.status_code == 200
    assert {item["id"] for item in definitions.json()} == {
        "agent-source-monitor",
        "agent-content-extractor",
        "agent-entity-resolver",
        "agent-evidence-verifier",
        "agent-quality-triage",
        "agent-source-acquisition-router",
        "agent-translation-maintainer",
        "agent-taxonomy-maintainer",
    }
    quality_graph = local_client.get("/api/v1/agent-graphs/quality-maintenance-triage").json()
    assert quality_graph["version"] == "graph-1.0.0"
    assert quality_graph["budget"]["maxModelCalls"] == 0
    graph_spec = local_client.get("/api/v1/agent-graphs/knowledge-maintenance").json()
    assert graph_spec["version"] == "graph-2.3.0"
    assert graph_spec["budget"]["maxModelCalls"] == 4
    assert graph_spec["nodes"][0]["agentId"] == "agent-source-monitor"
    request = {
        "runId": "run-graph-test",
        "sourceId": "source-test",
        "snapshotHash": "sha256:test",
        "entityId": "entity-snow-leopard",
        "label": "雪豹",
        "fieldPath": "/sections/0/body",
        "proposedValue": "新的、带来源的内容",
        "citationId": "cite-snow-leopard-001",
        "confidence": 0.97,
        "risk": "medium",
    }
    run = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/runs",
        json=request,
    )
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert run.json()["proposalIds"] == ["proposal-run-graph-test"]
    assert run.json()["budgetExhausted"] is False

    proposal = local_client.get("/api/v1/proposals/proposal-run-graph-test").json()
    assert proposal["proposal"]["status"] == "proposed"
    operation = proposal["proposal"]["operations"][0]
    assert operation["citationIds"] == ["cite-snow-leopard-001"]
    assert operation["before"][0]["value"] == (
        "雪豹适应寒冷、干燥和高海拔环境，是高山生态系统的重要指示物种。"
    )
    assert operation["machineGenerated"] is True

    repeated = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/runs",
        json=request,
    )
    assert repeated.status_code == 200
    assert repeated.json()["nodes"]["monitor"]["attempts"] == 1

    request["proposedValue"] = "同一个 runId 的不同输入"
    conflict = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/runs",
        json=request,
    )
    assert conflict.status_code == 409

    request["runId"] = "run-graph-unknown-citation"
    request["citationId"] = "cite-not-registered"
    unknown_citation = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/runs",
        json=request,
    )
    assert unknown_citation.status_code == 422
    assert "citation is not registered" in unknown_citation.json()["detail"]


def test_agent_graph_uses_configured_proxy_and_audits_model_provenance() -> None:
    calls: list[tuple[str, str | None]] = []

    def model_proxy(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                str(request.url),
                request.headers.get("x-hardatlas-agent-id"),
            )
        )
        agent_id = request.headers["x-hardatlas-agent-id"]
        if agent_id == "agent-content-extractor":
            output = {
                "proposedValue": "经代理模型抽取、仍需治理审核的内容。",
                "confidence": 0.98,
            }
        else:
            output = {
                "entityId": "entity-ginkgo",
                "normalizedLabel": "ginkgo biloba",
                "rationale": "候选标签匹配",
            }
        return httpx.Response(
            200,
            headers={"x-request-id": f"request-{len(calls)}"},
            json={
                "provider": "internal-router",
                "model": "maintainer-v2",
                "output_json": output,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    gateway = OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url="https://proxy.example.internal/models",
            model="maintainer",
            gateway_id="approved-proxy",
        ),
        transport=httpx.MockTransport(model_proxy),
    )
    local_client = api_client(model_gateway=gateway)
    registered = local_client.post(
        "/api/v1/sources",
        json={
            "id": "source-ginkgo",
            "version": "1.0.0",
            "name": "Approved Ginkgo source",
            "kind": "api",
            "baseUrl": "https://knowledge.example.org/ginkgo",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "authoritative",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "explicit-api",
            "robotsStatus": "allowed",
            "modelProcessingPolicy": "approved-gateway",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert registered.status_code == 200
    assert registered.json()["policy"]["modelProcessingAllowed"] is True
    response = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/runs",
        json={
            "runId": "run-proxy-integration",
            "sourceId": "source-ginkgo",
            "snapshotHash": "sha256:immutable",
            "entityId": "entity-ginkgo",
            "candidateEntityIds": [
                "entity-ginkgo",
                "entity-ginkgo-fossil",
            ],
            "label": "Ginkgo biloba",
            "fieldPath": "/sections/0/body",
            "proposedValue": "解析器候选",
            "sourceExcerpt": "银杏是银杏科、银杏属植物。",
            "citationId": "cite-ginkgo-001",
            "confidence": 0.9,
            "risk": "medium",
        },
    )

    assert response.status_code == 200
    run = response.json()
    assert run["usage"]["modelCalls"] == 2
    assert [item["agentId"] for item in run["modelInvocations"]] == [
        "agent-content-extractor",
        "agent-entity-resolver",
    ]
    assert all(item["gatewayId"] == "approved-proxy" for item in run["modelInvocations"])
    assert calls == [
        (
            "https://proxy.example.internal/models/v1/responses",
            "agent-content-extractor",
        ),
        (
            "https://proxy.example.internal/models/v1/responses",
            "agent-entity-resolver",
        ),
    ]
    proposal = local_client.get("/api/v1/proposals/proposal-run-proxy-integration").json()
    operation = proposal["proposal"]["operations"][0]
    assert operation["after"] == "经代理模型抽取、仍需治理审核的内容。"
    assert operation["confidence"] == 0.98
    assert operation["citationIds"] == ["cite-ginkgo-001"]
    assert operation["before"]
    assert operation["machineGenerated"] is True


def test_agent_graph_schedule_is_versioned_budgeted_and_idempotent() -> None:
    local_client = api_client()
    request = {
        "id": "schedule-maintenance-001",
        "triggerType": "source-change",
        "idempotencyKey": "source-snapshot-001",
        "input": {
            "sourceId": "source-test",
            "snapshotHash": "sha256:test",
            "entityId": "entity-ginkgo",
            "label": "银杏",
            "fieldPath": "/description/0/value",
            "proposedValue": "候选描述",
            "citationId": "cite-ginkgo-001",
            "confidence": 0.95,
            "risk": "low",
            "modelProcessingAllowed": True,
        },
    }
    created = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/schedules",
        json=request,
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["status"] == "queued"
    assert payload["graphVersion"] == "graph-2.3.0"
    assert payload["budget"]["maxModelCalls"] == 4
    assert payload["requestedBy"] == "api-test-user"
    assert payload["input"]["modelProcessingAllowed"] is False
    assert payload["input"]["currentRevisionId"]
    assert payload["input"]["currentValuePresent"] is True
    assert payload["input"]["currentValue"]
    assert payload["input"]["citationAlreadyPresent"] is True
    assert local_client.app.state.repository.pending_outbox_ids(topic="agent.graph.scheduled")
    stream = local_client.get(
        "/api/v1/jobs/schedule-maintenance-001/events",
    )
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: queued" in stream.text
    assert '"jobType":"agent-graph-schedule"' in stream.text
    schedule_repository = local_client.app.state.repository
    stored_schedule = schedule_repository.get_agent_graph_schedule("schedule-maintenance-001")
    assert stored_schedule
    schedule_repository.mark_outbox_published(
        schedule_repository.pending_outbox_ids(topic="agent.graph.scheduled")
    )
    dispatched_schedule = stored_schedule.model_copy(
        update={
            "status": "dispatched",
            "updated_at": datetime.now(UTC),
        }
    )
    schedule_repository.save_agent_graph_schedule(dispatched_schedule)
    schedule_repository.save_agent_graph_schedule(
        dispatched_schedule.model_copy(
            update={
                "status": "failed",
                "error": "RuntimeError: transient model proxy outage",
                "updated_at": datetime.now(UTC),
            }
        )
    )
    replay = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/schedules/schedule-maintenance-001/replay"
    )
    assert replay.status_code == 200
    assert replay.json()["stage"] == "agent-schedule"
    assert schedule_repository.pending_outbox_ids(topic="agent.graph.scheduled")

    repeated = {
        **request,
        "id": "schedule-maintenance-another-id",
    }
    same = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/schedules",
        json=repeated,
    )
    assert same.status_code == 200
    assert same.json()["id"] == "schedule-maintenance-001"

    repeated["input"] = {"sourceId": "different"}
    conflict = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/schedules",
        json=repeated,
    )
    assert conflict.status_code == 409

    missing_contract_fields = local_client.post(
        "/api/v1/agent-graphs/knowledge-maintenance/schedules",
        json={
            "id": "schedule-invalid-input",
            "triggerType": "manual",
            "idempotencyKey": "invalid-contract-input",
            "input": {"sourceId": "source-test"},
        },
    )
    assert missing_contract_fields.status_code == 422
    assert "input contract failed" in missing_contract_fields.json()["detail"]
    assert "snapshotHash" in missing_contract_fields.json()["detail"]


def test_operations_summary_is_derived_from_persisted_runtime_state() -> None:
    local_client = api_client()
    summary_response = local_client.get("/api/v1/operations/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    proposals = local_client.get("/api/v1/proposals").json()
    definitions = local_client.get("/api/v1/agent-definitions").json()

    assert summary["agentDefinitionCount"] == len(definitions)
    assert summary["proposalCount"] == len(proposals)
    assert summary["reviewQueueCount"] == sum(
        item["proposal"]["status"] in {"proposed", "human-review", "policy-blocked"}
        for item in proposals
    )
    assert 0 <= summary["evidenceCoveragePercent"] <= 100
    assert summary["dataVersion"] == "atlas-2026.07.29"
    assert summary["agentDefinitionCount"] != 42
    assert summary["proposalCount"] != 128
    assert {item["id"] for item in summary["proposals"]}.issubset(
        {item["proposal"]["id"] for item in proposals}
    )


def test_quality_scan_discovers_versioned_maintenance_work() -> None:
    local_client = api_client()
    registry = local_client.get("/api/v1/schema-registry").json()
    baseline_profiles = {
        "type-animal",
        "type-plant",
        "type-electronic-component",
    }
    profile_types = {profile["entityTypeId"] for profile in registry["qualityProfiles"]}
    assert baseline_profiles.issubset(profile_types)
    entities = local_client.get("/api/v1/entities").json()
    assessable_entities = [
        item
        for item in entities
        if item["ref"]["typeId"] in profile_types
        and item["publicationStatus"] == "published"
    ]

    scan = local_client.post(
        "/api/v1/quality/scans",
        json={"entityIds": []},
    )
    assert scan.status_code == 200
    result = scan.json()
    assert len(result["assessed"]) == len(assessable_entities)
    assert result["failures"] == []
    assert result["openTaskCount"] == sum(len(item["issues"]) for item in result["assessed"])
    expected_tasks_by_action: dict[str, int] = {}
    for item in result["assessed"]:
        for issue in item["issues"]:
            expected_tasks_by_action[issue["action"]] = (
                expected_tasks_by_action.get(issue["action"], 0) + 1
            )

    summary = local_client.get("/api/v1/quality/summary")
    assert summary.status_code == 200
    assert summary.json() == {
        "generatedAt": summary.json()["generatedAt"],
        "assessedEntityCount": len(result["assessed"]),
        "healthyCount": sum(
            1
            for item in result["assessed"]
            if item["status"] in {"healthy", "attention"}
        ),
        "attentionCount": sum(
            1 for item in result["assessed"] if item["status"] == "attention"
        ),
        "criticalCount": sum(
            1 for item in result["assessed"] if item["status"] == "critical"
        ),
        "averageScore": int(
            sum(item["score"] for item in result["assessed"])
            / max(len(result["assessed"]), 1),
        ),
        "openTaskCount": result["openTaskCount"],
        "scheduledTaskCount": 0,
        "tasksByAction": expected_tasks_by_action,
    }
    tasks = local_client.get(
        "/api/v1/quality/tasks",
        params={"status": "open"},
    ).json()
    assert len(tasks) == 6
    assert all(task["profileVersion"] == "1.0.0" for task in tasks)

    repeated = local_client.post(
        "/api/v1/quality/scans",
        json={"entityIds": []},
    )
    assert repeated.status_code == 200
    assert repeated.json()["openTaskCount"] == 6
    assert (
        len(
            local_client.get(
                "/api/v1/quality/tasks",
                params={"status": "open"},
            ).json()
        )
        == 6
    )


def test_quality_scan_is_permissioned_and_isolates_missing_entities() -> None:
    editor_client = api_client(roles="editor")
    assert editor_client.get("/api/v1/quality/summary").status_code == 200
    denied = editor_client.post(
        "/api/v1/quality/scans",
        json={"entityIds": []},
    )
    assert denied.status_code == 403

    admin_client = api_client()
    missing = admin_client.post(
        "/api/v1/quality/scans",
        json={"entityIds": ["entity-missing"]},
    )
    assert missing.status_code == 200
    assert missing.json()["failures"] == [
        {
            "entityId": "entity-missing",
            "code": "entity-not-found",
            "message": "requested entity does not exist",
        }
    ]


def test_agents_claim_version_pinned_maintenance_work_with_a_lease() -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    assert (
        local_client.post(
            "/api/v1/quality/scans",
            json={"entityIds": []},
        ).status_code
        == 200
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    assert dispatch_quality_maintenance_events(
        repository,
        graph,
        limit=1,
    ) == {"scheduled": 1, "skipped": 0, "failed": 0}
    schedule = repository.list_agent_graph_schedules(
        graph_id=graph.id,
        limit=1,
    )[0]
    execute_agent_schedule_once(
        repository,
        loaded_pack.registry(),
        schedule.id,
    )
    queued = repository.list_maintenance_work_items(status="queued")
    assert len(queued) == 1
    ready = repository.activate_maintenance_work_item(queued[0].id)

    listed = local_client.get(
        "/api/v1/maintenance/work-items",
        params={"status": "ready"},
    )
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == ready.id
    assert "leaseToken" not in listed.json()[0]

    claim = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/claim",
        json={"leaseSeconds": 300},
    )
    assert claim.status_code == 200
    claim_document = claim.json()
    lease_token = claim_document["leaseToken"]
    assert lease_token
    assert claim_document["workItem"]["status"] == "claimed"
    assert claim_document["workItem"]["assigneeId"] == "api-test-user"
    assert "leaseToken" not in claim_document["workItem"]
    claimed_detail = local_client.get(f"/api/v1/maintenance/work-items/{ready.id}")
    assert claimed_detail.status_code == 200
    assert "leaseToken" not in claimed_detail.json()

    repeated = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/claim",
        json={"leaseSeconds": 300},
    )
    assert repeated.status_code == 200
    assert repeated.json()["leaseToken"] == lease_token
    occupied = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/claim",
        json={"leaseSeconds": 300},
        headers={
            "x-hardatlas-dev-principal": "other-agent",
            "x-hardatlas-dev-roles": "agent-runner",
        },
    )
    assert occupied.status_code == 409

    heartbeat = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/heartbeat",
        json={"leaseToken": lease_token, "leaseSeconds": 600},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["status"] == "claimed"
    released = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/release",
        json={"leaseToken": lease_token},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "ready"
    assert released.json()["assigneeId"] is None

    claimed_again = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    second_token = claimed_again["leaseToken"]
    unverified = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/complete",
        json={
            "leaseToken": second_token,
            "evidenceRefs": [{"kind": "source-snapshot", "id": "snapshot-missing"}],
            "outputRefs": [],
        },
    )
    assert unverified.status_code == 409
    assert "does not exist" in unverified.json()["detail"]

    snapshot = SourceSnapshot(
        id="snapshot-maintenance-completion",
        source_id="source-maintenance-test",
        source_version="1.0.0",
        url="https://knowledge.example.org/evidence",
        content_sha256="a" * 64,
        storage_key="sources/test/aa",
        media_type="application/json",
        byte_size=1,
        http_status=200,
        license_id="CC-BY-4.0",
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
    )
    repository.save_source_snapshot(snapshot)
    completed = local_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/complete",
        json={
            "leaseToken": second_token,
            "evidenceRefs": [{"kind": "source-snapshot", "id": snapshot.id}],
            "outputRefs": [],
        },
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["evidenceRefs"] == [{"kind": "source-snapshot", "id": snapshot.id}]
    assert completed.json()["assigneeId"] is None

    assert dispatch_quality_maintenance_events(
        repository,
        graph,
        limit=1,
    ) == {"scheduled": 1, "skipped": 0, "failed": 0}
    next_schedule = next(
        item
        for item in repository.list_agent_graph_schedules(
            graph_id=graph.id,
            limit=10,
        )
        if item.status == "queued"
    )
    execute_agent_schedule_once(
        repository,
        loaded_pack.registry(),
        next_schedule.id,
    )
    next_work = repository.list_maintenance_work_items(status="queued")[0]
    repository.activate_maintenance_work_item(next_work.id)
    next_claim = local_client.post(
        f"/api/v1/maintenance/work-items/{next_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    blocked = local_client.post(
        f"/api/v1/maintenance/work-items/{next_work.id}/block",
        json={
            "leaseToken": next_claim["leaseToken"],
            "reason": "当前来源许可不允许自动处理",
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["blockedReason"] == ("当前来源许可不允许自动处理")
    assert blocked.json()["assigneeId"] is None
    requeued = local_client.post(
        f"/api/v1/maintenance/work-items/{next_work.id}/requeue",
    )
    assert requeued.status_code == 200
    assert requeued.json()["status"] == "queued"
    assert requeued.json()["blockedReason"] is None
    assert repository.pending_outbox_ids(topic="maintenance.work.requested")
    duplicate_requeue = local_client.post(
        f"/api/v1/maintenance/work-items/{next_work.id}/requeue",
    )
    assert duplicate_requeue.status_code == 409
    audit_documents = [
        str(event.model_dump(mode="json", by_alias=True))
        for event in repository.list_audit_events()
    ]
    assert all(
        token not in document
        for token in (lease_token, second_token)
        for document in audit_documents
    )

    editor = api_client(repository, roles="editor")
    assert editor.get("/api/v1/maintenance/work-items").status_code == 200
    assert (
        editor.post(
            f"/api/v1/maintenance/work-items/{ready.id}/claim",
            json={"leaseSeconds": 300},
        ).status_code
        == 403
    )


def _prepare_routed_work(
    repository: KnowledgeRepository,
    local_client: TestClient,
    *,
    entity_id: str,
    action: str,
):
    scan = local_client.post(
        "/api/v1/quality/scans",
        json={"entityIds": [entity_id]},
    )
    assert scan.status_code == 200
    task = next(
        item
        for item in repository.list_maintenance_tasks(status="open")
        if item.entity.id == entity_id and item.action == action
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    schedule = schedule_quality_maintenance_task_once(
        repository,
        graph,
        task.id,
    )
    assert schedule is not None
    execute_agent_schedule_once(
        repository,
        loaded_pack.registry(),
        schedule.id,
    )
    work_item = next(
        item
        for item in repository.list_maintenance_work_items(status="queued")
        if item.maintenance_task_id == task.id
    )
    return repository.activate_maintenance_work_item(work_item.id)


def test_agents_submit_translation_and_taxonomy_work_as_governed_proposals() -> None:
    translation_repository = isolated_repository()
    translation_client = api_client(translation_repository)
    ginkgo = translation_repository.get_entity_by_id("entity-ginkgo")
    assert ginkgo is not None
    ginkgo.description = [
        item for item in ginkgo.description if item.locale != "en"
    ]
    ginkgo.revision = ginkgo.revision.model_copy(
        update={
            "revision_id": "revision-ginkgo-translation-gap",
            "data_version": "data-translation-gap",
            "created_at": datetime(2026, 7, 29, 16, tzinfo=UTC),
        }
    )
    translation_repository.save_entity(ginkgo)
    translation_work = _prepare_routed_work(
        translation_repository,
        translation_client,
        entity_id=ginkgo.ref.id,
        action="translate",
    )
    assert translation_work.route == "translation-evidence"
    translation_claim = translation_client.post(
        f"/api/v1/maintenance/work-items/{translation_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    citation_id = ginkgo.citations[0].id
    translation_payload = {
        "leaseToken": translation_claim["leaseToken"],
        "targetLocale": "en",
        "translatedDescription": (
            "The only living representative of Ginkgoopsida, "
            "with a long evolutionary history."
        ),
        "citationIds": [citation_id],
        "confidence": 0.93,
    }
    translation = translation_client.post(
        (
            f"/api/v1/maintenance/work-items/{translation_work.id}"
            "/translation-proposal"
        ),
        json=translation_payload,
    )
    assert translation.status_code == 200
    translation_document = translation.json()
    assert translation_document["workItem"]["status"] == "completed"
    assert translation_document["proposal"]["proposal"]["proposalType"] == "translation"
    assert translation_document["proposal"]["proposal"]["risk"] == "medium"
    assert translation_document["proposal"]["proposal"]["operations"][0]["path"] == (
        "/description/-"
    )
    assert translation_repository.pending_outbox_ids(
        topic="governance.proposal.created"
    )
    repeated_translation = translation_client.post(
        (
            f"/api/v1/maintenance/work-items/{translation_work.id}"
            "/translation-proposal"
        ),
        json=translation_payload,
    )
    assert repeated_translation.status_code == 200
    assert (
        repeated_translation.json()["proposal"]["proposal"]["id"]
        == translation_document["proposal"]["proposal"]["id"]
    )
    evaluated_translation = evaluate_governed_proposal_once(
        translation_repository,
        translation_document["proposal"]["proposal"]["id"],
    )
    assert evaluated_translation.proposal.status == "human-review"
    assert evaluated_translation.policy_evaluation is not None
    assert evaluated_translation.policy_evaluation.required_approvals == 1

    taxonomy_repository = isolated_repository()
    taxonomy_client = api_client(taxonomy_repository)
    taxonomy_entity = taxonomy_repository.get_entity_by_id("entity-ginkgo")
    assert taxonomy_entity is not None
    taxonomy_entity.taxonomy_node_ids = []
    taxonomy_entity.revision = taxonomy_entity.revision.model_copy(
        update={
            "revision_id": "revision-ginkgo-taxonomy-gap",
            "data_version": "data-taxonomy-gap",
            "created_at": datetime(2026, 7, 29, 17, tzinfo=UTC),
        }
    )
    taxonomy_repository.save_entity(taxonomy_entity)
    taxonomy_work = _prepare_routed_work(
        taxonomy_repository,
        taxonomy_client,
        entity_id=taxonomy_entity.ref.id,
        action="classify",
    )
    assert taxonomy_work.route == "taxonomy-review"
    taxonomy_claim = taxonomy_client.post(
        f"/api/v1/maintenance/work-items/{taxonomy_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    invalid_taxonomy = taxonomy_client.post(
        f"/api/v1/maintenance/work-items/{taxonomy_work.id}/taxonomy-proposal",
        json={
            "leaseToken": taxonomy_claim["leaseToken"],
            "taxonomyNodeId": "tax-missing",
            "citationIds": [taxonomy_entity.citations[0].id],
            "confidence": 0.95,
        },
    )
    assert invalid_taxonomy.status_code == 409
    taxonomy_payload = {
        "leaseToken": taxonomy_claim["leaseToken"],
        "taxonomyNodeId": "tax-plants",
        "citationIds": [taxonomy_entity.citations[0].id],
        "confidence": 0.95,
    }
    taxonomy = taxonomy_client.post(
        f"/api/v1/maintenance/work-items/{taxonomy_work.id}/taxonomy-proposal",
        json=taxonomy_payload,
    )
    assert taxonomy.status_code == 200
    taxonomy_document = taxonomy.json()
    assert taxonomy_document["workItem"]["status"] == "completed"
    assert taxonomy_document["proposal"]["proposal"]["proposalType"] == "relation"
    taxonomy_operation = taxonomy_document["proposal"]["proposal"]["operations"][0]
    assert taxonomy_operation["path"] == "/taxonomyNodeIds"
    assert taxonomy_operation["before"] == []
    assert taxonomy_operation["after"] == ["tax-plants"]
    evaluated_taxonomy = evaluate_governed_proposal_once(
        taxonomy_repository,
        taxonomy_document["proposal"]["proposal"]["id"],
    )
    assert evaluated_taxonomy.proposal.status == "human-review"
    audit_text = "\n".join(
        str(event.model_dump(mode="json", by_alias=True))
        for event in [
            *translation_repository.list_audit_events(),
            *taxonomy_repository.list_audit_events(),
        ]
    )
    assert translation_claim["leaseToken"] not in audit_text
    assert taxonomy_claim["leaseToken"] not in audit_text
    assert translation_repository.verify_audit_chain()
    assert taxonomy_repository.verify_audit_chain()


def test_agent_runtime_matches_capabilities_capacity_and_heartbeat() -> None:
    repository = isolated_repository()
    admin_client = api_client(repository)
    ginkgo = repository.get_entity_by_id("entity-ginkgo")
    assert ginkgo is not None
    ginkgo.description = [
        item for item in ginkgo.description if item.locale != "en"
    ]
    ginkgo.revision = ginkgo.revision.model_copy(
        update={
            "revision_id": "revision-ginkgo-runtime-gap",
            "data_version": "data-runtime-gap",
            "created_at": datetime(2026, 7, 30, 8, tzinfo=UTC),
        }
    )
    repository.save_entity(ginkgo)
    ready = _prepare_routed_work(
        repository,
        admin_client,
        entity_id=ginkgo.ref.id,
        action="translate",
    )
    runtime_client = api_client(
        repository,
        roles="agent-runner",
        principal="translation-runtime-1",
    )
    registration_payload = {
        "definitionId": "agent-translation-maintainer",
        "definitionVersion": "1.0.0",
        "capabilities": [
            "entity-read",
            "translation-propose",
            "evidence-create",
        ],
        "supportedRoutes": ["translation-evidence"],
        "status": "online",
        "maxConcurrency": 1,
        "heartbeatTtlSeconds": 90,
        "labels": {"region": "local-test", "pool": "translation"},
    }
    registered = runtime_client.put(
        "/api/v1/agent-runtimes/self",
        json=registration_payload,
    )
    assert registered.status_code == 200
    assert registered.json()["runtime"]["id"] == "translation-runtime-1"
    assert registered.json()["effectiveStatus"] == "online"
    assert registered.json()["availableCapacity"] == 1

    claimed = runtime_client.post(
        "/api/v1/agent-runtimes/self/claim-next",
        json={"leaseSeconds": 300},
    )
    assert claimed.status_code == 200
    claim_document = claimed.json()
    assert claim_document["workItem"]["id"] == ready.id
    assert claim_document["workItem"]["assigneeId"] == "translation-runtime-1"
    lease_token = claim_document["leaseToken"]
    assert lease_token
    at_capacity = runtime_client.post(
        "/api/v1/agent-runtimes/self/claim-next",
        json={"leaseSeconds": 300},
    )
    assert at_capacity.status_code == 204
    runtimes = admin_client.get("/api/v1/agent-runtimes")
    assert runtimes.status_code == 200
    runtime_state = next(
        item
        for item in runtimes.json()
        if item["runtime"]["id"] == "translation-runtime-1"
    )
    assert runtime_state["activeLeaseCount"] == 1
    assert runtime_state["availableCapacity"] == 0

    draining = runtime_client.post(
        "/api/v1/agent-runtimes/self/heartbeat",
        json={"status": "draining"},
    )
    assert draining.status_code == 200
    released = runtime_client.post(
        f"/api/v1/maintenance/work-items/{ready.id}/release",
        json={"leaseToken": lease_token},
    )
    assert released.status_code == 200
    denied_while_draining = runtime_client.post(
        "/api/v1/agent-runtimes/self/claim-next",
        json={"leaseSeconds": 300},
    )
    assert denied_while_draining.status_code == 409
    online = runtime_client.post(
        "/api/v1/agent-runtimes/self/heartbeat",
        json={"status": "online"},
    )
    assert online.status_code == 200
    reclaimed = runtime_client.post(
        "/api/v1/agent-runtimes/self/claim-next",
        json={"leaseSeconds": 300},
    )
    assert reclaimed.status_code == 200
    assert reclaimed.json()["workItem"]["attempt"] == 2

    bad_runtime = api_client(
        repository,
        roles="agent-runner",
        principal="translation-runtime-invalid",
    )
    invalid = bad_runtime.put(
        "/api/v1/agent-runtimes/self",
        json={
            **registration_payload,
            "capabilities": ["entity-read", "translation-propose"],
        },
    )
    assert invalid.status_code == 409
    persisted_state = repository.get_agent_runtime_state(
        "translation-runtime-1",
    )
    assert persisted_state is not None
    offline = repository.get_agent_runtime_state(
        "translation-runtime-1",
        observed_at=(
            persisted_state.runtime.last_heartbeat_at
            + timedelta(
                seconds=persisted_state.runtime.heartbeat_ttl_seconds + 1
            )
        ),
    )
    assert offline is not None
    assert offline.effective_status == "offline"
    audit_text = "\n".join(
        str(event.model_dump(mode="json", by_alias=True))
        for event in repository.list_audit_events()
    )
    assert lease_token not in audit_text
    assert repository.verify_audit_chain()


def test_source_maintenance_work_can_be_completed_with_snapshot() -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    ginkgo = repository.get_entity_by_id("entity-ginkgo")
    assert ginkgo is not None
    ginkgo = ginkgo.model_copy(
        deep=True,
        update={
            "citations": [],
            "revision": ginkgo.revision.model_copy(
                update={
                    "revision_id": "revision-ginkgo-source-maintenance-complete",
                    "data_version": "data-source-maintenance-complete",
                    "created_at": datetime(2026, 7, 29, 18, tzinfo=UTC),
                }
            ),
        },
    )
    repository.save_entity(ginkgo)

    source_work = _prepare_routed_work(
        repository,
        local_client,
        entity_id=ginkgo.ref.id,
        action="add-citation",
    )
    assert source_work.route == "source-acquisition"

    source_claim = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    snapshot = SourceSnapshot(
        id="snapshot-source-maintenance-complete",
        source_id="source-maintenance-test",
        source_version="1.0.0",
        url="https://knowledge.example.org/evidence.json",
        content_sha256="a" * 64,
        storage_key="sources/maintenance/source-test",
        media_type="application/json",
        byte_size=1,
        http_status=200,
        license_id="CC-BY-4.0",
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 16, tzinfo=UTC),
    )
    repository.save_source_snapshot(snapshot)
    completion = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/complete",
        json={
            "leaseToken": source_claim["leaseToken"],
            "evidenceRefs": [{"kind": "source-snapshot", "id": snapshot.id}],
            "outputRefs": [],
        },
    )
    assert completion.status_code == 200
    completed = completion.json()
    assert completed["status"] == "completed"
    assert completed["evidenceRefs"] == [{"kind": "source-snapshot", "id": snapshot.id}]
    assert completed["route"] == "source-acquisition"

    audit_events = local_client.get("/api/v1/audit-events").json()
    assert any(
        event["action"] == "maintenance.work.complete"
        and event["resourceId"] == source_work.id
        and event["metadata"]["route"] == "source-acquisition"
        and event["metadata"]["outputRefs"] == []
        for event in audit_events
    )


def test_blocked_source_maintenance_work_can_be_requeued_and_completed() -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    ginkgo = repository.get_entity_by_id("entity-ginkgo")
    assert ginkgo is not None
    ginkgo = ginkgo.model_copy(
        deep=True,
        update={
            "citations": [],
            "revision": ginkgo.revision.model_copy(
                update={
                    "revision_id": "revision-ginkgo-source-maintenance-requeue",
                    "data_version": "data-source-maintenance-requeue",
                    "created_at": datetime(2026, 7, 29, 18, 1, tzinfo=UTC),
                }
            ),
        },
    )
    repository.save_entity(ginkgo)

    source_work = _prepare_routed_work(
        repository,
        local_client,
        entity_id=ginkgo.ref.id,
        action="add-citation",
    )
    assert source_work.route == "source-acquisition"

    first_claim = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    blocked = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/block",
        json={
            "leaseToken": first_claim["leaseToken"],
            "reason": "requires manual intervention",
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"

    requeued = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/requeue",
    )
    assert requeued.status_code == 200
    assert requeued.json()["status"] == "queued"
    assert requeued.json()["blockedReason"] is None
    outbox_ids = repository.pending_outbox_ids(topic="maintenance.work.requested")
    assert outbox_ids

    repository.activate_maintenance_work_item(source_work.id)
    second_claim = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    snapshot = SourceSnapshot(
        id="snapshot-source-maintenance-requeue-complete",
        source_id="source-maintenance-test",
        source_version="1.0.0",
        url="https://knowledge.example.org/evidence.json",
        content_sha256="a" * 64,
        storage_key="sources/maintenance/requeue",
        media_type="application/json",
        byte_size=1,
        http_status=200,
        license_id="CC-BY-4.0",
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 17, tzinfo=UTC),
    )
    repository.save_source_snapshot(snapshot)
    completed = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/complete",
        json={
            "leaseToken": second_claim["leaseToken"],
            "evidenceRefs": [{"kind": "source-snapshot", "id": snapshot.id}],
            "outputRefs": [],
        },
    )
    assert completed.status_code == 200
    completed_document = completed.json()
    assert completed_document["status"] == "completed"
    assert completed_document["route"] == "source-acquisition"
    assert completed_document["evidenceRefs"] == [
        {"kind": "source-snapshot", "id": snapshot.id},
    ]
    assert repository.verify_audit_chain()

    duplicate_requeue = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/requeue",
    )
    assert duplicate_requeue.status_code == 409

    audit_events = local_client.get("/api/v1/audit-events").json()
    assert any(
        event["action"] == "maintenance.work.block"
        and event["resourceId"] == source_work.id
        and event["metadata"]["route"] == source_work.route
        and event["metadata"]["reason"] == "requires manual intervention"
        for event in audit_events
    )
    requeue_event = next(
        event
        for event in audit_events
        if event["action"] == "maintenance.work.requeue"
        and event["resourceId"] == source_work.id
    )
    assert requeue_event["metadata"]["route"] == source_work.route
    assert requeue_event["metadata"]["outboxEventId"] in outbox_ids
    assert any(
        event["action"] == "maintenance.work.complete"
        and event["resourceId"] == source_work.id
        and event["metadata"]["route"] == source_work.route
        and event["metadata"]["outputRefs"] == []
        for event in audit_events
    )


def test_blocked_source_maintenance_work_requeue_can_be_driven_by_worker_dispatchers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = isolated_repository()
    local_client = api_client(repository)
    ginkgo = repository.get_entity_by_id("entity-ginkgo")
    assert ginkgo is not None
    ginkgo = ginkgo.model_copy(
        deep=True,
        update={
            "citations": [],
            "revision": ginkgo.revision.model_copy(
                update={
                    "revision_id": "revision-ginkgo-source-maintenance-worker-dispatch",
                    "data_version": "data-source-maintenance-worker-dispatch",
                    "created_at": datetime(2026, 7, 29, 18, 2, tzinfo=UTC),
                }
            ),
        },
    )
    repository.save_entity(ginkgo)

    source = SourceDefinition(
        id="source-plant-authority-api-recovery",
        version="1.0.0",
        name="Plant authority api recovery",
        kind="api",
        base_url="https://plants.example.org/records",
        allowed_hosts=["plants.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        entity_type_ids=["type-plant"],
        taxonomy_node_ids=["tax-plants"],
        status="active",
    )
    repository.save_source_definition(source)

    source_work = _prepare_routed_work(
        repository,
        local_client,
        entity_id=ginkgo.ref.id,
        action="add-citation",
    )
    assert source_work.route == "source-acquisition"

    first_claim = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/claim",
        json={"leaseSeconds": 300},
    ).json()
    blocked = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/block",
        json={
            "leaseToken": first_claim["leaseToken"],
            "reason": "requires manual intervention",
        },
    )
    assert blocked.status_code == 200
    requeued = local_client.post(
        f"/api/v1/maintenance/work-items/{source_work.id}/requeue",
    )
    assert requeued.status_code == 200
    outbox_ids = repository.pending_outbox_ids(topic="maintenance.work.requested")
    assert outbox_ids

    assert dispatch_maintenance_work_events(
        repository,
        lambda item_id: repository.activate_maintenance_work_item(item_id),
    ) == {"dispatched": len(outbox_ids), "failed": 0}
    assert repository.get_maintenance_work_item(source_work.id).status == "ready"

    routed, job = request_source_acquisition_for_work_once(
        repository,
        source_work.id,
        requested_at=datetime(2026, 7, 29, 14, 45, tzinfo=UTC),
    )
    assert routed.status == "ready"
    assert job is not None
    assert job.maintenance_work_item_id == source_work.id
    assert repository.pending_outbox_ids(topic="source.acquisition.requested")

    content = b'{"records":[{"id":"api"}]}'
    digest = hashlib.sha256(content).hexdigest()
    snapshot = SourceSnapshot(
        id=f"snapshot-{source.id}-{digest[:16]}",
        source_id=source.id,
        source_version=source.version,
        url=source.base_url,
        content_sha256=digest,
        storage_key=f"sources/{source.id}/{digest}",
        media_type="application/json",
        byte_size=len(content),
        http_status=200,
        license_id=source.license_id,
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 14, 50, tzinfo=UTC),
    )

    def fake_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        assert selected_source == source
        assert url == source.base_url
        return SimpleNamespace(snapshot=snapshot, content=content)

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        fake_acquire,
    )

    class WorkerStore:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put(self, *, key: str, **_: object) -> None:
            self.keys.append(key)

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected read: {key}")

    store = WorkerStore()
    assert dispatch_source_acquisition_events(
        repository,
        lambda job_id: execute_source_acquisition_once(
            repository,
            store,
            job_id,
        ),
    ) == {"dispatched": 1, "failed": 0}
    assert store.keys == [snapshot.storage_key]
    completed = repository.get_maintenance_work_item(source_work.id)
    assert completed is not None
    assert completed.status == "completed"
    assert [item.id for item in completed.evidence_refs] == [snapshot.id]
    assert [item.id for item in completed.output_refs] == [job.id]
    assert repository.verify_audit_chain()
    requeue_events = [
        event
        for event in local_client.get("/api/v1/audit-events").json()
        if event["action"] == "maintenance.work.requeue"
    ]
    assert any(event["resourceId"] == source_work.id for event in requeue_events)
    audit_events = local_client.get("/api/v1/audit-events").json()
    assert any(
        event["action"] == "maintenance.source.route"
        and event["resourceId"] == source_work.id
        and event["outcome"] == "success"
        for event in audit_events
    )
    assert any(
        event["action"] == "maintenance.source.complete"
        and event["resourceId"] == source_work.id
        and event["outcome"] == "success"
        for event in audit_events
    )
    assert any(
        event["action"] == "source.acquisition.execute"
        and event["resourceId"] == job.id
        and event["outcome"] == "success"
        for event in audit_events
    )


def test_hardware_is_an_optional_extension_route() -> None:
    response = client.post(
        "/api/v1/extensions/hardware/compatibility-checks",
        json={
            "subjects": [
                {"modelId": "cpu-fixture"},
                {"modelId": "board-fixture"},
            ],
            "facts": {"socket_match": True},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "compatible"


def test_unknown_entity_returns_404() -> None:
    assert client.get("/api/v1/entities/missing").status_code == 404
