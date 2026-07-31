import logging
import os
import time
from collections.abc import Callable

from .tasks import (
    dispatch_accepted_proposals,
    dispatch_agent_schedules,
    dispatch_governance_proposals,
    dispatch_maintenance_work,
    dispatch_published_releases,
    dispatch_quality_maintenance,
    dispatch_source_acquisitions,
    dispatch_source_extractions,
    dispatch_source_snapshots,
)

logger = logging.getLogger("hardatlas.outbox-dispatcher")


def poll_outbox_once(
    batch_size: int,
    *,
    enqueue_source_dispatch: Callable[[int], object] | None = None,
    enqueue_snapshot_dispatch: Callable[[int], object] | None = None,
    enqueue_extraction_dispatch: Callable[[int], object] | None = None,
    enqueue_quality_dispatch: Callable[[int], object] | None = None,
    enqueue_agent_dispatch: Callable[[int], object] | None = None,
    enqueue_maintenance_work_dispatch: Callable[[int], object] | None = None,
    enqueue_governance_dispatch: Callable[[int], object] | None = None,
    enqueue_accepted_dispatch: Callable[[int], object] | None = None,
    enqueue_release_dispatch: Callable[[int], object] | None = None,
) -> dict[str, object]:
    dispatchers = (
        (
            "source.acquisition.requested",
            enqueue_source_dispatch or dispatch_source_acquisitions.send,
        ),
        (
            "source.snapshot.captured",
            enqueue_snapshot_dispatch or dispatch_source_snapshots.send,
        ),
        (
            "source.extraction.completed",
            enqueue_extraction_dispatch or dispatch_source_extractions.send,
        ),
        (
            "quality.maintenance.requested",
            enqueue_quality_dispatch or dispatch_quality_maintenance.send,
        ),
        (
            "agent.graph.scheduled",
            enqueue_agent_dispatch or dispatch_agent_schedules.send,
        ),
        (
            "maintenance.work.requested",
            enqueue_maintenance_work_dispatch or dispatch_maintenance_work.send,
        ),
        (
            "governance.proposal.created",
            enqueue_governance_dispatch or dispatch_governance_proposals.send,
        ),
        (
            "governance.proposal.accepted",
            enqueue_accepted_dispatch or dispatch_accepted_proposals.send,
        ),
        (
            "release.published",
            enqueue_release_dispatch or dispatch_published_releases.send,
        ),
    )
    enqueued: list[str] = []
    failed: dict[str, str] = {}
    for topic, enqueue in dispatchers:
        try:
            enqueue(batch_size)
            enqueued.append(topic)
        except Exception as error:
            failed[topic] = f"{type(error).__name__}: {error}"
            logger.exception("failed to enqueue outbox dispatcher for %s", topic)
    return {
        "enqueued": enqueued,
        "failed": failed,
    }


def main() -> None:
    interval_seconds = max(
        float(os.getenv("HARDATLAS_OUTBOX_POLL_SECONDS", "2")),
        0.25,
    )
    batch_size = max(
        int(os.getenv("HARDATLAS_OUTBOX_BATCH_SIZE", "100")),
        1,
    )
    while True:
        poll_outbox_once(batch_size)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
