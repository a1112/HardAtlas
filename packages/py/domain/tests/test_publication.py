from datetime import UTC, datetime

import pytest
from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    GovernanceService,
    KnowledgeEntity,
    PublicationError,
    apply_release_to_entity,
)


def entity() -> KnowledgeEntity:
    return KnowledgeEntity.model_validate(
        {
            "ref": {
                "id": "entity-snow-leopard",
                "slug": "snow-leopard",
                "typeId": "type-animal",
                "canonicalName": "雪豹",
            },
            "names": [{"locale": "zh-CN", "value": "雪豹"}],
            "aliases": [{"locale": "la", "value": "Panthera uncia"}],
            "description": [{"locale": "zh-CN", "value": "大型猫科动物"}],
            "taxonomyNodeIds": ["tax-animals"],
            "claims": [
                {
                    "id": "claim-conservation",
                    "attributeDefinitionId": "attr-conservation",
                    "originalValue": "EN",
                    "normalizedValue": "EN",
                    "displayValue": [{"locale": "zh-CN", "value": "濒危（EN）"}],
                    "confidence": 0.94,
                    "citationIds": ["cite-1"],
                    "revisionId": "revision-1",
                }
            ],
            "sections": [
                {
                    "id": "section-overview",
                    "key": "overview",
                    "heading": [{"locale": "zh-CN", "value": "概述"}],
                    "body": [{"locale": "zh-CN", "value": "旧内容"}],
                    "citationIds": ["cite-1"],
                    "order": 10,
                }
            ],
            "relationships": [],
            "citations": [
                {
                    "id": "cite-1",
                    "sourceId": "source-1",
                    "sourceTitle": "权威来源",
                    "sourceTier": "authoritative",
                    "retrievedAt": datetime(2026, 7, 29, tzinfo=UTC),
                }
            ],
            "revision": {
                "revisionId": "revision-1",
                "dataVersion": "data-1",
                "schemaVersion": "schema-1",
                "policyVersion": "policy-1",
                "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
            },
            "publicationStatus": "published",
        }
    )


def accepted_proposal(
    operation: ChangeOperation | list[ChangeOperation],
    *,
    proposal_id: str = "proposal-1",
) -> object:
    proposal = AgentProposal(
        id=proposal_id,
        entity_id="entity-snow-leopard",
        proposal_type="content",
        operations=operation if isinstance(operation, list) else [operation],
        risk="low",
        status="proposed",
        agent_run_id="run-1",
        impact={"entityCount": 1},
    )
    service = GovernanceService([proposal], "policy-2")
    service.evaluate(proposal_id)
    return service.accept_policy_approved(proposal_id)


def test_release_creates_new_immutable_revision_and_updates_claim() -> None:
    governed = accepted_proposal(
        ChangeOperation(
            operation="replace",
            path="/claims/attr-conservation",
            before="EN",
            after="VU",
            citation_ids=["cite-1"],
            confidence=0.98,
        )
    )
    released = apply_release_to_entity(
        entity(),
        [governed],
        data_version="atlas-2",
        schema_version="schema-2",
        policy_version="policy-2",
    )
    assert released.claims[0].original_value == "VU"
    assert released.claims[0].display_value[0].value == "VU"
    assert released.revision.revision_id != "revision-1"
    assert released.claims[0].revision_id == released.revision.revision_id


def test_release_updates_localized_section_body() -> None:
    governed = accepted_proposal(
        ChangeOperation(
            operation="replace",
            path="/sections/0/body",
            after="由 Agent 提议并经过审核的新内容",
            citation_ids=["cite-1"],
            confidence=0.96,
        )
    )
    released = apply_release_to_entity(
        entity(),
        [governed],
        data_version="atlas-2",
        schema_version="schema-2",
        policy_version="policy-2",
    )
    assert released.sections[0].body[0].value == "由 Agent 提议并经过审核的新内容"
    assert released.sections[0].body[0].machine_generated is True


def test_release_atomically_adds_snapshot_citation_and_evidence_backed_value() -> None:
    citation = {
        "id": "cite-source/2",
        "sourceId": "source-2",
        "sourceTitle": "不可变来源快照",
        "sourceTier": "authoritative",
        "sourceUrl": "https://example.org/snow-leopard",
        "retrievedAt": "2026-07-30T00:00:00Z",
        "locator": "snapshot-source-2#/items/0/description",
        "quoteHash": "a" * 64,
    }
    governed = accepted_proposal(
        [
            ChangeOperation(
                operation="add",
                path="/citations/cite-source~12",
                after=citation,
                citation_ids=[],
                confidence=1,
            ),
            ChangeOperation(
                operation="replace",
                path="/description/0/value",
                before="大型猫科动物",
                after="生活在中亚高山地区的大型猫科动物",
                citation_ids=["cite-source/2"],
                confidence=0.97,
            ),
        ]
    )
    released = apply_release_to_entity(
        entity(),
        [governed],
        data_version="atlas-2",
        schema_version="schema-2",
        policy_version="policy-2",
    )
    assert released.description[0].value == "生活在中亚高山地区的大型猫科动物"
    assert released.citations[-1].id == "cite-source/2"
    assert released.citations[-1].locator == ("snapshot-source-2#/items/0/description")


def test_release_rejects_stale_before_value_and_unknown_evidence() -> None:
    stale = accepted_proposal(
        ChangeOperation(
            operation="replace",
            path="/claims/attr-conservation",
            before="CR",
            after="VU",
            citation_ids=["cite-1"],
            confidence=0.98,
        )
    )
    with pytest.raises(PublicationError, match="before value conflicts"):
        apply_release_to_entity(
            entity(),
            [stale],
            data_version="atlas-2",
            schema_version="schema-2",
            policy_version="policy-2",
        )

    unknown_evidence = accepted_proposal(
        ChangeOperation(
            operation="replace",
            path="/sections/0/body",
            after="新内容",
            citation_ids=["missing-citation"],
            confidence=0.98,
        ),
        proposal_id="proposal-2",
    )
    with pytest.raises(PublicationError, match="unknown citations"):
        apply_release_to_entity(
            entity(),
            [unknown_evidence],
            data_version="atlas-2",
            schema_version="schema-2",
            policy_version="policy-2",
        )
