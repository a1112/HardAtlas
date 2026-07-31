import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from .knowledge import AgentProposal, ChangeOperation, Citation, KnowledgeModel


def _is_self_evidencing_citation_addition(
    operation: ChangeOperation,
) -> bool:
    match = re.fullmatch(r"/citations/([^/]+)", operation.path)
    if operation.operation != "add" or match is None or not isinstance(operation.after, dict):
        return False
    try:
        citation = Citation.model_validate(operation.after)
    except ValueError:
        return False
    citation_id = match.group(1).replace("~1", "/").replace("~0", "~")
    return citation.id == citation_id and bool(citation.locator) and bool(citation.quote_hash)


class PolicyEvaluation(KnowledgeModel):
    proposal_id: str
    outcome: Literal["policy-approved", "human-review", "policy-blocked"]
    policy_version: str
    required_approvals: int
    blockers: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReviewRecord(KnowledgeModel):
    id: str
    proposal_id: str
    reviewer_id: str
    decision: Literal["approve", "reject"]
    comment: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GovernedProposal(KnowledgeModel):
    version: int = Field(default=0, ge=0)
    proposal: AgentProposal
    policy_evaluation: PolicyEvaluation | None = None
    reviews: list[ReviewRecord] = Field(default_factory=list)
    source_proposal_ids: list[str] = Field(default_factory=list)
    superseded_by_proposal_id: str | None = None
    superseded_at: datetime | None = None


class ReleaseVerificationCheck(KnowledgeModel):
    key: Literal[
        "release-state",
        "search-readiness",
        "search-index",
        "entity-revisions",
        "citation-integrity",
        "relationship-integrity",
        "search-discoverability",
    ]
    status: Literal["passed", "failed", "skipped"]
    detail: str
    affected_entity_ids: list[str] = Field(default_factory=list)


class ReleaseVerification(KnowledgeModel):
    status: Literal[
        "pending",
        "running",
        "passed",
        "failed",
        "superseded",
    ] = "pending"
    attempt: int = Field(default=0, ge=0)
    checks: list[ReleaseVerificationCheck] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    automatic_rollback: bool = False
    rollback_reason: str | None = None


class ReleaseManifest(KnowledgeModel):
    id: str
    proposal_ids: list[str]
    data_version: str
    schema_versions: list[str]
    policy_version: str
    previous_release_id: str
    trigger: Literal["manual", "automatic"] = "manual"
    initiated_by: str | None = None
    status: Literal[
        "staged",
        "publishing",
        "published",
        "rolling-back",
        "rolled-back",
    ]
    search_index: str | None = None
    search_alias: str | None = None
    rollback_search_index: str | None = None
    entity_revisions_before: dict[str, str] = Field(default_factory=dict)
    entity_revisions_after: dict[str, str] = Field(default_factory=dict)
    created_entity_ids: list[str] = Field(default_factory=list)
    verification: ReleaseVerification = Field(
        default_factory=ReleaseVerification,
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    rolled_back_at: datetime | None = None


class GovernanceError(ValueError):
    pass


class GovernanceService:
    def __init__(self, proposals: list[AgentProposal], policy_version: str) -> None:
        self.policy_version = policy_version
        self.proposals = {
            proposal.id: GovernedProposal(proposal=proposal.model_copy(deep=True))
            for proposal in proposals
        }
        self.releases: dict[str, ReleaseManifest] = {}

    def add_proposal(self, proposal: AgentProposal) -> GovernedProposal:
        if proposal.id in self.proposals:
            raise GovernanceError("proposal already exists")
        governed = GovernedProposal(proposal=proposal.model_copy(deep=True))
        self.proposals[proposal.id] = governed
        return governed

    def list_proposals(self) -> list[GovernedProposal]:
        return list(self.proposals.values())

    def get_proposal(self, proposal_id: str) -> GovernedProposal:
        try:
            return self.proposals[proposal_id]
        except KeyError as error:
            raise GovernanceError("proposal not found") from error

    def evaluate(self, proposal_id: str) -> GovernedProposal:
        governed = self.get_proposal(proposal_id)
        proposal = governed.proposal
        if proposal.status in {"accepted", "rejected", "superseded", "released"}:
            raise GovernanceError("terminal proposal cannot be re-evaluated")
        blockers: list[str] = []
        reasons: list[str] = []

        if not proposal.operations:
            blockers.append("proposal has no operations")
        if any(
            not operation.citation_ids and not _is_self_evidencing_citation_addition(operation)
            for operation in proposal.operations
        ):
            blockers.append("every operation requires evidence")
        if any(operation.confidence < 0.85 for operation in proposal.operations):
            blockers.append("operation confidence is below policy threshold 0.85")

        if blockers:
            outcome = "policy-blocked"
            required_approvals = 0
            proposal.status = "policy-blocked"
        elif (
            proposal.risk == "low"
            and proposal.proposal_type in {"content", "relation", "translation"}
            and all(operation.operation != "remove" for operation in proposal.operations)
        ):
            outcome = "policy-approved"
            required_approvals = 0
            reasons.append("low-risk, evidence-backed, non-destructive proposal")
            proposal.status = "policy-approved"
        else:
            outcome = "human-review"
            required_approvals = (
                2
                if proposal.risk in {"high", "critical"} or proposal.proposal_type == "schema"
                else 1
            )
            reasons.append("risk or proposal type requires human review")
            proposal.status = "human-review"

        governed.policy_evaluation = PolicyEvaluation(
            proposal_id=proposal.id,
            outcome=outcome,
            policy_version=self.policy_version,
            required_approvals=required_approvals,
            blockers=blockers,
            reasons=reasons,
        )
        return governed

    def review(
        self,
        proposal_id: str,
        *,
        reviewer_id: str,
        decision: Literal["approve", "reject"],
        comment: str,
    ) -> GovernedProposal:
        governed = self.get_proposal(proposal_id)
        if governed.proposal.status != "human-review" or not governed.policy_evaluation:
            raise GovernanceError("proposal is not awaiting human review")
        if any(review.reviewer_id == reviewer_id for review in governed.reviews):
            raise GovernanceError("reviewer has already decided on this proposal")

        review = ReviewRecord(
            id=f"review-{proposal_id}-{len(governed.reviews) + 1}",
            proposal_id=proposal_id,
            reviewer_id=reviewer_id,
            decision=decision,
            comment=comment,
        )
        governed.reviews.append(review)

        if decision == "reject":
            governed.proposal.status = "rejected"
            return governed

        approval_count = sum(review.decision == "approve" for review in governed.reviews)
        if approval_count >= governed.policy_evaluation.required_approvals:
            governed.proposal.status = "accepted"
        return governed

    def accept_policy_approved(self, proposal_id: str) -> GovernedProposal:
        governed = self.get_proposal(proposal_id)
        if governed.proposal.status != "policy-approved":
            raise GovernanceError("proposal is not policy-approved")
        governed.proposal.status = "accepted"
        return governed

    def add_merge_proposal(
        self,
        proposal: AgentProposal,
        *,
        source_proposal_ids: list[str],
    ) -> tuple[GovernedProposal, list[GovernedProposal]]:
        if proposal.proposal_type != "merge":
            raise GovernanceError("merged proposal must use proposal type merge")
        if len(source_proposal_ids) != 2 or len(set(source_proposal_ids)) != 2:
            raise GovernanceError("merge requires exactly two unique source proposals")
        sources = [self.get_proposal(proposal_id) for proposal_id in source_proposal_ids]
        if any(source.proposal.status != "human-review" for source in sources):
            raise GovernanceError("merge sources must be awaiting human review")
        if proposal.id in self.proposals:
            raise GovernanceError("proposal already exists")

        governed = GovernedProposal(
            proposal=proposal.model_copy(deep=True),
            source_proposal_ids=sorted(source_proposal_ids),
        )
        superseded_at = datetime.now(UTC)
        for source in sources:
            source.proposal.status = "superseded"
            source.superseded_by_proposal_id = proposal.id
            source.superseded_at = superseded_at
        self.proposals[proposal.id] = governed
        return governed, sources

    def stage_release(
        self,
        *,
        release_id: str,
        proposal_ids: list[str],
        data_version: str,
        schema_versions: list[str],
        previous_release_id: str,
    ) -> ReleaseManifest:
        if release_id in self.releases:
            raise GovernanceError("release already exists")
        proposals = [self.get_proposal(proposal_id) for proposal_id in proposal_ids]
        if not proposals:
            raise GovernanceError("release requires at least one proposal")
        if any(item.proposal.status != "accepted" for item in proposals):
            raise GovernanceError("all release proposals must be accepted")
        manifest = ReleaseManifest(
            id=release_id,
            proposal_ids=proposal_ids,
            data_version=data_version,
            schema_versions=sorted(set(schema_versions)),
            policy_version=self.policy_version,
            previous_release_id=previous_release_id,
            status="staged",
        )
        self.releases[release_id] = manifest
        return manifest

    def publish_release(self, release_id: str) -> ReleaseManifest:
        try:
            manifest = self.releases[release_id]
        except KeyError as error:
            raise GovernanceError("release not found") from error
        if manifest.status != "publishing":
            raise GovernanceError("only publishing releases can be completed")
        manifest.status = "published"
        manifest.published_at = datetime.now(UTC)
        for proposal_id in manifest.proposal_ids:
            self.proposals[proposal_id].proposal.status = "released"
        return manifest

    def begin_release_publication(self, release_id: str) -> ReleaseManifest:
        try:
            manifest = self.releases[release_id]
        except KeyError as error:
            raise GovernanceError("release not found") from error
        if manifest.status != "staged":
            raise GovernanceError("only staged releases can begin publication")
        manifest.status = "publishing"
        return manifest

    def rollback_release(self, release_id: str) -> ReleaseManifest:
        try:
            manifest = self.releases[release_id]
        except KeyError as error:
            raise GovernanceError("release not found") from error
        if manifest.status != "rolling-back":
            raise GovernanceError("only rolling-back releases can be completed")
        manifest.status = "rolled-back"
        manifest.rolled_back_at = datetime.now(UTC)
        return manifest

    def begin_release_rollback(self, release_id: str) -> ReleaseManifest:
        try:
            manifest = self.releases[release_id]
        except KeyError as error:
            raise GovernanceError("release not found") from error
        if manifest.status != "published":
            raise GovernanceError("only published releases can begin rollback")
        manifest.status = "rolling-back"
        return manifest
