from typing import Any

from .gateway import ModelGateway, ModelJSONRequest
from .graph import HandlerContext, HandlerResult


def source_monitor_handler(context: HandlerContext) -> HandlerResult:
    return HandlerResult(
        output={
            "sourceId": context.input["sourceId"],
            "snapshotHash": context.input["snapshotHash"],
        }
    )


def extraction_handler(
    context: HandlerContext,
    gateway: ModelGateway | None = None,
) -> HandlerResult:
    output = {
        "label": context.input["label"],
        "fieldPath": context.input["fieldPath"],
        "proposedValue": context.input["proposedValue"],
        "citationId": context.input["citationId"],
        "confidence": context.input["confidence"],
        "executionMode": "deterministic",
    }
    source_excerpt = context.input.get("sourceExcerpt")
    if (
        gateway is None
        or context.input.get("modelProcessingAllowed") is not True
        or not isinstance(source_excerpt, str)
        or not source_excerpt.strip()
    ):
        return HandlerResult(output=output)
    result = gateway.generate_json(
        ModelJSONRequest(
            agent_id="agent-content-extractor",
            instructions=(
                "Extract only the requested encyclopedia field from the supplied immutable "
                "source excerpt. Do not invent evidence or citations. Return strict JSON."
            ),
            input_document={
                "label": context.input["label"],
                "fieldPath": context.input["fieldPath"],
                "sourceExcerpt": source_excerpt,
                "parserCandidate": context.input["proposedValue"],
            },
            output_schema_name="content_extraction_candidate",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "proposedValue": {},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["proposedValue", "confidence"],
            },
        )
    )
    confidence = result.output.get("confidence")
    if not isinstance(confidence, int | float) or not 0 <= confidence <= 1:
        raise ValueError("model extraction confidence must be between 0 and 1")
    output.update(
        {
            "proposedValue": result.output.get("proposedValue"),
            "confidence": float(confidence),
            "executionMode": "model",
        }
    )
    return HandlerResult(
        output=output,
        usage=result.usage,
        model_invocations=[result.invocation],
    )


def entity_resolution_handler(
    context: HandlerContext,
    gateway: ModelGateway | None = None,
) -> HandlerResult:
    extracted = context.dependency_outputs["extract"]
    output: dict[str, Any] = {
        **extracted,
        "entityId": context.input["entityId"],
        "normalizedLabel": str(extracted["label"]).casefold().strip(),
        "resolutionMode": "deterministic",
    }
    candidates = context.input.get("candidateEntityIds")
    if (
        gateway is None
        or context.input.get("modelProcessingAllowed") is not True
        or not isinstance(candidates, list)
        or len(candidates) < 2
        or any(not isinstance(candidate, str) for candidate in candidates)
    ):
        return HandlerResult(output=output)
    result = gateway.generate_json(
        ModelJSONRequest(
            agent_id="agent-entity-resolver",
            instructions=(
                "Resolve the extracted encyclopedia label to exactly one supplied candidate "
                "entity ID. Never return an ID outside the candidate list. Return strict JSON."
            ),
            input_document={
                "label": extracted["label"],
                "candidateEntityIds": candidates,
                "defaultEntityId": context.input["entityId"],
            },
            output_schema_name="entity_resolution",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entityId": {"type": "string", "enum": candidates},
                    "normalizedLabel": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["entityId", "normalizedLabel", "rationale"],
            },
        )
    )
    resolved_entity_id = result.output.get("entityId")
    if resolved_entity_id not in candidates:
        raise ValueError("model selected an entity outside the supplied candidates")
    output.update(
        {
            "entityId": resolved_entity_id,
            "normalizedLabel": str(result.output.get("normalizedLabel", "")).strip(),
            "resolutionRationale": str(result.output.get("rationale", "")).strip(),
            "resolutionMode": "model",
        }
    )
    return HandlerResult(
        output=output,
        usage=result.usage,
        model_invocations=[result.invocation],
    )


def evidence_verification_handler(context: HandlerContext) -> HandlerResult:
    resolved = context.dependency_outputs["resolve"]
    if not context.input["snapshotHash"] or not resolved["citationId"]:
        raise ValueError("evidence snapshot and citation are required")
    proposal_id = f"proposal-{context.run_id}"
    return HandlerResult(
        output={
            **resolved,
            "evidenceVerified": True,
            "proposalId": proposal_id,
        },
        proposal_ids=[proposal_id],
    )


def quality_triage_handler(context: HandlerContext) -> HandlerResult:
    action = context.input["action"]
    routes = {
        "enrich-attribute": "source-acquisition",
        "add-citation": "source-acquisition",
        "add-section": "source-acquisition",
        "refresh-evidence": "source-acquisition",
        "translate": "translation-evidence",
        "classify": "taxonomy-review",
    }
    route = routes.get(action)
    if route is None:
        raise ValueError(f"unsupported quality maintenance action: {action}")
    return HandlerResult(
        output={
            "maintenanceTaskId": context.input["maintenanceTaskId"],
            "entityId": context.input["entityId"],
            "currentRevisionId": context.input["currentRevisionId"],
            "action": action,
            "route": route,
            "requiresEvidence": True,
            "proposalEligible": False,
        }
    )


def core_handlers(gateway: ModelGateway | None = None):
    return {
        "source-monitor": source_monitor_handler,
        "content-extraction": lambda context: extraction_handler(context, gateway),
        "entity-resolution": lambda context: entity_resolution_handler(context, gateway),
        "evidence-verification": evidence_verification_handler,
        "quality-triage": quality_triage_handler,
    }
