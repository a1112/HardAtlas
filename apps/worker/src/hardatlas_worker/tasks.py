import hashlib
import glob
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from hardatlas_ai import (
    AgentDefinition,
    AgentGraphRunner,
    AgentGraphSpec,
    AgentRegistry,
    GraphRun,
    GraphRunSchedule,
    ModelGateway,
    ModelGatewayConfig,
    OpenAICompatibleModelGateway,
    core_handlers,
    load_agent_pack,
)
from hardatlas_data import (
    KnowledgeRepository,
    OpenSearchBackend,
    OutboxRecord,
    ProposalConcurrencyError,
    QualityMaintenanceService,
    QualityScanResult,
    ReleaseOrchestrator,
    ReleaseVerifier,
    SearchBackend,
    SearchPublicationService,
)
from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    EntityResolutionMatch,
    ExtractionBatch,
    ExtractionCandidate,
    GovernanceService,
    GovernedProposal,
    MaintenanceEvidenceLink,
    MaintenanceOutputLink,
    MaintenanceWorkItem,
    Principal,
    QualityProfile,
    ReleaseManifest,
    SourceAcquisitionJob,
    SourceDefinition,
    build_evidence_backed_maintenance_proposal,
    detect_proposal_conflicts,
    evaluate_source_policy,
    maintenance_work_item_from_triage,
    read_entity_operation_value,
)
from hardatlas_ingestion import (
    ParserRegistry,
    S3SnapshotStore,
    SnapshotStore,
    acquire_http_source,
    extract_snapshot,
    load_parser_registry,
    normalize_label,
)
from sqlalchemy import create_engine

dramatiq.set_broker(
    RedisBroker(
        url=os.getenv("HARDATLAS_REDIS_URL", "redis://localhost:6379/0"),
    )
)


class RepositoryCheckpointStore:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def load(self, run_id: str) -> GraphRun | None:
        document = self.repository.get_agent_graph_run(run_id)
        return GraphRun.model_validate(document) if document else None

    def save(self, run: GraphRun) -> None:
        self.repository.save_agent_graph_run(
            run_id=run.id,
            graph_id=run.graph_id,
            status=run.status,
            document=run.model_dump(mode="json", by_alias=True),
        )


WORKER_PRINCIPAL = Principal(
    subject="atlas-worker",
    display_name="Atlas Worker",
    roles=["agent-runner"],
    authentication_method="system",
)
SOURCE_ACQUISITION_AGENT_ID = "agent-source-acquisition-router@1.0.0"


def _resolve_pack_directories(configured_paths: str, kind: str) -> list[Path]:
    roots: list[Path] = []
    for configured_path in configured_paths.split(","):
        value = configured_path.strip()
        if not value:
            continue
        is_glob = any(token in value for token in ("*", "?", "["))
        if is_glob:
            candidates = sorted(glob.glob(value, recursive=True))
        elif Path(value).is_absolute():
            candidates = [value]
        else:
            candidates = [str(Path.cwd() / Path(value))]
        if not candidates:
            raise ValueError(f"{kind} pack path has no matches: {configured_path}")
        for candidate in candidates:
            candidate_path = Path(candidate)
            if not candidate_path.exists():
                raise ValueError(f"{kind} pack path does not exist: {candidate_path}")
            if not candidate_path.is_dir():
                raise ValueError(f"{kind} pack path must be a directory: {candidate_path}")
            roots.append(candidate_path)
    if not roots:
        raise ValueError(f"at least one {kind} pack path must be configured")
    return roots


def build_agent_registry(configured_paths: str) -> AgentRegistry:
    registry = AgentRegistry()
    try:
        roots = _resolve_pack_directories(configured_paths, kind="agent")
    except Exception as error:
        raise ValueError(
            f"failed to resolve agent pack paths from {configured_paths}: {error}"
        ) from error
    for root in roots:
        try:
            pack = load_agent_pack(root)
        except Exception as error:
            raise ValueError(f"failed to load agent pack {root}: {error}") from error
        for definition in pack.definitions:
            registry.register_definition(definition)
        for graph in pack.graphs:
            registry.register_graph(graph)
    if not registry.graphs:
        raise ValueError("at least one agent graph must be registered")
    return registry


def build_parser_registry(configured_paths: str) -> ParserRegistry:
    try:
        registry = load_parser_registry(configured_paths)
    except Exception as error:
        raise ValueError(
            "failed to build parser registry from "
            f"HARDATLAS_PARSER_PATHS={configured_paths}: {error}"
        ) from error
    if not registry.list():
        raise ValueError("at least one extraction parser must be registered")
    return registry


def resolve_schedule_registry(
    repository: KnowledgeRepository,
    schedule: GraphRunSchedule,
    configured_paths: str,
) -> tuple[AgentRegistry, AgentGraphSpec]:
    configured = build_agent_registry(configured_paths)
    current = configured.graphs.get(schedule.graph_id)
    if current and current.version == schedule.graph_version:
        return configured, current

    graph_document = repository.get_schema_document(
        document_id=schedule.graph_id,
        kind="agent-graph",
        schema_version=schedule.graph_version,
    )
    if graph_document is None:
        raise ValueError(
            f"scheduled graph version is unavailable: {schedule.graph_id}@{schedule.graph_version}"
        )
    graph = AgentGraphSpec.model_validate(graph_document)
    definitions: list[AgentDefinition] = []
    for node in graph.nodes:
        if not node.agent_id:
            continue
        document = repository.get_schema_document(
            document_id=node.agent_id,
            kind="agent-definition",
            schema_version=node.agent_version,
        )
        if document is None:
            raise ValueError(
                f"scheduled agent definition is unavailable: "
                f"{node.agent_id}@{node.agent_version or 'latest'}"
            )
        definitions.append(AgentDefinition.model_validate(document))
    registry = AgentRegistry(
        definitions=definitions,
        graphs=[graph],
    )
    return registry, graph


def _proposal_from_run(
    schedule: GraphRunSchedule,
    run: GraphRun,
    proposal_id: str,
) -> GovernedProposal:
    input_document = schedule.input
    proposal_node = next(node for node in run.nodes.values() if proposal_id in node.proposal_ids)
    proposal_output = proposal_node.output
    citation_id = str(
        proposal_output.get(
            "citationId",
            input_document["citationId"],
        )
    )
    citation = input_document.get("citation")
    field_path = str(
        proposal_output.get(
            "fieldPath",
            input_document["fieldPath"],
        )
    )
    proposed_value = proposal_output.get(
        "proposedValue",
        input_document["proposedValue"],
    )
    if schedule.graph_version == "graph-2.2.0" and "currentValuePresent" not in input_document:
        return GovernedProposal(
            proposal=AgentProposal(
                id=proposal_id,
                entity_id=str(
                    proposal_output.get(
                        "entityId",
                        input_document["entityId"],
                    )
                ),
                proposal_type="content",
                operations=[
                    ChangeOperation(
                        operation="replace",
                        path=field_path,
                        before=None,
                        after=proposed_value,
                        citation_ids=[citation_id],
                        confidence=float(
                            proposal_output.get(
                                "confidence",
                                input_document["confidence"],
                            )
                        ),
                    )
                ],
                risk=input_document.get("risk", "medium"),
                status="proposed",
                agent_run_id=run.id,
                impact={"entityCount": 1},
            )
        )
    current_value_present = input_document.get("currentValuePresent") is True
    return GovernedProposal(
        proposal=build_evidence_backed_maintenance_proposal(
            proposal_id=proposal_id,
            entity_id=str(proposal_output.get("entityId", input_document["entityId"])),
            agent_run_id=run.id,
            field_path=field_path,
            proposed_value=proposed_value,
            citation_id=citation_id,
            confidence=float(
                proposal_output.get(
                    "confidence",
                    input_document["confidence"],
                )
            ),
            risk=input_document.get("risk", "medium"),
            current_value_present=current_value_present,
            current_value=input_document.get("currentValue"),
            citation=citation if isinstance(citation, dict) else None,
            citation_already_present=(input_document.get("citationAlreadyPresent") is True),
        )
    )


def _update_schedule(
    repository: KnowledgeRepository,
    schedule: GraphRunSchedule,
    *,
    status: str,
    run_id: str | None = None,
    error: str | None = None,
) -> GraphRunSchedule:
    updated = schedule.model_copy(
        update={
            "status": status,
            "run_id": run_id if run_id is not None else schedule.run_id,
            "error": error,
            "updated_at": datetime.now(UTC),
        }
    )
    repository.save_agent_graph_schedule(updated)
    return updated


def execute_agent_schedule_once(
    repository: KnowledgeRepository,
    registry: AgentRegistry,
    schedule_id: str,
    model_gateway: ModelGateway | None = None,
) -> GraphRunSchedule:
    schedule = repository.get_agent_graph_schedule(schedule_id)
    if schedule is None:
        raise ValueError("agent graph schedule not found")
    if schedule.status in {"completed", "canceled"}:
        return schedule
    if schedule.status not in {"queued", "failed", "dispatched", "running"}:
        raise ValueError(f"schedule cannot be executed from status {schedule.status}")

    graph = registry.graphs.get(schedule.graph_id)
    if graph is None or graph.version != schedule.graph_version:
        raise ValueError(
            f"registry does not contain scheduled graph "
            f"{schedule.graph_id}@{schedule.graph_version}"
        )

    if schedule.status in {"queued", "failed"}:
        schedule = _update_schedule(repository, schedule, status="dispatched")
    if schedule.status == "dispatched":
        schedule = _update_schedule(repository, schedule, status="running")

    run_id = schedule.run_id or f"run-{schedule.id}"
    try:
        run = AgentGraphRunner(
            checkpoints=RepositoryCheckpointStore(repository),
            handlers=core_handlers(model_gateway),
            registry=registry,
        ).run(
            spec=graph,
            run_id=run_id,
            input=schedule.input,
        )
        if run.status != "completed":
            raise RuntimeError("agent graph did not complete")
        for proposal_id in run.proposal_ids:
            proposal_node = next(
                node for node in graph.nodes if proposal_id in run.nodes[node.id].proposal_ids
            )
            registry.validate_proposal(
                proposal_node,
                proposal_type="content",
                risk=schedule.input.get("risk", "medium"),
            )
            if repository.get_governed_proposal(proposal_id) is None:
                repository.save_governed_proposal(_proposal_from_run(schedule, run, proposal_id))
        work_item: MaintenanceWorkItem | None = None
        completed_schedule = schedule.model_copy(
            update={
                "status": "completed",
                "run_id": run.id,
                "error": None,
                "updated_at": datetime.now(UTC),
            }
        )
        if graph.id == "quality-maintenance-triage":
            if run.proposal_ids:
                raise RuntimeError("quality triage may not create governed proposals")
            task_id = str(schedule.input["maintenanceTaskId"])
            task = repository.get_maintenance_task(task_id)
            if task is None:
                raise ValueError("maintenance task not found after triage")
            triage_output = run.nodes["triage"].output
            if (
                triage_output.get("requiresEvidence") is not True
                or triage_output.get("proposalEligible") is not False
            ):
                raise ValueError("quality triage output violates evidence boundary")
            candidate = maintenance_work_item_from_triage(
                task,
                route=str(triage_output["route"]),
                triage_schedule_id=schedule.id,
                triage_run_id=run.id,
            )
            schedule, work_item = repository.complete_quality_triage_schedule(
                completed_schedule,
                candidate,
            )
        else:
            schedule = _update_schedule(
                repository,
                schedule,
                status="completed",
                run_id=run.id,
            )
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="agent.schedule.execute",
            resource_type="agent-graph-schedule",
            resource_id=schedule.id,
            outcome="success",
            request_id=f"worker-{uuid4()}",
            metadata={
                "graphId": graph.id,
                "graphVersion": graph.version,
                "runId": run.id,
                "proposalCount": len(run.proposal_ids),
                "maintenanceWorkItemId": (work_item.id if work_item is not None else None),
                "maintenanceRoute": (work_item.route if work_item is not None else None),
                "usage": run.usage.model_dump(mode="json", by_alias=True),
            },
        )
        return schedule
    except Exception as error:
        latest = repository.get_agent_graph_schedule(schedule.id) or schedule
        if latest.status not in {"completed", "canceled"}:
            _update_schedule(
                repository,
                latest,
                status="failed",
                run_id=run_id,
                error=f"{type(error).__name__}: {error}",
            )
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="agent.schedule.execute",
            resource_type="agent-graph-schedule",
            resource_id=schedule.id,
            outcome="failed",
            request_id=f"worker-{uuid4()}",
            metadata={
                "graphId": graph.id,
                "graphVersion": graph.version,
                "error": f"{type(error).__name__}: {error}",
            },
        )
        raise


def dispatch_agent_schedule_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events: list[OutboxRecord] = repository.pending_outbox_records(
        topic="agent.graph.scheduled",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        schedule_id = str(event.payload.get("scheduleId") or event.aggregate_id)
        try:
            enqueue(schedule_id)
        except Exception as error:
            repository.mark_outbox_failed([event.id], f"{type(error).__name__}: {error}")
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


def schedule_quality_maintenance_task_once(
    repository: KnowledgeRepository,
    graph: AgentGraphSpec,
    task_id: str,
) -> GraphRunSchedule | None:
    if graph.id != "quality-maintenance-triage":
        raise ValueError("quality tasks require the safety triage graph")
    if "schedule" not in graph.trigger_types:
        raise ValueError("quality triage graph does not allow scheduled runs")
    task = repository.get_maintenance_task(task_id)
    if task is None:
        raise ValueError("maintenance task not found")
    if task.status in {"resolved", "superseded"}:
        return None

    identity = hashlib.sha256(
        (f"{task.id}\x1f{task.revision_id}\x1f{graph.id}\x1f{graph.version}").encode()
    ).hexdigest()
    schedule = GraphRunSchedule(
        id=f"schedule-quality-{identity[:24]}",
        graph_id=graph.id,
        graph_version=graph.version,
        trigger_type="schedule",
        input={
            "maintenanceTaskId": task.id,
            "assessmentId": task.assessment_id,
            "entityId": task.entity.id,
            "entityTypeId": task.entity.type_id,
            "currentRevisionId": task.revision_id,
            "profileId": task.profile_id,
            "profileVersion": task.profile_version,
            "issueCode": task.issue.code,
            "issuePath": task.issue.path,
            "action": task.action,
        },
        requested_by=(f"quality-maintenance:{task.profile_id}@{task.profile_version}"),
        idempotency_key=f"quality-maintenance-{identity}",
        budget=graph.budget,
    )
    _, persisted = repository.schedule_maintenance_task(task.id, schedule)
    return persisted


def dispatch_quality_maintenance_events(
    repository: KnowledgeRepository,
    graph: AgentGraphSpec,
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="quality.maintenance.requested",
        limit=limit,
    )
    scheduled = 0
    skipped = 0
    failed = 0
    for event in events:
        task_id = str(event.payload.get("taskId") or "")
        if not task_id:
            repository.mark_outbox_failed(
                [event.id],
                "ValueError: quality maintenance event has no taskId",
            )
            failed += 1
            continue
        try:
            schedule = schedule_quality_maintenance_task_once(
                repository,
                graph,
                task_id,
            )
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        if schedule is None:
            skipped += 1
        else:
            scheduled += 1
    return {
        "scheduled": scheduled,
        "skipped": skipped,
        "failed": failed,
    }


def activate_maintenance_work_once(
    repository: KnowledgeRepository,
    work_item_id: str,
) -> MaintenanceWorkItem:
    work_item = repository.activate_maintenance_work_item(work_item_id)
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="maintenance.work.activate",
        resource_type="maintenance-work-item",
        resource_id=work_item.id,
        outcome="success",
        request_id=f"worker-{uuid4()}",
        metadata={
            "status": work_item.status,
            "route": work_item.route,
            "entityId": work_item.entity.id,
            "revisionId": work_item.revision_id,
        },
    )
    return work_item


def dispatch_maintenance_work_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="maintenance.work.requested",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        work_item_id = str(event.payload.get("workItemId") or event.aggregate_id)
        try:
            enqueue(work_item_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


def _maintenance_target_locale(work_item: MaintenanceWorkItem) -> str | None:
    prefix = "/locales/"
    if not work_item.issue.path.startswith(prefix):
        return None
    locale = work_item.issue.path.removeprefix(prefix).split("/", 1)[0].strip()
    return locale or None


def _source_scope_rank(
    source: SourceDefinition,
    work_item: MaintenanceWorkItem,
    *,
    entity_taxonomy_node_ids: set[str],
) -> tuple[int, int, int, int] | None:
    if source.entity_type_ids and work_item.entity.type_id not in source.entity_type_ids:
        return None
    if source.taxonomy_node_ids and not (set(source.taxonomy_node_ids) & entity_taxonomy_node_ids):
        return None
    target_locale = _maintenance_target_locale(work_item)
    if target_locale and source.locales and target_locale not in source.locales:
        return None
    trust_rank = {
        "primary": 4,
        "authoritative": 3,
        "secondary": 2,
        "community": 1,
    }[source.trust_tier]
    return (
        int(bool(source.entity_type_ids)),
        int(bool(source.taxonomy_node_ids)),
        int(bool(target_locale and source.locales)),
        trust_rank,
    )


def request_source_acquisition_for_work_once(
    repository: KnowledgeRepository,
    work_item_id: str,
    *,
    requested_at: datetime | None = None,
) -> tuple[MaintenanceWorkItem, SourceAcquisitionJob | None]:
    """Route one revision-pinned work item into the governed source pipeline."""

    now = requested_at or datetime.now(UTC)
    work_item = repository.get_maintenance_work_item(work_item_id)
    if work_item is None:
        raise ValueError("maintenance work item not found")
    if work_item.status == "queued":
        work_item = repository.activate_maintenance_work_item(
            work_item.id,
            activated_at=now,
        )
    if work_item.status in {"blocked", "completed", "superseded"}:
        return work_item, None
    if work_item.route != "source-acquisition":
        raise ValueError("maintenance work item is not a source-acquisition route")
    work_item = repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id=SOURCE_ACQUISITION_AGENT_ID,
        lease_seconds=3600,
        claimed_at=now,
    )
    if work_item.status == "superseded":
        return work_item, None
    assert work_item.lease_token is not None

    entity = repository.get_entity_by_id(work_item.entity.id)
    if entity is None:
        blocked = repository.block_maintenance_work_item(
            work_item.id,
            assignee_id=SOURCE_ACQUISITION_AGENT_ID,
            lease_token=work_item.lease_token,
            reason="固定修订对应的百科条目不存在，无法选择采集来源",
            blocked_at=now,
        )
        return blocked, None

    ranked_sources: list[tuple[tuple[int, int, int, int], SourceDefinition]] = []
    for source in repository.list_source_definitions():
        decision = evaluate_source_policy(source)
        if not decision.allowed:
            continue
        rank = _source_scope_rank(
            source,
            work_item,
            entity_taxonomy_node_ids=set(entity.taxonomy_node_ids),
        )
        if rank is not None:
            ranked_sources.append((rank, source))

    if not ranked_sources:
        blocked = repository.block_maintenance_work_item(
            work_item.id,
            assignee_id=SOURCE_ACQUISITION_AGENT_ID,
            lease_token=work_item.lease_token,
            reason=("来源注册表中没有许可、robots 策略和领域范围均满足要求的活动来源"),
            blocked_at=now,
        )
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="maintenance.source.route",
            resource_type="maintenance-work-item",
            resource_id=work_item.id,
            outcome="denied",
            request_id=f"worker-{uuid4()}",
            metadata={"reason": blocked.blocked_reason},
        )
        return blocked, None

    ranked_sources.sort(key=lambda item: (item[0], item[1].id), reverse=True)
    best_rank = ranked_sources[0][0]
    best_sources = [source for rank, source in ranked_sources if rank == best_rank]
    if len(best_sources) != 1:
        source_ids = sorted(source.id for source in best_sources)
        blocked = repository.block_maintenance_work_item(
            work_item.id,
            assignee_id=SOURCE_ACQUISITION_AGENT_ID,
            lease_token=work_item.lease_token,
            reason=("存在多个同优先级合规来源，需要人工确定来源策略：" + "、".join(source_ids)),
            blocked_at=now,
        )
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="maintenance.source.route",
            resource_type="maintenance-work-item",
            resource_id=work_item.id,
            outcome="denied",
            request_id=f"worker-{uuid4()}",
            metadata={"candidateSourceIds": source_ids},
        )
        return blocked, None

    source = best_sources[0]
    identity = hashlib.sha256(
        (f"{work_item.id}\x1f{work_item.revision_id}\x1f{source.id}\x1f{source.version}").encode()
    ).hexdigest()
    idempotency_key = f"maintenance-source:{identity}"
    job = repository.get_source_acquisition_job_by_idempotency(idempotency_key)
    if job is None:
        candidate = SourceAcquisitionJob(
            id=f"acquisition-maintenance-{identity[:24]}",
            source_id=source.id,
            source_version=source.version,
            url=source.base_url,
            requested_by=SOURCE_ACQUISITION_AGENT_ID,
            idempotency_key=idempotency_key,
            maintenance_work_item_id=work_item.id,
            created_at=now,
            updated_at=now,
        )
        try:
            repository.create_source_acquisition_job(candidate)
            job = candidate
        except ValueError:
            job = repository.get_source_acquisition_job_by_idempotency(idempotency_key)
            if job is None:
                raise
    if job.maintenance_work_item_id != work_item.id:
        raise ValueError("source acquisition idempotency identity is inconsistent")

    released = repository.release_maintenance_work_item(
        work_item.id,
        assignee_id=SOURCE_ACQUISITION_AGENT_ID,
        lease_token=work_item.lease_token,
        released_at=now,
    )
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="maintenance.source.route",
        resource_type="maintenance-work-item",
        resource_id=work_item.id,
        outcome="success",
        request_id=f"worker-{uuid4()}",
        metadata={
            "sourceId": source.id,
            "sourceVersion": source.version,
            "acquisitionJobId": job.id,
            "revisionId": work_item.revision_id,
        },
    )
    if job.status == "completed":
        reconciled = reconcile_source_acquisition_work_once(
            repository,
            job,
            reconciled_at=now,
        )
        return reconciled or released, job
    return released, job


def reconcile_source_acquisition_work_once(
    repository: KnowledgeRepository,
    job: SourceAcquisitionJob,
    *,
    reconciled_at: datetime | None = None,
) -> MaintenanceWorkItem | None:
    """Complete only the linked source work after its durable snapshot exists."""

    if job.maintenance_work_item_id is None:
        return None
    if job.status != "completed" or not job.snapshot_id:
        return repository.get_maintenance_work_item(job.maintenance_work_item_id)
    now = reconciled_at or datetime.now(UTC)
    work_item = repository.get_maintenance_work_item(job.maintenance_work_item_id)
    if work_item is None:
        raise ValueError("linked maintenance work item not found")
    if work_item.status in {"completed", "blocked", "superseded"}:
        return work_item
    if work_item.status == "queued":
        work_item = repository.activate_maintenance_work_item(
            work_item.id,
            activated_at=now,
        )
    if work_item.status == "superseded":
        return work_item
    if work_item.status == "claimed":
        if work_item.assignee_id != SOURCE_ACQUISITION_AGENT_ID:
            raise ValueError("linked maintenance work item is leased by another assignee")
        assert work_item.lease_token is not None
        assert work_item.lease_expires_at is not None
        if work_item.lease_expires_at <= now:
            work_item = repository.claim_maintenance_work_item(
                work_item.id,
                assignee_id=SOURCE_ACQUISITION_AGENT_ID,
                lease_seconds=300,
                claimed_at=now,
            )
    else:
        work_item = repository.claim_maintenance_work_item(
            work_item.id,
            assignee_id=SOURCE_ACQUISITION_AGENT_ID,
            lease_seconds=300,
            claimed_at=now,
        )
    if work_item.status == "superseded":
        return work_item
    assert work_item.lease_token is not None
    completed = repository.complete_maintenance_work_item(
        work_item.id,
        assignee_id=SOURCE_ACQUISITION_AGENT_ID,
        lease_token=work_item.lease_token,
        evidence_refs=[
            MaintenanceEvidenceLink(
                kind="source-snapshot",
                id=job.snapshot_id,
            )
        ],
        output_refs=[
            MaintenanceOutputLink(
                kind="source-acquisition-job",
                id=job.id,
            )
        ],
        completed_at=now,
    )
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="maintenance.source.complete",
        resource_type="maintenance-work-item",
        resource_id=work_item.id,
        outcome=("success" if completed.status == "completed" else completed.status),
        request_id=f"worker-{uuid4()}",
        metadata={
            "sourceId": job.source_id,
            "sourceVersion": job.source_version,
            "acquisitionJobId": job.id,
            "snapshotId": job.snapshot_id,
            "revisionId": work_item.revision_id,
        },
    )
    return completed


def evaluate_governed_proposal_once(
    repository: KnowledgeRepository,
    proposal_id: str,
    *,
    policy_version: str = "policy-1.0.0",
) -> GovernedProposal:
    current = repository.get_governed_proposal(proposal_id)
    if current is None:
        raise ValueError("governed proposal not found")
    if current.proposal.status != "proposed":
        return current

    governance = GovernanceService([], policy_version)
    governance.proposals = {proposal_id: current}
    evaluated = governance.evaluate(proposal_id)
    conflicts = [
        conflict
        for conflict in detect_proposal_conflicts(
            [
                evaluated if item.proposal.id == proposal_id else item
                for item in repository.list_governed_proposals()
            ]
        )
        if proposal_id in conflict.proposal_ids
    ]
    if evaluated.proposal.status == "policy-approved":
        if conflicts:
            assert evaluated.policy_evaluation is not None
            evaluated.policy_evaluation.outcome = "human-review"
            evaluated.policy_evaluation.required_approvals = 1
            evaluated.policy_evaluation.reasons.append(
                "active overlapping proposal requires conflict resolution"
            )
            evaluated.proposal.status = "human-review"
        else:
            evaluated = governance.accept_policy_approved(proposal_id)

    try:
        repository.save_governed_proposal(evaluated)
    except ProposalConcurrencyError:
        latest = repository.get_governed_proposal(proposal_id)
        if latest is not None and latest.proposal.status != "proposed":
            return latest
        raise
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="proposal.policy.evaluate",
        resource_type="proposal",
        resource_id=proposal_id,
        outcome="success",
        request_id=f"worker-{uuid4()}",
        metadata={
            "policyVersion": policy_version,
            "resultStatus": evaluated.proposal.status,
            "proposalVersion": evaluated.version,
            "conflictIds": [conflict.id for conflict in conflicts],
            "automatic": True,
        },
    )
    return evaluated


def dispatch_governance_proposal_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="governance.proposal.created",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        proposal_id = str(event.payload.get("proposalId") or event.aggregate_id)
        try:
            enqueue(proposal_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


AUTO_RELEASE_PROPOSAL_TYPES = {
    "content",
    "relation",
    "translation",
    "merge",
}


def plan_and_publish_accepted_proposals(
    repository: KnowledgeRepository,
    search_backend: SearchBackend,
    trigger_proposal_id: str,
    *,
    default_schema_version: str,
    batch_limit: int = 50,
) -> ReleaseManifest | None:
    trigger = repository.get_governed_proposal(trigger_proposal_id)
    if trigger is None:
        raise ValueError("accepted proposal not found")
    existing_releases = repository.list_releases()
    containing = next(
        (release for release in existing_releases if trigger_proposal_id in release.proposal_ids),
        None,
    )
    orchestrator = ReleaseOrchestrator(
        repository=repository,
        search_backend=search_backend,
    )
    if containing is not None:
        if containing.status in {"staged", "publishing", "published"}:
            return orchestrator.publish(containing.id).release
        return None
    if (
        trigger.proposal.status != "accepted"
        or trigger.proposal.entity_id is None
        or trigger.proposal.proposal_type not in AUTO_RELEASE_PROPOSAL_TYPES
    ):
        return None

    claimed_ids = {
        proposal_id
        for release in existing_releases
        for proposal_id in release.proposal_ids
        if release.status != "rolled-back"
    }
    policy_version = (
        trigger.policy_evaluation.policy_version if trigger.policy_evaluation else "policy-1.0.0"
    )
    eligible = [
        governed
        for governed in repository.list_governed_proposals()
        if governed.proposal.status == "accepted"
        and governed.proposal.entity_id is not None
        and governed.proposal.proposal_type in AUTO_RELEASE_PROPOSAL_TYPES
        and governed.proposal.id not in claimed_ids
        and (
            governed.policy_evaluation is None
            or governed.policy_evaluation.policy_version == policy_version
        )
    ]
    eligible.sort(key=lambda item: item.proposal.id)
    selected = eligible[: max(1, batch_limit)]
    if not any(item.proposal.id == trigger_proposal_id for item in selected):
        selected = [
            trigger,
            *[item for item in selected if item.proposal.id != trigger_proposal_id],
        ][: max(1, batch_limit)]

    conflicts = detect_proposal_conflicts(repository.list_governed_proposals())
    selected_ids = {item.proposal.id for item in selected}
    blocking = [
        conflict for conflict in conflicts if selected_ids.intersection(conflict.proposal_ids)
    ]
    if blocking:
        raise ValueError(
            "accepted proposal batch has active conflicts: "
            + ",".join(conflict.id for conflict in blocking)
        )

    identity = "|".join(f"{item.proposal.id}@{item.version}" for item in selected)
    digest = hashlib.sha256(identity.encode()).hexdigest()
    release_id = f"auto-release-{digest[:24]}"
    data_version = f"atlas-auto-{digest[:16]}"
    schema_versions = {default_schema_version}
    for governed in selected:
        entity_id = governed.proposal.entity_id
        if entity_id is None:
            continue
        entity = repository.get_entity_by_id(entity_id)
        if entity is not None:
            schema_versions.add(entity.revision.schema_version)
    previous_release = next(
        (release for release in existing_releases if release.status == "published"),
        None,
    )
    staged = orchestrator.stage(
        release_id=release_id,
        proposal_ids=[item.proposal.id for item in selected],
        data_version=data_version,
        schema_versions=sorted(schema_versions),
        policy_version=policy_version,
        previous_release_id=(previous_release.id if previous_release is not None else "none"),
        trigger="automatic",
        initiated_by=WORKER_PRINCIPAL.subject,
    )
    published = orchestrator.publish(staged.id).release
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="release.auto.publish",
        resource_type="release",
        resource_id=published.id,
        outcome="success",
        request_id=f"worker-{uuid4()}",
        metadata={
            "proposalIds": published.proposal_ids,
            "dataVersion": published.data_version,
            "searchIndex": published.search_index,
            "automatic": True,
        },
    )
    return published


def dispatch_accepted_proposal_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="governance.proposal.accepted",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        proposal_id = str(event.payload.get("proposalId") or event.aggregate_id)
        try:
            enqueue(proposal_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


def verify_published_release_once(
    repository: KnowledgeRepository,
    search_backend: SearchBackend,
    release_id: str,
    *,
    automatic_rollback_enabled: bool = True,
) -> ReleaseManifest:
    before = repository.get_release(release_id)
    verifier = ReleaseVerifier(
        repository=repository,
        search_backend=search_backend,
    )
    result = verifier.verify(release_id)
    release = result.release
    transitioned = before is None or before.verification.status not in {
        "passed",
        "failed",
        "superseded",
    }
    if transitioned:
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="release.verify",
            resource_type="release",
            resource_id=release.id,
            outcome=("failed" if release.verification.status == "failed" else "success"),
            request_id=f"worker-{uuid4()}",
            metadata={
                "verificationStatus": release.verification.status,
                "attempt": release.verification.attempt,
                "checks": [
                    check.model_dump(mode="json", by_alias=True)
                    for check in release.verification.checks
                ],
                "automaticRelease": release.trigger == "automatic",
            },
        )
    if not (result.should_rollback and automatic_rollback_enabled):
        return release

    failed_checks = [check for check in release.verification.checks if check.status == "failed"]
    reason = (
        "; ".join(f"{check.key}: {check.detail}" for check in failed_checks)
        or "automatic release verification failed"
    )
    if not release.verification.automatic_rollback:
        release = verifier.mark_automatic_rollback(
            release.id,
            reason=reason,
        )
    rolled_back = (
        ReleaseOrchestrator(
            repository=repository,
            search_backend=search_backend,
        )
        .rollback(release.id)
        .release
    )
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="release.auto.rollback",
        resource_type="release",
        resource_id=rolled_back.id,
        outcome="success",
        request_id=f"worker-{uuid4()}",
        metadata={
            "reason": reason,
            "rollbackSearchIndex": (rolled_back.rollback_search_index),
            "verificationAttempt": (rolled_back.verification.attempt),
        },
    )
    return rolled_back


def dispatch_release_published_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="release.published",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        release_id = str(event.payload.get("releaseId") or event.aggregate_id)
        try:
            enqueue(release_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


def extract_source_snapshot_once(
    repository: KnowledgeRepository,
    store: SnapshotStore,
    parser_registry: ParserRegistry,
    snapshot_id: str,
) -> tuple[ExtractionBatch, list[ExtractionCandidate]]:
    snapshot = repository.get_source_snapshot(snapshot_id)
    if snapshot is None:
        raise ValueError("source snapshot not found")
    source = repository.get_source_definition(
        snapshot.source_id,
        version=snapshot.source_version,
    )
    if source is None:
        raise ValueError("pinned source definition not found")
    parser = parser_registry.resolve_for_source(source)
    content = store.get(key=snapshot.storage_key)
    batch, candidates = extract_snapshot(
        parser,
        source=source,
        snapshot=snapshot,
        content=content,
    )
    repository.save_extraction_result(batch, candidates)
    return batch, candidates


def schedule_extraction_candidates(
    repository: KnowledgeRepository,
    graph: AgentGraphSpec,
    batch: ExtractionBatch,
    candidates: list[ExtractionCandidate],
) -> dict[str, int]:
    if "source-change" not in graph.trigger_types:
        raise ValueError("agent graph does not allow source-change schedules")
    scheduled = 0
    unresolved = 0
    empty = 0
    for candidate in candidates:
        if candidate.batch_id != batch.id:
            raise ValueError("candidate does not belong to extraction batch")
        if not candidate.entity_id:
            unresolved += 1
            continue
        current_entity = repository.get_entity_by_id(candidate.entity_id)
        if current_entity is None:
            raise ValueError(
                f"resolved extraction entity is no longer published: {candidate.entity_id}"
            )
        existing_citation = next(
            (
                citation
                for citation in current_entity.citations
                if citation.id == candidate.citation.id
            ),
            None,
        )
        schedule_ids: list[str] = []
        for field in candidate.fields:
            current_value_present, current_value = read_entity_operation_value(
                current_entity,
                field.target_path,
            )
            if current_value_present and current_value == field.proposed_value:
                continue
            schedule_hash = hashlib.sha256(
                f"{candidate.id}:{field.id}:{graph.id}:{graph.version}".encode()
            ).hexdigest()
            schedule_id = f"schedule-{schedule_hash[:24]}"
            idempotency_key = f"extraction:{candidate.id}:{field.id}:{graph.id}:{graph.version}"
            existing = repository.get_agent_graph_schedule_by_idempotency(idempotency_key)
            if existing is None:
                source = repository.get_source_definition(
                    candidate.source_id,
                    version=candidate.source_version,
                )
                model_processing_allowed = bool(
                    source and evaluate_source_policy(source).model_processing_allowed
                )
                repository.save_agent_graph_schedule(
                    GraphRunSchedule(
                        id=schedule_id,
                        graph_id=graph.id,
                        graph_version=graph.version,
                        trigger_type="source-change",
                        input={
                            "sourceId": candidate.source_id,
                            "snapshotHash": batch.content_sha256,
                            "snapshotId": candidate.snapshot_id,
                            "batchId": candidate.batch_id,
                            "candidateId": candidate.id,
                            "parserId": candidate.parser_id,
                            "parserVersion": candidate.parser_version,
                            "entityId": candidate.entity_id,
                            "entityTypeId": candidate.entity_type_id,
                            "label": candidate.labels[0].value,
                            "fieldPath": field.target_path,
                            "proposedValue": field.proposed_value,
                            "sourceExcerpt": (
                                field.proposed_value
                                if isinstance(field.proposed_value, str)
                                else ""
                            ),
                            "candidateEntityIds": [
                                match.entity_id for match in candidate.resolution_matches
                            ],
                            "modelProcessingAllowed": model_processing_allowed,
                            "citationId": field.citation_id,
                            "citation": candidate.citation.model_dump(
                                mode="json",
                                by_alias=True,
                            ),
                            "citationAlreadyPresent": (
                                existing_citation is not None
                                and existing_citation == candidate.citation
                            ),
                            "currentRevisionId": (current_entity.revision.revision_id),
                            "currentValuePresent": current_value_present,
                            "currentValue": current_value,
                            "confidence": field.confidence,
                            "risk": "low" if field.confidence >= 0.9 else "medium",
                        },
                        requested_by=(
                            f"source-parser:{candidate.parser_id}@{candidate.parser_version}"
                        ),
                        idempotency_key=idempotency_key,
                        budget=graph.budget,
                    )
                )
                scheduled += 1
                schedule_ids.append(schedule_id)
            else:
                schedule_ids.append(existing.id)
        if not schedule_ids:
            empty += 1
            continue
        repository.mark_extraction_candidate_scheduled(
            candidate.id,
            schedule_ids,
        )
    return {
        "scheduled": scheduled,
        "unresolved": unresolved,
        "empty": empty,
    }


def resolve_extraction_candidate_once(
    repository: KnowledgeRepository,
    candidate: ExtractionCandidate,
) -> ExtractionCandidate:
    entities = repository.list_entities(candidate.entity_type_id)
    if candidate.entity_id:
        explicit = next(
            (entity for entity in entities if entity.ref.id == candidate.entity_id),
            None,
        )
        matches = (
            [
                EntityResolutionMatch(
                    entity_id=explicit.ref.id,
                    slug=explicit.ref.slug,
                    canonical_name=explicit.ref.canonical_name,
                    match_basis="explicit-id",
                    score=1,
                )
            ]
            if explicit
            else []
        )
        return repository.record_extraction_resolution(
            candidate.id,
            matches,
            resolved_entity_id=explicit.ref.id if explicit else None,
        )

    normalized_labels = {
        normalize_label(label.value) for label in candidate.labels if label.value.strip()
    }
    best_by_entity: dict[str, EntityResolutionMatch] = {}
    for entity in entities:
        names = {
            normalize_label(entity.ref.canonical_name),
            *(normalize_label(name.value) for name in entity.names),
        }
        aliases = {normalize_label(alias.value) for alias in entity.aliases}
        basis: str | None = None
        score = 0.0
        if normalized_labels.intersection(names):
            basis = "exact-name"
            score = 1.0
        elif normalized_labels.intersection(aliases):
            basis = "exact-alias"
            score = 0.98
        if basis is not None:
            best_by_entity[entity.ref.id] = EntityResolutionMatch(
                entity_id=entity.ref.id,
                slug=entity.ref.slug,
                canonical_name=entity.ref.canonical_name,
                match_basis=basis,
                score=score,
            )
    matches = sorted(
        best_by_entity.values(),
        key=lambda match: (-match.score, match.entity_id),
    )
    resolved_entity_id = matches[0].entity_id if len(matches) == 1 else None
    return repository.record_extraction_resolution(
        candidate.id,
        matches,
        resolved_entity_id=resolved_entity_id,
    )


def _update_source_acquisition_job(
    repository: KnowledgeRepository,
    job: SourceAcquisitionJob,
    *,
    status: str,
    snapshot_id: str | None = None,
    error: str | None = None,
) -> SourceAcquisitionJob:
    updated = job.model_copy(
        update={
            "status": status,
            "snapshot_id": snapshot_id,
            "error": error,
            "updated_at": datetime.now(UTC),
        }
    )
    repository.save_source_acquisition_job(updated)
    return updated


def execute_source_acquisition_once(
    repository: KnowledgeRepository,
    store: SnapshotStore,
    job_id: str,
) -> SourceAcquisitionJob:
    job = repository.get_source_acquisition_job(job_id)
    if job is None:
        raise ValueError("source acquisition job not found")
    if job.status in {"completed", "canceled"}:
        if job.status == "completed":
            reconcile_source_acquisition_work_once(repository, job)
        return job
    if job.status not in {"queued", "failed", "dispatched", "running"}:
        raise ValueError(f"source acquisition cannot run from status {job.status}")
    source = repository.get_source_definition(
        job.source_id,
        version=job.source_version,
    )
    if source is None:
        raise ValueError("pinned source definition not found")
    if job.status in {"queued", "failed"}:
        job = _update_source_acquisition_job(
            repository,
            job,
            status="dispatched",
        )
    if job.status == "dispatched":
        job = _update_source_acquisition_job(
            repository,
            job,
            status="running",
        )
    try:
        result = acquire_http_source(source, url=job.url)
        store.put(
            key=result.snapshot.storage_key,
            content=result.content,
            media_type=result.snapshot.media_type,
            metadata={
                "sha256": result.snapshot.content_sha256,
                "source-id": source.id,
                "source-version": source.version,
                "license-id": source.license_id,
            },
        )
        repository.save_source_snapshot(result.snapshot)
        completed = _update_source_acquisition_job(
            repository,
            job,
            status="completed",
            snapshot_id=result.snapshot.id,
        )
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="source.acquisition.execute",
            resource_type="source-acquisition",
            resource_id=job.id,
            outcome="success",
            request_id=f"worker-{uuid4()}",
            metadata={
                "sourceId": source.id,
                "sourceVersion": source.version,
                "url": result.snapshot.url,
                "snapshotId": result.snapshot.id,
                "contentSha256": result.snapshot.content_sha256,
            },
        )
    except Exception as error:
        latest = repository.get_source_acquisition_job(job.id) or job
        if latest.status not in {"completed", "canceled"}:
            _update_source_acquisition_job(
                repository,
                latest,
                status="failed",
                error=f"{type(error).__name__}: {error}",
            )
        audit_url = latest.url or source.base_url
        repository.append_audit_event(
            actor=WORKER_PRINCIPAL,
            action="source.acquisition.execute",
            resource_type="source-acquisition",
            resource_id=job.id,
            outcome="failed",
            request_id=f"worker-{uuid4()}",
            metadata={
                "sourceId": source.id,
                "sourceVersion": source.version,
                "url": audit_url,
                "error": f"{type(error).__name__}: {error}",
            },
        )
        raise
    reconcile_source_acquisition_work_once(repository, completed)
    return completed


def dispatch_source_acquisition_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="source.acquisition.requested",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        job_id = str(event.payload.get("jobId") or event.aggregate_id)
        try:
            enqueue(job_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


def dispatch_source_snapshot_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="source.snapshot.captured",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    skipped = 0
    for event in events:
        snapshot_id = str(event.payload.get("snapshotId") or event.aggregate_id)
        snapshot = repository.get_source_snapshot(snapshot_id)
        source = (
            repository.get_source_definition(
                snapshot.source_id,
                version=snapshot.source_version,
            )
            if snapshot
            else None
        )
        if source is not None and not (source.parser_id and source.parser_version):
            repository.mark_outbox_published([event.id])
            skipped += 1
            continue
        try:
            enqueue(snapshot_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {
        "dispatched": dispatched,
        "failed": failed,
        "skipped": skipped,
    }


def dispatch_source_extraction_events(
    repository: KnowledgeRepository,
    enqueue: Callable[[str], object],
    *,
    limit: int = 100,
) -> dict[str, int]:
    events = repository.pending_outbox_records(
        topic="source.extraction.completed",
        limit=limit,
    )
    dispatched = 0
    failed = 0
    for event in events:
        batch_id = str(event.payload.get("batchId") or event.aggregate_id)
        try:
            enqueue(batch_id)
        except Exception as error:
            repository.mark_outbox_failed(
                [event.id],
                f"{type(error).__name__}: {error}",
            )
            failed += 1
            continue
        repository.mark_outbox_published([event.id])
        dispatched += 1
    return {"dispatched": dispatched, "failed": failed}


def _repository_from_environment() -> KnowledgeRepository:
    return KnowledgeRepository(
        create_engine(os.environ["HARDATLAS_DATABASE_URL"], pool_pre_ping=True)
    )


def assess_knowledge_quality_once(
    repository: KnowledgeRepository,
    *,
    entity_ids: list[str] | None = None,
    assessed_at: datetime | None = None,
) -> QualityScanResult:
    profiles = [
        QualityProfile.model_validate(document)
        for document in repository.list_schema_documents(kind="quality-profile")
    ]
    result = QualityMaintenanceService(repository, profiles).scan(
        entity_ids=entity_ids,
        assessed_at=assessed_at,
    )
    repository.append_audit_event(
        actor=WORKER_PRINCIPAL,
        action="quality.scan",
        resource_type="quality-assessment",
        resource_id=",".join(entity_ids or []) or "all-published",
        outcome="success" if not result.failures else "failed",
        request_id=f"worker-{uuid4()}",
        metadata={
            "assessedCount": len(result.assessed),
            "skippedCount": len(result.skipped_entity_ids),
            "failureCount": len(result.failures),
            "openTaskCount": result.open_task_count,
        },
    )
    return result


def _model_gateway_from_environment() -> ModelGateway | None:
    base_url = os.getenv("HARDATLAS_MODEL_GATEWAY_URL", "").strip()
    if not base_url:
        return None
    return OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url=base_url,
            model=os.getenv("HARDATLAS_MODEL_GATEWAY_MODEL", ""),
            api_key=os.getenv("HARDATLAS_MODEL_GATEWAY_API_KEY", ""),
            endpoint_path=os.getenv(
                "HARDATLAS_MODEL_GATEWAY_ENDPOINT_PATH",
                "/v1/responses",
            ),
            timeout_seconds=float(os.getenv("HARDATLAS_MODEL_GATEWAY_TIMEOUT_SECONDS", "60")),
            gateway_id=os.getenv(
                "HARDATLAS_MODEL_GATEWAY_ID",
                "configured-proxy",
            ),
            allow_direct_provider=(
                os.getenv(
                    "HARDATLAS_MODEL_ALLOW_DIRECT_PROVIDER",
                    "false",
                ).casefold()
                == "true"
            ),
        )
    )


@dramatiq.actor(queue_name="quality", max_retries=5, min_backoff=5_000)
def assess_knowledge_quality(
    entity_ids: list[str] | None = None,
) -> dict[str, object]:
    result = assess_knowledge_quality_once(
        _repository_from_environment(),
        entity_ids=entity_ids,
    )
    return result.model_dump(mode="json", by_alias=True)


@dramatiq.actor(queue_name="outbox", max_retries=10, min_backoff=2_000)
def dispatch_quality_maintenance(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    registry = build_agent_registry(
        os.getenv(
            "HARDATLAS_AGENT_PACK_PATHS",
            "agent-packs/core",
        )
    )
    graph = registry.graphs.get("quality-maintenance-triage")
    if graph is None:
        raise ValueError("quality maintenance triage graph is not registered")
    return dispatch_quality_maintenance_events(
        repository,
        graph,
        limit=limit,
    )


@dramatiq.actor(
    queue_name="maintenance-work",
    max_retries=5,
    min_backoff=2_000,
)
def activate_maintenance_work(
    work_item_id: str,
) -> dict[str, object]:
    work_item = activate_maintenance_work_once(
        _repository_from_environment(),
        work_item_id,
    )
    if work_item.status == "ready" and work_item.route == "source-acquisition":
        execute_source_maintenance_work.send(work_item.id)
    return work_item.model_dump(mode="json", by_alias=True)


@dramatiq.actor(
    queue_name="maintenance-work",
    max_retries=10,
    min_backoff=5_000,
)
def execute_source_maintenance_work(
    work_item_id: str,
) -> dict[str, object]:
    work_item, job = request_source_acquisition_for_work_once(
        _repository_from_environment(),
        work_item_id,
    )
    return {
        "workItem": work_item.model_dump(mode="json", by_alias=True),
        "acquisitionJob": (job.model_dump(mode="json", by_alias=True) if job is not None else None),
    }


@dramatiq.actor(queue_name="outbox", max_retries=10, min_backoff=2_000)
def dispatch_maintenance_work(limit: int = 100) -> dict[str, int]:
    return dispatch_maintenance_work_events(
        _repository_from_environment(),
        activate_maintenance_work.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="agent-graphs", max_retries=5, min_backoff=5_000)
def execute_agent_schedule(schedule_id: str) -> dict[str, object]:
    repository = _repository_from_environment()
    configured_paths = os.getenv(
        "HARDATLAS_AGENT_PACK_PATHS",
        "agent-packs/core",
    )
    schedule = repository.get_agent_graph_schedule(schedule_id)
    if schedule is None:
        raise ValueError("agent graph schedule not found")
    registry, _ = resolve_schedule_registry(
        repository,
        schedule,
        configured_paths,
    )
    completed = execute_agent_schedule_once(
        repository,
        registry,
        schedule_id,
        _model_gateway_from_environment(),
    )
    return completed.model_dump(mode="json", by_alias=True)


@dramatiq.actor(queue_name="outbox", max_retries=10, min_backoff=2_000)
def dispatch_agent_schedules(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_agent_schedule_events(
        repository,
        execute_agent_schedule.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="governance", max_retries=5, min_backoff=2_000)
def evaluate_governance_proposal(proposal_id: str) -> dict[str, object]:
    repository = _repository_from_environment()
    evaluated = evaluate_governed_proposal_once(
        repository,
        proposal_id,
        policy_version=os.getenv(
            "HARDATLAS_POLICY_VERSION",
            "policy-1.0.0",
        ),
    )
    return evaluated.model_dump(mode="json", by_alias=True)


@dramatiq.actor(queue_name="outbox", max_retries=10, min_backoff=2_000)
def dispatch_governance_proposals(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_governance_proposal_events(
        repository,
        evaluate_governance_proposal.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="publication", max_retries=8, min_backoff=5_000)
def publish_accepted_proposal_batch(
    proposal_id: str,
) -> dict[str, object]:
    if os.getenv("HARDATLAS_AUTO_RELEASE_ENABLED", "true").strip().casefold() != "true":
        return {
            "proposalId": proposal_id,
            "status": "auto-release-disabled",
        }
    if os.getenv("HARDATLAS_SEARCH_BACKEND", "memory").strip().casefold() != "opensearch":
        raise ValueError("automatic release requires HARDATLAS_SEARCH_BACKEND=opensearch")
    repository = _repository_from_environment()
    release = plan_and_publish_accepted_proposals(
        repository,
        OpenSearchBackend(
            base_url=os.getenv(
                "HARDATLAS_OPENSEARCH_URL",
                "http://localhost:9200",
            ),
            index_alias=os.getenv(
                "HARDATLAS_OPENSEARCH_INDEX_ALIAS",
                "atlas-knowledge-read",
            ),
        ),
        proposal_id,
        default_schema_version=os.getenv(
            "HARDATLAS_SCHEMA_VERSION",
            "schema-2.2.0",
        ),
        batch_limit=max(
            int(os.getenv("HARDATLAS_AUTO_RELEASE_BATCH_SIZE", "50")),
            1,
        ),
    )
    return (
        release.model_dump(mode="json", by_alias=True)
        if release is not None
        else {
            "proposalId": proposal_id,
            "status": "not-auto-release-eligible",
        }
    )


@dramatiq.actor(queue_name="outbox", max_retries=10, min_backoff=2_000)
def dispatch_accepted_proposals(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_accepted_proposal_events(
        repository,
        publish_accepted_proposal_batch.send,
        limit=limit,
    )


@dramatiq.actor(
    queue_name="publication",
    max_retries=8,
    min_backoff=5_000,
)
def verify_published_release(
    release_id: str,
) -> dict[str, object]:
    if os.getenv("HARDATLAS_SEARCH_BACKEND", "memory").strip().casefold() != "opensearch":
        raise ValueError("release verification requires HARDATLAS_SEARCH_BACKEND=opensearch")
    repository = _repository_from_environment()
    release = verify_published_release_once(
        repository,
        OpenSearchBackend(
            base_url=os.getenv(
                "HARDATLAS_OPENSEARCH_URL",
                "http://localhost:9200",
            ),
            index_alias=os.getenv(
                "HARDATLAS_OPENSEARCH_INDEX_ALIAS",
                "atlas-knowledge-read",
            ),
        ),
        release_id,
        automatic_rollback_enabled=(
            os.getenv(
                "HARDATLAS_AUTO_ROLLBACK_ENABLED",
                "true",
            )
            .strip()
            .casefold()
            == "true"
        ),
    )
    return release.model_dump(mode="json", by_alias=True)


@dramatiq.actor(
    queue_name="outbox",
    max_retries=10,
    min_backoff=2_000,
)
def dispatch_published_releases(
    limit: int = 100,
) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_release_published_events(
        repository,
        verify_published_release.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="sources", max_retries=10, min_backoff=2_000)
def dispatch_source_acquisitions(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_source_acquisition_events(
        repository,
        acquire_source_job.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="sources", max_retries=10, min_backoff=2_000)
def dispatch_source_snapshots(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_source_snapshot_events(
        repository,
        extract_source_snapshot_task.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="sources", max_retries=10, min_backoff=2_000)
def dispatch_source_extractions(limit: int = 100) -> dict[str, int]:
    repository = _repository_from_environment()
    return dispatch_source_extraction_events(
        repository,
        finalize_extraction_batch_task.send,
        limit=limit,
    )


@dramatiq.actor(queue_name="sources", max_retries=5)
def monitor_source(source_id: str, snapshot_hash: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "snapshot_hash": snapshot_hash,
        "next_step": "acquisition",
    }


@dramatiq.actor(queue_name="sources", max_retries=5, min_backoff=10_000)
def acquire_source(source_id: str, url: str | None = None) -> dict[str, str | int]:
    repository = KnowledgeRepository(
        create_engine(os.environ["HARDATLAS_DATABASE_URL"], pool_pre_ping=True)
    )
    source = repository.get_source_definition(source_id)
    if source is None:
        raise ValueError("source definition not found")
    result = acquire_http_source(source, url=url)
    store = S3SnapshotStore(
        bucket=os.getenv("S3_BUCKET", "hardatlas-local"),
        endpoint_url=os.getenv("S3_ENDPOINT"),
        access_key=os.getenv("S3_ACCESS_KEY"),
        secret_key=os.getenv("S3_SECRET_KEY"),
        region=os.getenv("S3_REGION", "us-east-1"),
    )
    store.ensure_bucket()
    store.put(
        key=result.snapshot.storage_key,
        content=result.content,
        media_type=result.snapshot.media_type,
        metadata={
            "sha256": result.snapshot.content_sha256,
            "source-id": source.id,
            "source-version": source.version,
            "license-id": source.license_id,
        },
    )
    repository.save_source_snapshot(result.snapshot)
    next_step = (
        "extraction-pending-outbox"
        if source.parser_id and source.parser_version
        else "extraction-configuration-required"
    )
    return {
        "source_id": source.id,
        "snapshot_id": result.snapshot.id,
        "sha256": result.snapshot.content_sha256,
        "byte_size": result.snapshot.byte_size,
        "storage_key": result.snapshot.storage_key,
        "next_step": next_step,
    }


@dramatiq.actor(queue_name="sources", max_retries=5, min_backoff=10_000)
def acquire_source_job(job_id: str) -> dict[str, str]:
    repository = _repository_from_environment()
    store = S3SnapshotStore(
        bucket=os.getenv("S3_BUCKET", "hardatlas-local"),
        endpoint_url=os.getenv("S3_ENDPOINT"),
        access_key=os.getenv("S3_ACCESS_KEY"),
        secret_key=os.getenv("S3_SECRET_KEY"),
        region=os.getenv("S3_REGION", "us-east-1"),
    )
    store.ensure_bucket()
    completed = execute_source_acquisition_once(
        repository,
        store,
        job_id,
    )
    source = repository.get_source_definition(
        completed.source_id,
        version=completed.source_version,
    )
    next_step = (
        "extraction-pending-outbox"
        if (source and source.parser_id and source.parser_version and completed.snapshot_id)
        else "completed"
    )
    return {
        "job_id": completed.id,
        "status": completed.status,
        "snapshot_id": completed.snapshot_id or "",
        "next_step": next_step,
    }


@dramatiq.actor(queue_name="sources", max_retries=5, min_backoff=10_000)
def extract_source_snapshot_task(
    snapshot_id: str,
) -> dict[str, str | int]:
    repository = _repository_from_environment()
    store = S3SnapshotStore(
        bucket=os.getenv("S3_BUCKET", "hardatlas-local"),
        endpoint_url=os.getenv("S3_ENDPOINT"),
        access_key=os.getenv("S3_ACCESS_KEY"),
        secret_key=os.getenv("S3_SECRET_KEY"),
        region=os.getenv("S3_REGION", "us-east-1"),
    )
    parser_registry = build_parser_registry(
        os.getenv("HARDATLAS_PARSER_PATHS", "parser-packs/core")
    )
    batch, candidates = extract_source_snapshot_once(
        repository,
        store,
        parser_registry,
        snapshot_id,
    )
    return {
        "snapshot_id": snapshot_id,
        "batch_id": batch.id,
        "candidate_count": batch.candidate_count,
        "next_step": "resolution-pending-outbox",
    }


@dramatiq.actor(queue_name="entity-resolution", max_retries=5, min_backoff=5_000)
def finalize_extraction_batch_task(
    batch_id: str,
) -> dict[str, str | int]:
    repository = _repository_from_environment()
    batch = repository.get_extraction_batch(batch_id)
    if batch is None:
        raise ValueError("extraction batch not found")
    candidates = repository.list_extraction_candidates(
        batch_id=batch.id,
        limit=max(batch.candidate_count, 1),
    )
    if len(candidates) != batch.candidate_count:
        raise ValueError("extraction batch candidate set is incomplete")
    candidates = [
        resolve_extraction_candidate_once(repository, candidate) for candidate in candidates
    ]
    agent_registry = build_agent_registry(
        os.getenv("HARDATLAS_AGENT_PACK_PATHS", "agent-packs/core")
    )
    graph = agent_registry.graphs.get("knowledge-maintenance")
    if graph is None:
        raise ValueError("knowledge-maintenance graph is not registered")
    scheduling = schedule_extraction_candidates(
        repository,
        graph,
        batch,
        candidates,
    )
    return {
        "snapshot_id": batch.snapshot_id,
        "batch_id": batch.id,
        "candidate_count": batch.candidate_count,
        **scheduling,
        "next_step": "agent-graph-dispatch",
    }


@dramatiq.actor(queue_name="entity-resolution", max_retries=3)
def resolve_entity_candidate(
    candidate_id: str,
    label: str,
    entity_type_id: str,
) -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "normalized_label": normalize_label(label),
        "entity_type_id": entity_type_id,
        "next_step": "evidence-verification",
    }


@dramatiq.actor(queue_name="publication", max_retries=2)
def stage_release(release_id: str, proposal_count: int) -> dict[str, str | int]:
    return {
        "release_id": release_id,
        "proposal_count": proposal_count,
        "status": "awaiting-policy-gate",
    }


@dramatiq.actor(queue_name="publication", max_retries=5, min_backoff=5_000)
def publish_search_release(
    release_id: str,
    data_version: str,
) -> dict[str, str | int]:
    database_url = os.environ["HARDATLAS_DATABASE_URL"]
    opensearch_url = os.getenv(
        "HARDATLAS_OPENSEARCH_URL",
        "http://localhost:9200",
    )
    index_alias = os.getenv(
        "HARDATLAS_OPENSEARCH_INDEX_ALIAS",
        "atlas-knowledge-read",
    )
    repository = KnowledgeRepository(create_engine(database_url, pool_pre_ping=True))
    result = SearchPublicationService(
        repository=repository,
        search_backend=OpenSearchBackend(
            base_url=opensearch_url,
            index_alias=index_alias,
        ),
    ).publish_snapshot(data_version=data_version)
    return {
        "release_id": release_id,
        "data_version": result.data_version,
        "index_name": result.index_name,
        "entity_count": result.entity_count,
        "outbox_event_count": result.outbox_event_count,
        "status": "search-index-active",
    }


@dramatiq.actor(queue_name="hardware-indexing", max_retries=5)
def index_hardware_extension(
    model_id: str,
    display_name: str,
    data_version: str,
) -> dict[str, str]:
    return {
        "model_id": model_id,
        "normalized_name": normalize_label(display_name),
        "data_version": data_version,
    }
