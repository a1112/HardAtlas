from hardatlas_worker.dispatcher import poll_outbox_once


def test_dispatcher_enqueues_all_supported_outbox_topics() -> None:
    calls: list[tuple[str, int]] = []

    result = poll_outbox_once(
        37,
        enqueue_source_dispatch=lambda limit: calls.append(("source", limit)),
        enqueue_snapshot_dispatch=lambda limit: calls.append(("snapshot", limit)),
        enqueue_extraction_dispatch=lambda limit: calls.append(("extraction", limit)),
        enqueue_quality_dispatch=lambda limit: calls.append(("quality", limit)),
        enqueue_agent_dispatch=lambda limit: calls.append(("agent", limit)),
        enqueue_maintenance_work_dispatch=lambda limit: calls.append(("maintenance-work", limit)),
        enqueue_governance_dispatch=lambda limit: calls.append(("governance", limit)),
        enqueue_accepted_dispatch=lambda limit: calls.append(("accepted", limit)),
        enqueue_release_dispatch=lambda limit: calls.append(("release", limit)),
    )

    assert calls == [
        ("source", 37),
        ("snapshot", 37),
        ("extraction", 37),
        ("quality", 37),
        ("agent", 37),
        ("maintenance-work", 37),
        ("governance", 37),
        ("accepted", 37),
        ("release", 37),
    ]
    assert result == {
        "enqueued": [
            "source.acquisition.requested",
            "source.snapshot.captured",
            "source.extraction.completed",
            "quality.maintenance.requested",
            "agent.graph.scheduled",
            "maintenance.work.requested",
            "governance.proposal.created",
            "governance.proposal.accepted",
            "release.published",
        ],
        "failed": {},
    }


def test_dispatcher_isolates_one_topic_enqueue_failure() -> None:
    calls: list[tuple[str, int]] = []

    def unavailable_source_broker(_limit: int) -> None:
        raise ConnectionError("source queue unavailable")

    result = poll_outbox_once(
        10,
        enqueue_source_dispatch=unavailable_source_broker,
        enqueue_snapshot_dispatch=lambda limit: calls.append(("snapshot", limit)),
        enqueue_extraction_dispatch=lambda limit: calls.append(("extraction", limit)),
        enqueue_quality_dispatch=lambda limit: calls.append(("quality", limit)),
        enqueue_agent_dispatch=lambda limit: calls.append(("agent", limit)),
        enqueue_maintenance_work_dispatch=lambda limit: calls.append(("maintenance-work", limit)),
        enqueue_governance_dispatch=lambda limit: calls.append(("governance", limit)),
        enqueue_accepted_dispatch=lambda limit: calls.append(("accepted", limit)),
        enqueue_release_dispatch=lambda limit: calls.append(("release", limit)),
    )

    assert calls == [
        ("snapshot", 10),
        ("extraction", 10),
        ("quality", 10),
        ("agent", 10),
        ("maintenance-work", 10),
        ("governance", 10),
        ("accepted", 10),
        ("release", 10),
    ]
    assert result["enqueued"] == [
        "source.snapshot.captured",
        "source.extraction.completed",
        "quality.maintenance.requested",
        "agent.graph.scheduled",
        "maintenance.work.requested",
        "governance.proposal.created",
        "governance.proposal.accepted",
        "release.published",
    ]
    assert result["failed"] == {
        "source.acquisition.requested": ("ConnectionError: source queue unavailable")
    }
