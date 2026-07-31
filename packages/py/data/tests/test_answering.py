from datetime import UTC, datetime

from hardatlas_ai import (
    HandlerExecutionError,
    ModelInvocationRecord,
    ModelJSONRequest,
    ModelJSONResult,
    UsageRecord,
)
from hardatlas_data import (
    KnowledgeAnsweringService,
    KnowledgeRepository,
    LexicalSearchBackend,
)
from hardatlas_domain import KnowledgeEntity
from sqlalchemy import create_engine


def cited_entity() -> KnowledgeEntity:
    return KnowledgeEntity.model_validate(
        {
            "ref": {
                "id": "entity-snow-leopard",
                "slug": "snow-leopard",
                "typeId": "type-animal",
                "canonicalName": "雪豹",
            },
            "names": [
                {"locale": "zh-CN", "value": "雪豹"},
                {"locale": "en", "value": "Snow leopard"},
            ],
            "aliases": [{"locale": "la", "value": "Panthera uncia"}],
            "description": [
                {"locale": "zh-CN", "value": "生活在亚洲高山环境的猫科动物。"}
            ],
            "taxonomyNodeIds": ["tax-animals"],
            "claims": [],
            "sections": [
                {
                    "id": "section-habitat",
                    "key": "habitat",
                    "heading": [{"locale": "zh-CN", "value": "栖息地"}],
                    "body": [
                        {
                            "locale": "zh-CN",
                            "value": "雪豹适应寒冷、干燥和高海拔的山地环境。",
                        }
                    ],
                    "citationIds": ["citation-iucn"],
                    "order": 10,
                }
            ],
            "relationships": [],
            "citations": [
                {
                    "id": "citation-iucn",
                    "sourceId": "source-iucn",
                    "sourceTitle": "IUCN assessment",
                    "sourceTier": "authoritative",
                    "retrievedAt": datetime(2026, 7, 29, tzinfo=UTC),
                    "locator": "habitat",
                }
            ],
            "revision": {
                "revisionId": "revision-snow-leopard-1",
                "dataVersion": "atlas-test-1",
                "schemaVersion": "schema-1",
                "policyVersion": "policy-1",
                "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
            },
            "publicationStatus": "published",
        }
    )


def service(model_gateway=None) -> tuple[KnowledgeAnsweringService, KnowledgeRepository]:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(cited_entity())
    return (
        KnowledgeAnsweringService(
            repository=repository,
            search_backend=LexicalSearchBackend(repository.list_entities()),
            model_gateway=model_gateway,
        ),
        repository,
    )


class SuccessfulGateway:
    def __init__(self, *, use_unknown_evidence: bool = False) -> None:
        self.calls: list[ModelJSONRequest] = []
        self.use_unknown_evidence = use_unknown_evidence

    def generate_json(self, request: ModelJSONRequest) -> ModelJSONResult:
        self.calls.append(request)
        evidence = request.input_document["evidence"]
        assert isinstance(evidence, list)
        evidence_id = (
            "evidence:not-retrieved"
            if self.use_unknown_evidence
            else str(evidence[0]["id"])
        )
        return ModelJSONResult(
            output={
                "answer": "雪豹适应寒冷、干燥和高海拔的山地环境。[1]",
                "evidenceIds": [evidence_id],
                "confidence": 0.96,
            },
            invocation=ModelInvocationRecord(
                gateway_id="approved-proxy",
                gateway_base_url="https://gateway.internal",
                provider="proxy",
                model="answer-model",
                request_id="proxy-request-1",
                agent_id="public-answer-synthesizer",
            ),
            usage=UsageRecord(input_tokens=120, output_tokens=28, model_calls=1),
        )


class FailingGateway:
    def generate_json(self, request: ModelJSONRequest) -> ModelJSONResult:
        raise HandlerExecutionError(
            "model_gateway_transport_error",
            usage=UsageRecord(model_calls=1),
            model_invocations=[
                ModelInvocationRecord(
                    gateway_id="approved-proxy",
                    gateway_base_url="https://gateway.internal",
                    provider="proxy",
                    model="answer-model",
                    request_id="unavailable",
                    agent_id=request.agent_id,
                    status="failed",
                    error_code="model_gateway_transport_error",
                )
            ],
        )


def test_retrieval_answer_is_cited_versioned_and_persisted() -> None:
    answering, repository = service()
    answer = answering.answer(
        question="雪豹生活在什么环境？",
        locale="zh-CN",
        allow_model=False,
    )

    assert answer.status == "answered"
    assert answer.mode == "retrieval-synthesis"
    assert answer.entities[0].slug == "snow-leopard"
    assert answer.evidence[0].revision_id == "revision-snow-leopard-1"
    assert answer.citations[0].id == "citation-iucn"
    assert "[1]" in answer.answer
    assert repository.get_knowledge_answer(answer.id) == answer


def test_configured_proxy_synthesizes_only_from_retrieved_evidence() -> None:
    gateway = SuccessfulGateway()
    answering, _ = service(gateway)
    answer = answering.answer(
        question="Panthera uncia 的栖息环境是什么？",
        locale="zh-CN",
        allow_model=True,
    )

    assert len(gateway.calls) == 1
    assert gateway.calls[0].agent_id == "public-answer-synthesizer"
    assert gateway.calls[0].input_document["evidence"][0]["revisionId"] == (
        "revision-snow-leopard-1"
    )
    assert answer.mode == "model-proxy"
    assert answer.model_provenance is not None
    assert answer.model_provenance.gateway_id == "approved-proxy"
    assert answer.model_provenance.request_id == "proxy-request-1"


def test_model_cannot_select_evidence_outside_retrieval_whitelist() -> None:
    answering, _ = service(SuccessfulGateway(use_unknown_evidence=True))
    answer = answering.answer(
        question="雪豹生活在哪里？",
        locale="zh-CN",
        allow_model=True,
    )

    assert answer.mode == "retrieval-fallback"
    assert answer.fallback_reason == "model-output-rejected"
    assert answer.model_provenance is None
    assert answer.evidence[0].id.endswith("section:section-habitat")


def test_proxy_failure_is_visible_and_degrades_to_cited_retrieval() -> None:
    answering, _ = service(FailingGateway())
    answer = answering.answer(
        question="雪豹生活在哪里？",
        locale="zh-CN",
        allow_model=True,
    )

    assert answer.mode == "retrieval-fallback"
    assert answer.fallback_reason == "model_gateway_transport_error"
    assert answer.model_provenance is not None
    assert answer.model_provenance.status == "failed"
    assert answer.model_provenance.error_code == "model_gateway_transport_error"
    assert answer.citations[0].id == "citation-iucn"


def test_unknown_question_returns_explicit_insufficient_evidence_without_model() -> None:
    gateway = SuccessfulGateway()
    answering, _ = service(gateway)
    answer = answering.answer(
        question="量子引力的完整理论是什么？",
        locale="zh-CN",
        allow_model=True,
    )

    assert answer.status == "insufficient-evidence"
    assert answer.evidence == []
    assert answer.citations == []
    assert answer.model_provenance is None
    assert gateway.calls == []
