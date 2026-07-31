import json
from itertools import combinations

from pydantic import Field

from .governance import GovernedProposal
from .knowledge import AgentProposal, ChangeOperation, KnowledgeModel


class ProposalConflict(KnowledgeModel):
    id: str
    proposal_ids: list[str] = Field(min_length=2, max_length=2)
    entity_id: str | None = None
    paths: list[str] = Field(min_length=1)
    reason: str
    blocking: bool = True


class ProposalConflictResolution(KnowledgeModel):
    path: str
    proposal_id: str


class ConflictMergeError(ValueError):
    pass


ACTIVE_PROPOSAL_STATUSES = {
    "proposed",
    "policy-approved",
    "policy-blocked",
    "human-review",
    "accepted",
}


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
    )


def build_conflict_merge_proposal(
    conflict: ProposalConflict,
    sources: list[GovernedProposal],
    *,
    proposal_id: str,
    agent_run_id: str,
    resolutions: list[ProposalConflictResolution],
) -> AgentProposal:
    source_by_id = {item.proposal.id: item for item in sources}
    if set(source_by_id) != set(conflict.proposal_ids):
        raise ConflictMergeError("merge sources do not match the conflict")
    if any(
        item.proposal.status != "human-review"
        for item in source_by_id.values()
    ):
        raise ConflictMergeError("merge sources must be awaiting human review")
    resolution_by_path = {item.path: item.proposal_id for item in resolutions}
    if len(resolution_by_path) != len(resolutions):
        raise ConflictMergeError("conflict resolution paths must be unique")
    if set(resolution_by_path) != set(conflict.paths):
        raise ConflictMergeError("every conflict path requires one resolution")
    if any(
        source_id not in source_by_id
        for source_id in resolution_by_path.values()
    ):
        raise ConflictMergeError("resolution must select one conflict source")

    selected: list[tuple[str, ChangeOperation]] = []
    for source_id in sorted(source_by_id):
        proposal = source_by_id[source_id].proposal
        for operation in proposal.operations:
            affected_paths = [
                path
                for path in conflict.paths
                if _paths_overlap(operation.path, path)
            ]
            if affected_paths:
                selected_sources = {
                    resolution_by_path[path] for path in affected_paths
                }
                if len(selected_sources) > 1:
                    raise ConflictMergeError(
                        "one operation spans paths assigned to different sources"
                    )
                if source_id not in selected_sources:
                    continue
            selected.append((source_id, operation))

    for path, selected_source_id in resolution_by_path.items():
        if not any(
            source_id == selected_source_id
            and _paths_overlap(operation.path, path)
            for source_id, operation in selected
        ):
            raise ConflictMergeError(
                f"selected source has no operation for conflict path {path}"
            )

    operations: list[ChangeOperation] = []
    fingerprints: set[str] = set()
    for _, operation in selected:
        fingerprint = json.dumps(
            operation.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        operations.append(operation.model_copy(deep=True))
    if not operations:
        raise ConflictMergeError("merged proposal has no operations")

    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    risk = max(
        (item.proposal.risk for item in sources),
        key=lambda value: risk_order[value],
    )
    impact_keys = {
        key for item in sources for key in item.proposal.impact
    }
    impact = {
        key: max(item.proposal.impact.get(key, 0) for item in sources)
        for key in sorted(impact_keys)
    }
    entity_ids = {item.proposal.entity_id for item in sources}
    if len(entity_ids) != 1:
        raise ConflictMergeError("merge sources must share one entity scope")
    return AgentProposal(
        id=proposal_id,
        entity_id=entity_ids.pop(),
        proposal_type="merge",
        operations=operations,
        risk=risk,
        status="proposed",
        agent_run_id=agent_run_id,
        impact=impact,
    )


def detect_proposal_conflicts(
    proposals: list[GovernedProposal],
) -> list[ProposalConflict]:
    """Find active proposals that write incompatible values to one scope."""
    active = [
        item
        for item in proposals
        if item.proposal.status in ACTIVE_PROPOSAL_STATUSES
    ]
    conflicts: list[ProposalConflict] = []
    for left, right in combinations(active, 2):
        left_proposal = left.proposal
        right_proposal = right.proposal
        if left_proposal.entity_id != right_proposal.entity_id:
            continue
        if left_proposal.entity_id is None and (
            left_proposal.proposal_type != right_proposal.proposal_type
        ):
            continue
        paths: set[str] = set()
        for left_operation in left_proposal.operations:
            for right_operation in right_proposal.operations:
                if not _paths_overlap(
                    left_operation.path,
                    right_operation.path,
                ):
                    continue
                if (
                    left_operation.operation == right_operation.operation
                    and left_operation.after == right_operation.after
                ):
                    continue
                paths.add(
                    left_operation.path
                    if len(left_operation.path) >= len(right_operation.path)
                    else right_operation.path
                )
        if not paths:
            continue
        proposal_ids = sorted([left_proposal.id, right_proposal.id])
        conflicts.append(
            ProposalConflict(
                id=f"conflict-{proposal_ids[0]}--{proposal_ids[1]}",
                proposal_ids=proposal_ids,
                entity_id=left_proposal.entity_id,
                paths=sorted(paths),
                reason="active proposals write incompatible values to overlapping paths",
            )
        )
    return conflicts
