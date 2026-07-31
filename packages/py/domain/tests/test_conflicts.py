from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    ConflictMergeError,
    GovernedProposal,
    ProposalConflictResolution,
    build_conflict_merge_proposal,
    detect_proposal_conflicts,
)


def governed(
    proposal_id: str,
    *,
    entity_id: str = "entity-1",
    path: str = "/description",
    after: object = "value",
    status: str = "human-review",
) -> GovernedProposal:
    return GovernedProposal(
        proposal=AgentProposal(
            id=proposal_id,
            entity_id=entity_id,
            proposal_type="content",
            operations=[
                ChangeOperation(
                    operation="replace",
                    path=path,
                    before="before",
                    after=after,
                    citation_ids=["cite-1"],
                    confidence=0.98,
                )
            ],
            risk="medium",
            status=status,
            agent_run_id="agent-run-1",
            impact={"entityCount": 1},
        )
    )


def test_detects_overlapping_incompatible_paths_only() -> None:
    conflicts = detect_proposal_conflicts(
        [
            governed("proposal-a", path="/description"),
            governed("proposal-b", path="/description/0", after="other"),
            governed("proposal-identical", after="value"),
            governed("proposal-other-entity", entity_id="entity-2"),
            governed("proposal-rejected", after="ignored", status="rejected"),
        ]
    )
    assert len(conflicts) == 2
    assert {tuple(item.proposal_ids) for item in conflicts} == {
        ("proposal-a", "proposal-b"),
        ("proposal-b", "proposal-identical"),
    }
    assert all(item.paths == ["/description/0"] for item in conflicts)


def test_builds_deterministic_merge_from_path_choices_and_preserves_other_edits() -> None:
    left = governed("proposal-a", after="候选 A")
    left.proposal.operations.append(
        ChangeOperation(
            operation="add",
            path="/aliases",
            after=["别名 A"],
            citation_ids=["cite-left-alias"],
            confidence=0.96,
        )
    )
    right = governed("proposal-b", after="候选 B")
    right.proposal.operations.append(
        ChangeOperation(
            operation="add",
            path="/relationships",
            after=[{"typeId": "rel-related"}],
            citation_ids=["cite-right-relation"],
            confidence=0.97,
        )
    )
    conflict = detect_proposal_conflicts([left, right])[0]

    merged = build_conflict_merge_proposal(
        conflict,
        [right, left],
        proposal_id="proposal-merge-a-b",
        agent_run_id="run-human-merge-a-b",
        resolutions=[
            ProposalConflictResolution(
                path="/description",
                proposal_id="proposal-b",
            )
        ],
    )

    assert merged.proposal_type == "merge"
    assert merged.status == "proposed"
    assert [operation.path for operation in merged.operations] == [
        "/aliases",
        "/description",
        "/relationships",
    ]
    assert next(
        operation.after
        for operation in merged.operations
        if operation.path == "/description"
    ) == "候选 B"
    assert {
        citation
        for operation in merged.operations
        for citation in operation.citation_ids
    } == {"cite-left-alias", "cite-1", "cite-right-relation"}

    try:
        build_conflict_merge_proposal(
            conflict,
            [left, right],
            proposal_id="proposal-invalid-merge",
            agent_run_id="run-invalid-merge",
            resolutions=[],
        )
    except ConflictMergeError as error:
        assert str(error) == "every conflict path requires one resolution"
    else:
        raise AssertionError("a merge must resolve every conflict path")
