import copy
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from .governance import GovernedProposal
from .knowledge import (
    AgentProposal,
    ChangeOperation,
    Citation,
    KnowledgeEntity,
)


class PublicationError(ValueError):
    pass


def read_entity_operation_value(
    entity: KnowledgeEntity,
    path: str,
) -> tuple[bool, Any]:
    document = entity.model_dump(mode="json", by_alias=True)
    claim_match = re.fullmatch(r"/claims/([^/]+)", path)
    if claim_match:
        attribute_id = _decode_pointer_token(claim_match.group(1))
        claim = next(
            (item for item in document["claims"] if item["attributeDefinitionId"] == attribute_id),
            None,
        )
        return (True, claim["originalValue"]) if claim is not None else (False, None)
    current: Any = document
    tokens = [_decode_pointer_token(token) for token in path.removeprefix("/").split("/") if token]
    if not tokens:
        raise ValueError("maintenance target path cannot be the entity root")
    try:
        for token in tokens:
            current = current[int(token)] if isinstance(current, list) else current[token]
    except (KeyError, IndexError, TypeError, ValueError):
        return False, None
    return True, current


def build_evidence_backed_maintenance_proposal(
    *,
    proposal_id: str,
    entity_id: str,
    agent_run_id: str,
    field_path: str,
    proposed_value: Any,
    citation_id: str,
    confidence: float,
    risk: Literal["low", "medium", "high", "critical"],
    current_value_present: bool,
    current_value: Any = None,
    citation: dict[str, Any] | Citation | None = None,
    citation_already_present: bool = False,
    machine_generated: bool = True,
) -> AgentProposal:
    operations: list[ChangeOperation] = []
    if not citation_already_present:
        if citation is None:
            raise ValueError("maintenance proposal requires a registered or embedded citation")
        citation_document = (
            citation if isinstance(citation, Citation) else Citation.model_validate(citation)
        )
        if citation_document.id != citation_id:
            raise ValueError("maintenance citation id does not match citation")
        encoded_citation_id = citation_id.replace("~", "~0").replace("/", "~1")
        operations.append(
            ChangeOperation(
                operation="add",
                path=f"/citations/{encoded_citation_id}",
                after=citation_document.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                citation_ids=[],
                confidence=1,
            )
        )
    operations.append(
        ChangeOperation(
            operation="replace" if current_value_present else "add",
            path=field_path,
            before=current_value if current_value_present else None,
            after=proposed_value,
            citation_ids=[citation_id],
            confidence=confidence,
            machine_generated=machine_generated,
        )
    )
    return AgentProposal(
        id=proposal_id,
        entity_id=entity_id,
        proposal_type="content",
        operations=operations,
        risk=risk,
        status="proposed",
        agent_run_id=agent_run_id,
        impact={
            "entityCount": 1,
            "operationCount": len(operations),
            "citationCount": 0 if citation_already_present else 1,
        },
    )


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _apply_claim_operation(
    document: dict[str, Any],
    governed: GovernedProposal,
    operation: Any,
    attribute_id: str,
) -> None:
    claims = document["claims"]
    claim = next(
        (item for item in claims if item["attributeDefinitionId"] == attribute_id),
        None,
    )
    if operation.operation == "remove":
        if claim is None:
            raise PublicationError(f"claim does not exist: {attribute_id}")
        if operation.before is not None and claim["originalValue"] != operation.before:
            raise PublicationError("proposal before value conflicts with current claim")
        claims.remove(claim)
        return

    if claim is None:
        if operation.operation == "replace" and operation.before is not None:
            raise PublicationError(f"claim does not exist: {attribute_id}")
        claim = {
            "id": f"claim-{governed.proposal.id}-{attribute_id}",
            "attributeDefinitionId": attribute_id,
            "originalValue": operation.after,
            "normalizedValue": operation.after,
            "displayValue": [
                {
                    "locale": "zh-CN",
                    "value": _display_value(operation.after),
                    "machineGenerated": True,
                }
            ],
            "confidence": operation.confidence,
            "citationIds": operation.citation_ids,
            "revisionId": document["revision"]["revisionId"],
        }
        claims.append(claim)
        return

    if operation.before is not None and claim["originalValue"] != operation.before:
        raise PublicationError("proposal before value conflicts with current claim")
    if operation.operation == "merge":
        if not isinstance(claim["originalValue"], dict) or not isinstance(operation.after, dict):
            raise PublicationError("merge requires object claim values")
        value = {**claim["originalValue"], **operation.after}
    else:
        value = operation.after
    claim.update(
        {
            "originalValue": value,
            "normalizedValue": value,
            "displayValue": [
                {
                    "locale": "zh-CN",
                    "value": _display_value(value),
                    "machineGenerated": True,
                }
            ],
            "confidence": operation.confidence,
            "citationIds": operation.citation_ids,
        }
    )


def _apply_section_body_operation(
    document: dict[str, Any],
    operation: Any,
    section_index: int,
) -> None:
    try:
        section = document["sections"][section_index]
    except IndexError as error:
        raise PublicationError(f"section index does not exist: {section_index}") from error
    current = section["body"]
    if operation.before is not None and current != operation.before:
        raise PublicationError("proposal before value conflicts with current section")
    if operation.operation == "remove":
        section["body"] = []
    elif operation.operation in {"add", "replace"}:
        section["body"] = [
            {
                "locale": "zh-CN",
                "value": _display_value(operation.after),
                "machineGenerated": True,
            }
        ]
        section["citationIds"] = sorted(
            set([*section.get("citationIds", []), *operation.citation_ids])
        )
    else:
        raise PublicationError("merge is not supported for localized section bodies")


def _decode_pointer_token(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _apply_citation_operation(
    document: dict[str, Any],
    operation: Any,
    encoded_citation_id: str,
) -> None:
    citation_id = _decode_pointer_token(encoded_citation_id)
    citations = document["citations"]
    existing = next(
        (item for item in citations if item.get("id") == citation_id),
        None,
    )
    if operation.operation == "remove":
        if existing is None:
            raise PublicationError(f"citation does not exist: {citation_id}")
        if operation.before is not None and existing != operation.before:
            raise PublicationError("proposal before value conflicts with current citation")
        citations.remove(existing)
        return

    if not isinstance(operation.after, dict):
        raise PublicationError("citation operation requires an object value")
    if operation.after.get("id") != citation_id:
        raise PublicationError("citation path does not match citation document id")
    if existing is None:
        if operation.operation == "replace":
            raise PublicationError(f"citation does not exist: {citation_id}")
        citations.append(operation.after)
        return
    if operation.before is not None and existing != operation.before:
        raise PublicationError("proposal before value conflicts with current citation")
    if operation.operation == "add":
        if existing != operation.after:
            raise PublicationError(
                f"citation id already exists with different content: {citation_id}"
            )
        return
    if operation.operation == "merge":
        merged = {**existing, **operation.after}
        if merged.get("id") != citation_id:
            raise PublicationError("citation merge cannot change citation id")
        citations[citations.index(existing)] = merged
        return
    citations[citations.index(existing)] = operation.after


def _resolve_pointer_parent(
    document: dict[str, Any],
    path: str,
) -> tuple[dict[str, Any] | list[Any], str]:
    tokens = [
        token.replace("~1", "/").replace("~0", "~")
        for token in path.lstrip("/").split("/")
        if token
    ]
    if not tokens:
        raise PublicationError("proposal path cannot target the document root")
    parent: dict[str, Any] | list[Any] = document
    for token in tokens[:-1]:
        try:
            parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise PublicationError(f"proposal path does not exist: {path}") from error
        if not isinstance(parent, (dict, list)):
            raise PublicationError(f"proposal path is not a container: {path}")
    return parent, tokens[-1]


def _mark_localized_values_machine_generated(value: Any) -> Any:
    result = copy.deepcopy(value)
    if isinstance(result, list):
        return [_mark_localized_values_machine_generated(item) for item in result]
    if isinstance(result, dict):
        result = {
            key: _mark_localized_values_machine_generated(item) for key, item in result.items()
        }
        if isinstance(result.get("locale"), str) and "value" in result:
            result["machineGenerated"] = True
    return result


def _apply_generic_operation(
    document: dict[str, Any],
    operation: Any,
) -> None:
    parent, token = _resolve_pointer_parent(document, operation.path)
    after = (
        _mark_localized_values_machine_generated(operation.after)
        if operation.machine_generated
        else operation.after
    )
    if isinstance(parent, list):
        if token == "-" and operation.operation == "add":
            parent.append(after)
            return
        try:
            index = int(token)
            current = parent[index]
        except (ValueError, IndexError) as error:
            raise PublicationError(
                f"proposal list path does not exist: {operation.path}"
            ) from error
        if operation.before is not None and current != operation.before:
            raise PublicationError("proposal before value conflicts with current document")
        if operation.operation == "remove":
            parent.pop(index)
        elif operation.operation == "merge":
            if not isinstance(current, dict) or not isinstance(after, dict):
                raise PublicationError("merge requires object values")
            parent[index] = {**current, **after}
        else:
            parent[index] = after
        return

    current = parent.get(token)
    if operation.operation != "add" and token not in parent:
        raise PublicationError(f"proposal property does not exist: {operation.path}")
    if operation.before is not None and current != operation.before:
        raise PublicationError("proposal before value conflicts with current document")
    if operation.operation == "remove":
        del parent[token]
    elif operation.operation == "merge":
        if not isinstance(current, dict) or not isinstance(after, dict):
            raise PublicationError("merge requires object values")
        parent[token] = {**current, **after}
    else:
        parent[token] = after
    if operation.machine_generated and token == "value" and isinstance(parent.get("locale"), str):
        parent["machineGenerated"] = True


def apply_release_to_entity(
    entity: KnowledgeEntity,
    governed_proposals: Iterable[GovernedProposal],
    *,
    data_version: str,
    schema_version: str,
    policy_version: str,
) -> KnowledgeEntity:
    proposals = list(governed_proposals)
    if not proposals:
        raise PublicationError("entity release requires at least one proposal")
    if any(item.proposal.status != "accepted" for item in proposals):
        raise PublicationError("only accepted proposals can create entity revisions")
    if any(item.proposal.entity_id != entity.ref.id for item in proposals):
        raise PublicationError("proposal entity does not match release entity")

    document = entity.model_dump(mode="json", by_alias=True)
    citation_ids = {citation["id"] for citation in document["citations"]}
    for governed in proposals:
        for operation in governed.proposal.operations:
            citation_match = re.fullmatch(
                r"/citations/([^/]+)",
                operation.path,
            )
            if citation_match and isinstance(operation.after, dict):
                citation_id = operation.after.get("id")
                if isinstance(citation_id, str) and citation_id == _decode_pointer_token(
                    citation_match.group(1)
                ):
                    citation_ids.add(citation_id)
            elif operation.path == "/citations" and isinstance(operation.after, list):
                for citation in operation.after:
                    if isinstance(citation, dict) and isinstance(citation.get("id"), str):
                        citation_ids.add(citation["id"])
    for governed in proposals:
        for operation in governed.proposal.operations:
            missing_citations = set(operation.citation_ids) - citation_ids
            if missing_citations:
                raise PublicationError(
                    f"proposal references unknown citations: {sorted(missing_citations)}"
                )
            claim_match = re.fullmatch(r"/claims/([^/]+)", operation.path)
            section_match = re.fullmatch(r"/sections/(\d+)/body", operation.path)
            citation_match = re.fullmatch(
                r"/citations/([^/]+)",
                operation.path,
            )
            if citation_match:
                _apply_citation_operation(
                    document,
                    operation,
                    citation_match.group(1),
                )
            elif claim_match:
                _apply_claim_operation(
                    document,
                    governed,
                    operation,
                    claim_match.group(1),
                )
            elif section_match:
                _apply_section_body_operation(
                    document,
                    operation,
                    int(section_match.group(1)),
                )
            else:
                _apply_generic_operation(document, operation)

    proposal_digest = hashlib.sha256(
        "|".join(sorted(item.proposal.id for item in proposals)).encode()
    ).hexdigest()[:12]
    safe_version = re.sub(r"[^a-zA-Z0-9]+", "-", data_version).strip("-")
    revision_id = f"rev-{entity.ref.slug}-{safe_version}-{proposal_digest}"
    document["revision"] = {
        "revisionId": revision_id,
        "dataVersion": data_version,
        "schemaVersion": schema_version,
        "policyVersion": policy_version,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    for claim in document["claims"]:
        claim["revisionId"] = revision_id
    for relationship in document["relationships"]:
        relationship["revisionId"] = revision_id
    document["publicationStatus"] = "published"
    return KnowledgeEntity.model_validate(document)


def apply_release_to_new_entity(
    governed_proposals: Iterable[GovernedProposal],
    *,
    entity_id: str,
    data_version: str,
    schema_version: str,
    policy_version: str,
) -> KnowledgeEntity:
    proposals = list(governed_proposals)
    if len(proposals) != 1:
        raise PublicationError("new entity release requires exactly one creation proposal")
    governed = proposals[0]
    proposal = governed.proposal
    if proposal.status != "accepted":
        raise PublicationError("only accepted proposals can create entities")
    if proposal.entity_id != entity_id:
        raise PublicationError("proposal entity does not match new entity")
    if len(proposal.operations) != 1:
        raise PublicationError("creation proposal requires exactly one operation")
    operation = proposal.operations[0]
    if operation.operation != "add" or operation.path != "/entity":
        raise PublicationError("new entity proposal must add /entity")
    try:
        entity = KnowledgeEntity.model_validate(operation.after)
    except Exception as error:
        raise PublicationError(f"new entity document is invalid: {error}") from error
    if entity.ref.id != entity_id:
        raise PublicationError("new entity document id does not match proposal")
    citation_ids = {citation.id for citation in entity.citations}
    missing_citations = set(operation.citation_ids) - citation_ids
    if missing_citations:
        raise PublicationError(
            f"proposal references unknown citations: {sorted(missing_citations)}"
        )

    document = entity.model_dump(mode="json", by_alias=True)
    proposal_digest = hashlib.sha256(proposal.id.encode()).hexdigest()[:12]
    safe_version = re.sub(r"[^a-zA-Z0-9]+", "-", data_version).strip("-")
    revision_id = f"rev-{entity.ref.slug}-{safe_version}-{proposal_digest}"
    document["revision"] = {
        "revisionId": revision_id,
        "dataVersion": data_version,
        "schemaVersion": schema_version,
        "policyVersion": policy_version,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    for claim in document["claims"]:
        claim["revisionId"] = revision_id
    for relationship in document["relationships"]:
        relationship["revisionId"] = revision_id
    document["publicationStatus"] = "published"
    return KnowledgeEntity.model_validate(document)
