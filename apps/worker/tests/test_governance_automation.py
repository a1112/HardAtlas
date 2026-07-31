from datetime import UTC, datetime

import pytest
from hardatlas_data import (
    KnowledgeRepository,
    LexicalSearchBackend,
    ReleaseOrchestrationError,
    ReleaseOrchestrator,
    ReleaseVerificationError,
)
from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    GovernanceService,
    KnowledgeEntity,
)
from hardatlas_worker.tasks import (
    dispatch_accepted_proposal_events,
    dispatch_governance_proposal_events,
    dispatch_release_published_events,
    evaluate_governed_proposal_once,
    plan_and_publish_accepted_proposals,
    verify_published_release_once,
)
from sqlalchemy import create_engine


def repository() -> KnowledgeRepository:
    result = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    result.create_schema()
    return result


def save_proposal(
    target: KnowledgeRepository,
    proposal_id: str,
    *,
    after: str,
    risk: str = "low",
    entity_id: str = "entity-ginkgo",
) -> None:
    proposal = AgentProposal(
        id=proposal_id,
        entity_id=entity_id,
        proposal_type="content",
        operations=[
            ChangeOperation(
                operation="replace",
                path="/description/0/value",
                before="旧描述",
                after=after,
                citation_ids=["cite-ginkgo-001"],
                confidence=0.98,
                machine_generated=True,
            )
        ],
        risk=risk,
        status="proposed",
        agent_run_id=f"run-{proposal_id}",
        impact={"entityCount": 1},
    )
    governed = GovernanceService([proposal], "policy-test").list_proposals()[0]
    target.save_governed_proposal(governed)


def entity(entity_id: str, slug: str) -> KnowledgeEntity:
    return KnowledgeEntity.model_validate(
        {
            "ref": {
                "id": entity_id,
                "slug": slug,
                "typeId": "type-plant",
                "canonicalName": slug,
            },
            "names": [{"locale": "zh-CN", "value": slug}],
            "aliases": [],
            "description": [{"locale": "zh-CN", "value": "旧描述"}],
            "taxonomyNodeIds": ["tax-plants"],
            "claims": [],
            "sections": [],
            "relationships": [],
            "citations": [
                {
                    "id": "cite-ginkgo-001",
                    "sourceId": "source-test",
                    "sourceTitle": "自动发布测试来源",
                    "sourceTier": "authoritative",
                    "retrievedAt": datetime(2026, 7, 29, tzinfo=UTC),
                    "locator": "section-1",
                    "quoteHash": "sha256:test",
                }
            ],
            "revision": {
                "revisionId": f"revision-{entity_id}-1",
                "dataVersion": "atlas-before-auto-release",
                "schemaVersion": "schema-test",
                "policyVersion": "policy-test",
                "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
            },
            "publicationStatus": "published",
        }
    )


def test_new_proposal_outbox_drives_idempotent_automatic_policy() -> None:
    target = repository()
    proposal_id = "proposal-auto-low-risk"
    save_proposal(target, proposal_id, after="自动接受的证据候选")
    queued: list[str] = []

    assert dispatch_governance_proposal_events(target, queued.append) == {
        "dispatched": 1,
        "failed": 0,
    }
    assert queued == [proposal_id]
    assert target.pending_outbox_records(
        topic="governance.proposal.created"
    ) == []

    accepted = evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-auto-test",
    )
    assert accepted.version == 2
    assert accepted.proposal.status == "accepted"
    assert accepted.policy_evaluation is not None
    assert accepted.policy_evaluation.outcome == "policy-approved"
    assert accepted.policy_evaluation.required_approvals == 0

    repeated = evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-auto-test",
    )
    assert repeated.version == 2
    assert repeated.proposal.status == "accepted"
    assert target.verify_audit_chain()
    audit = target.list_audit_events()[0]
    assert audit.action == "proposal.policy.evaluate"
    assert audit.metadata["automatic"] is True
    assert audit.metadata["proposalVersion"] == 2


def test_automatic_policy_routes_risk_and_conflicts_to_human_review() -> None:
    target = repository()
    save_proposal(
        target,
        "proposal-auto-medium-risk",
        after="中风险候选",
        risk="medium",
    )
    medium = evaluate_governed_proposal_once(
        target,
        "proposal-auto-medium-risk",
    )
    assert medium.proposal.status == "human-review"
    assert medium.policy_evaluation is not None
    assert medium.policy_evaluation.required_approvals == 1

    save_proposal(
        target,
        "proposal-auto-conflict-a",
        after="冲突候选 A",
    )
    save_proposal(
        target,
        "proposal-auto-conflict-b",
        after="冲突候选 B",
    )
    conflicted = evaluate_governed_proposal_once(
        target,
        "proposal-auto-conflict-a",
    )
    assert conflicted.proposal.status == "human-review"
    assert conflicted.policy_evaluation is not None
    assert conflicted.policy_evaluation.outcome == "human-review"
    assert conflicted.policy_evaluation.required_approvals == 1
    assert "conflict resolution" in conflicted.policy_evaluation.reasons[-1]


def test_governance_outbox_failure_is_retained_for_retry() -> None:
    target = repository()
    save_proposal(
        target,
        "proposal-auto-retry",
        after="需要重试的候选",
    )

    def unavailable(_proposal_id: str) -> None:
        raise ConnectionError("governance queue unavailable")

    assert dispatch_governance_proposal_events(target, unavailable) == {
        "dispatched": 0,
        "failed": 1,
    }
    pending = target.pending_outbox_records(
        topic="governance.proposal.created"
    )
    assert len(pending) == 1
    assert pending[0].aggregate_id == "proposal-auto-retry"

    queued: list[str] = []
    assert dispatch_governance_proposal_events(target, queued.append) == {
        "dispatched": 1,
        "failed": 0,
    }
    assert queued == ["proposal-auto-retry"]
    assert target.pending_outbox_records(
        topic="governance.proposal.created"
    ) == []


def test_accepted_outbox_batches_and_publishes_multiple_entities() -> None:
    target = repository()
    first_entity = entity("entity-auto-a", "自动条目 A")
    second_entity = entity("entity-auto-b", "自动条目 B")
    target.save_entity(first_entity)
    target.save_entity(second_entity)
    for proposal_id, entity_id, value in (
        ("proposal-auto-release-a", first_entity.ref.id, "自动发布 A"),
        ("proposal-auto-release-b", second_entity.ref.id, "自动发布 B"),
    ):
        save_proposal(
            target,
            proposal_id,
            entity_id=entity_id,
            after=value,
        )
        evaluated = evaluate_governed_proposal_once(
            target,
            proposal_id,
            policy_version="policy-auto-release",
        )
        assert evaluated.proposal.status == "accepted"

    queued: list[str] = []
    assert dispatch_accepted_proposal_events(
        target,
        queued.append,
    ) == {"dispatched": 2, "failed": 0}
    assert set(queued) == {
        "proposal-auto-release-a",
        "proposal-auto-release-b",
    }

    backend = LexicalSearchBackend(
        [first_entity, second_entity]
    )
    release = plan_and_publish_accepted_proposals(
        target,
        backend,
        queued[0],
        default_schema_version="schema-test",
    )
    assert release is not None
    assert release.status == "published"
    assert set(release.proposal_ids) == set(queued)
    assert backend.active_index == release.search_index
    assert (
        target.get_entity_by_id(first_entity.ref.id)
        .description[0]
        .value
        == "自动发布 A"
    )
    assert (
        target.get_entity_by_id(second_entity.ref.id)
        .description[0]
        .value
        == "自动发布 B"
    )
    assert all(
        target.get_governed_proposal(proposal_id)
        .proposal.status
        == "released"
        for proposal_id in queued
    )
    repeated = plan_and_publish_accepted_proposals(
        target,
        backend,
        queued[1],
        default_schema_version="schema-test",
    )
    assert repeated == release
    assert target.verify_audit_chain()


def test_automatic_release_resumes_after_search_activation_failure() -> None:
    class FlakyLexicalSearch(LexicalSearchBackend):
        failures_remaining = 1

        def activate(self, index_name: str) -> None:
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise ConnectionError("search alias unavailable")
            super().activate(index_name)

    target = repository()
    current = entity("entity-auto-retry", "自动重试条目")
    target.save_entity(current)
    proposal_id = "proposal-auto-release-retry"
    save_proposal(
        target,
        proposal_id,
        entity_id=current.ref.id,
        after="重试后发布",
    )
    evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-auto-release",
    )
    backend = FlakyLexicalSearch([current])

    with pytest.raises(
        ReleaseOrchestrationError,
        match="search index activation failed",
    ):
        plan_and_publish_accepted_proposals(
            target,
            backend,
            proposal_id,
            default_schema_version="schema-test",
        )
    publishing = target.list_releases()[0]
    assert publishing.status == "publishing"

    released = plan_and_publish_accepted_proposals(
        target,
        backend,
        proposal_id,
        default_schema_version="schema-test",
    )
    assert released is not None
    assert released.status == "published"
    assert (
        target.get_entity_by_id(current.ref.id)
        .description[0]
        .value
        == "重试后发布"
    )
    assert (
        target.get_governed_proposal(proposal_id)
        .proposal.status
        == "released"
    )


def test_published_release_outbox_runs_persistent_verification() -> None:
    target = repository()
    current = entity("entity-release-verify", "发布验证条目")
    target.save_entity(current)
    proposal_id = "proposal-release-verify"
    save_proposal(
        target,
        proposal_id,
        entity_id=current.ref.id,
        after="验证后的新描述",
    )
    evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-release-verify",
    )
    backend = LexicalSearchBackend([current])
    published = plan_and_publish_accepted_proposals(
        target,
        backend,
        proposal_id,
        default_schema_version="schema-test",
    )
    assert published is not None
    assert published.verification.status == "pending"

    queued: list[str] = []
    assert dispatch_release_published_events(
        target,
        queued.append,
    ) == {"dispatched": 1, "failed": 0}
    assert queued == [published.id]
    assert target.pending_outbox_records(
        topic="release.published"
    ) == []

    verified = verify_published_release_once(
        target,
        backend,
        published.id,
    )
    assert verified.status == "published"
    assert verified.verification.status == "passed"
    assert verified.verification.attempt == 1
    assert {
        check.key
        for check in verified.verification.checks
        if check.status == "passed"
    } == {
        "release-state",
        "search-readiness",
        "search-index",
        "entity-revisions",
        "citation-integrity",
        "relationship-integrity",
        "search-discoverability",
    }
    repeated = verify_published_release_once(
        target,
        backend,
        published.id,
    )
    assert repeated == verified
    verification_audits = [
        event
        for event in target.list_audit_events()
        if event.action == "release.verify"
    ]
    assert len(verification_audits) == 1
    assert target.verify_audit_chain()


def test_failed_automatic_release_verification_rolls_back_once() -> None:
    target = repository()
    current = entity("entity-release-regression", "发布回归条目")
    target.save_entity(current)
    proposal_id = "proposal-release-regression"
    save_proposal(
        target,
        proposal_id,
        entity_id=current.ref.id,
        after="无法被搜索的新描述",
    )
    evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-release-verify",
    )
    backend = LexicalSearchBackend([current])
    published = plan_and_publish_accepted_proposals(
        target,
        backend,
        proposal_id,
        default_schema_version="schema-test",
    )
    assert published is not None
    backend.documents.pop(current.ref.slug)

    rolled_back = verify_published_release_once(
        target,
        backend,
        published.id,
    )
    assert rolled_back.status == "rolled-back"
    assert rolled_back.verification.status == "failed"
    assert rolled_back.verification.automatic_rollback is True
    assert (
        rolled_back.verification.rollback_reason
        == (
            "search-discoverability: released entities are missing "
            "from exact-name search"
        )
    )
    assert (
        target.get_entity_by_id(current.ref.id)
        .description[0]
        .value
        == "旧描述"
    )
    assert backend.active_index == rolled_back.rollback_search_index
    actions = [
        event.action
        for event in target.list_audit_events()
    ]
    assert actions.count("release.verify") == 1
    assert actions.count("release.auto.rollback") == 1
    assert target.verify_audit_chain()


def test_release_verification_retries_transient_search_outage() -> None:
    class TemporarilyUnavailableSearch(LexicalSearchBackend):
        ready_calls = 0

        def ready(self) -> bool:
            self.ready_calls += 1
            return self.ready_calls > 1

    target = repository()
    current = entity("entity-release-retry-verify", "验证重试条目")
    target.save_entity(current)
    proposal_id = "proposal-release-retry-verify"
    save_proposal(
        target,
        proposal_id,
        entity_id=current.ref.id,
        after="基础设施恢复后验证",
    )
    evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-release-verify",
    )
    backend = TemporarilyUnavailableSearch([current])
    published = plan_and_publish_accepted_proposals(
        target,
        backend,
        proposal_id,
        default_schema_version="schema-test",
    )
    assert published is not None

    with pytest.raises(
        ReleaseVerificationError,
        match="search backend is not ready",
    ) as caught:
        verify_published_release_once(
            target,
            backend,
            published.id,
        )
    assert caught.value.retryable is True
    running = target.get_release(published.id)
    assert running is not None
    assert running.verification.status == "running"
    assert running.verification.attempt == 1

    verified = verify_published_release_once(
        target,
        backend,
        published.id,
    )
    assert verified.status == "published"
    assert verified.verification.status == "passed"
    assert verified.verification.attempt == 2


def test_failed_manual_release_verification_requires_human_rollback() -> None:
    target = repository()
    current = entity("entity-manual-verification", "人工发布验证条目")
    target.save_entity(current)
    proposal_id = "proposal-manual-verification"
    save_proposal(
        target,
        proposal_id,
        entity_id=current.ref.id,
        after="人工发布后的新描述",
    )
    accepted = evaluate_governed_proposal_once(
        target,
        proposal_id,
        policy_version="policy-release-verify",
    )
    assert accepted.proposal.status == "accepted"
    backend = LexicalSearchBackend([current])
    orchestrator = ReleaseOrchestrator(
        repository=target,
        search_backend=backend,
    )
    staged = orchestrator.stage(
        release_id="release-manual-verification",
        proposal_ids=[proposal_id],
        data_version="atlas-manual-verification",
        schema_versions=["schema-test"],
        policy_version="policy-release-verify",
        previous_release_id="none",
        trigger="manual",
        initiated_by="human-reviewer",
    )
    published = orchestrator.publish(staged.id).release
    backend.documents.pop(current.ref.slug)

    failed = verify_published_release_once(
        target,
        backend,
        published.id,
    )
    assert failed.status == "published"
    assert failed.verification.status == "failed"
    assert failed.verification.automatic_rollback is False
    assert (
        target.get_entity_by_id(current.ref.id)
        .description[0]
        .value
        == "人工发布后的新描述"
    )


def test_delayed_verification_never_rolls_back_a_newer_release() -> None:
    target = repository()
    first_entity = entity("entity-superseded-a", "延迟验证条目 A")
    second_entity = entity("entity-superseded-b", "延迟验证条目 B")
    target.save_entity(first_entity)
    target.save_entity(second_entity)
    backend = LexicalSearchBackend([first_entity, second_entity])
    releases = []
    for proposal_id, current, after in (
        (
            "proposal-superseded-a",
            first_entity,
            "第一批发布",
        ),
        (
            "proposal-superseded-b",
            second_entity,
            "第二批发布",
        ),
    ):
        save_proposal(
            target,
            proposal_id,
            entity_id=current.ref.id,
            after=after,
        )
        evaluate_governed_proposal_once(
            target,
            proposal_id,
            policy_version="policy-release-verify",
        )
        release = plan_and_publish_accepted_proposals(
            target,
            backend,
            proposal_id,
            default_schema_version="schema-test",
            batch_limit=1,
        )
        assert release is not None
        releases.append(release)

    delayed = verify_published_release_once(
        target,
        backend,
        releases[0].id,
    )
    assert delayed.status == "published"
    assert delayed.verification.status == "superseded"
    assert delayed.verification.automatic_rollback is False
    assert backend.active_index == releases[1].search_index
    assert (
        target.get_entity_by_id(second_entity.ref.id)
        .description[0]
        .value
        == "第二批发布"
    )
