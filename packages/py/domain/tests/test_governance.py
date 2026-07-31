import pytest
from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    GovernanceError,
    GovernanceService,
)


def proposal(
    *,
    proposal_id: str = "proposal-1",
    risk: str = "high",
    proposal_type: str = "content",
    confidence: float = 0.96,
    citations: list[str] | None = None,
) -> AgentProposal:
    return AgentProposal.model_validate(
        {
            "id": proposal_id,
            "entityId": "entity-1",
            "proposalType": proposal_type,
            "operations": [
                ChangeOperation(
                    operation="replace",
                    path="/claims/status",
                    before="old",
                    after="new",
                    citation_ids=citations if citations is not None else ["cite-1"],
                    confidence=confidence,
                )
            ],
            "risk": risk,
            "status": "proposed",
            "agentRunId": "run-1",
            "impact": {"entityCount": 1, "relationCount": 2, "localeCount": 1},
        }
    )


def test_high_risk_proposal_requires_two_distinct_reviewers() -> None:
    service = GovernanceService([proposal()], "policy-1")
    governed = service.evaluate("proposal-1")
    assert governed.proposal.status == "human-review"
    assert governed.policy_evaluation
    assert governed.policy_evaluation.required_approvals == 2

    service.review("proposal-1", reviewer_id="alice", decision="approve", comment="verified")
    governed = service.review(
        "proposal-1",
        reviewer_id="bob",
        decision="approve",
        comment="second review",
    )
    assert governed.proposal.status == "accepted"


def test_missing_evidence_blocks_policy_gate() -> None:
    service = GovernanceService([proposal(citations=[])], "policy-1")
    governed = service.evaluate("proposal-1")
    assert governed.proposal.status == "policy-blocked"
    assert governed.policy_evaluation
    assert "every operation requires evidence" in governed.policy_evaluation.blockers


def test_snapshot_citation_addition_is_self_evidencing_only_with_locator() -> None:
    citation = {
        "id": "cite-source/2",
        "sourceId": "source-2",
        "sourceTitle": "不可变来源快照",
        "sourceTier": "authoritative",
        "retrievedAt": "2026-07-30T00:00:00Z",
        "locator": "snapshot-source-2#/items/0",
        "quoteHash": "a" * 64,
    }
    valid = proposal(risk="low").model_copy(
        update={
            "operations": [
                ChangeOperation(
                    operation="add",
                    path="/citations/cite-source~12",
                    after=citation,
                    citation_ids=[],
                    confidence=1,
                )
            ]
        }
    )
    governed = GovernanceService([valid], "policy-1").evaluate(valid.id)
    assert governed.proposal.status == "policy-approved"

    invalid = valid.model_copy(deep=True)
    invalid.id = "proposal-invalid-citation"
    invalid.operations[0].after["locator"] = None
    invalid.status = "proposed"
    blocked = GovernanceService([invalid], "policy-1").evaluate(invalid.id)
    assert blocked.proposal.status == "policy-blocked"
    assert blocked.policy_evaluation
    assert "every operation requires evidence" in blocked.policy_evaluation.blockers


def test_low_risk_non_destructive_change_can_be_policy_approved() -> None:
    service = GovernanceService([proposal(risk="low")], "policy-1")
    governed = service.evaluate("proposal-1")
    assert governed.proposal.status == "policy-approved"
    assert service.accept_policy_approved("proposal-1").proposal.status == "accepted"


def test_merge_proposal_supersedes_sources_without_bypassing_policy() -> None:
    left = proposal(proposal_id="proposal-left", risk="medium")
    right = proposal(proposal_id="proposal-right", risk="medium")
    merge = proposal(
        proposal_id="proposal-merge",
        risk="medium",
        proposal_type="merge",
    )
    service = GovernanceService([left, right], "policy-1")
    service.evaluate("proposal-left")
    service.evaluate("proposal-right")

    governed, sources = service.add_merge_proposal(
        merge,
        source_proposal_ids=["proposal-right", "proposal-left"],
    )

    assert governed.source_proposal_ids == ["proposal-left", "proposal-right"]
    assert governed.proposal.status == "proposed"
    assert {item.proposal.status for item in sources} == {"superseded"}
    assert all(item.superseded_by_proposal_id == "proposal-merge" for item in sources)
    with pytest.raises(GovernanceError, match="terminal"):
        service.evaluate("proposal-left")
    evaluated = service.evaluate("proposal-merge")
    assert evaluated.proposal.status == "human-review"
    assert evaluated.policy_evaluation
    assert evaluated.policy_evaluation.reasons == ["risk or proposal type requires human review"]


def test_release_requires_accepted_proposals_and_can_roll_back() -> None:
    service = GovernanceService([proposal(risk="low")], "policy-1")
    service.evaluate("proposal-1")
    with pytest.raises(GovernanceError, match="must be accepted"):
        service.stage_release(
            release_id="release-1",
            proposal_ids=["proposal-1"],
            data_version="data-1",
            schema_versions=["schema-1"],
            previous_release_id="release-0",
        )
    service.accept_policy_approved("proposal-1")
    staged = service.stage_release(
        release_id="release-1",
        proposal_ids=["proposal-1"],
        data_version="data-1",
        schema_versions=["schema-1"],
        previous_release_id="release-0",
    )
    assert staged.status == "staged"
    assert service.begin_release_publication("release-1").status == "publishing"
    assert service.publish_release("release-1").status == "published"
    assert service.get_proposal("proposal-1").proposal.status == "released"
    assert service.begin_release_rollback("release-1").status == "rolling-back"
    assert service.rollback_release("release-1").status == "rolled-back"
