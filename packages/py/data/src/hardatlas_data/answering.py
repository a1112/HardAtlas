import re
from collections.abc import Iterable
from uuid import uuid4

from hardatlas_ai import (
    HandlerExecutionError,
    ModelGateway,
    ModelJSONRequest,
)
from hardatlas_domain import (
    AnswerEvidence,
    AnswerModelProvenance,
    AttributeDefinition,
    Citation,
    KnowledgeAnswer,
    KnowledgeEntity,
    LocalizedText,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .repository import KnowledgeRepository
from .search import SearchBackend, SearchBackendError


class _SynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=4_000)
    evidenceIds: list[str] = Field(min_length=1, max_length=12)
    confidence: float = Field(ge=0, le=1)


class KnowledgeAnsweringService:
    """Retrieval-first public answering with an optional audited model proxy."""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        search_backend: SearchBackend,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.repository = repository
        self.search_backend = search_backend
        self.model_gateway = model_gateway

    @staticmethod
    def _localized(values: list[LocalizedText], locale: str) -> str:
        language = locale.split("-", 1)[0].casefold()
        for item in values:
            if item.locale == locale:
                return item.value
        for item in values:
            if item.locale.split("-", 1)[0].casefold() == language:
                return item.value
        for item in values:
            if item.locale == "zh-CN":
                return item.value
        return values[0].value if values else ""

    @staticmethod
    def _query_terms(question: str) -> list[str]:
        normalized = question.casefold()
        terms = {
            value
            for value in re.findall(r"[a-z0-9][a-z0-9.+#_-]{1,}", normalized)
            if len(value) >= 2
        }
        ignored = {
            "什么",
            "为什么",
            "哪些",
            "哪里",
            "如何",
            "怎么",
            "是否",
            "可以",
            "关于",
            "介绍",
            "多少",
        }
        for sequence in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
            if sequence not in ignored:
                terms.add(sequence)
            for width in (4, 3, 2):
                if len(sequence) < width:
                    continue
                for index in range(len(sequence) - width + 1):
                    term = sequence[index : index + width]
                    if term not in ignored:
                        terms.add(term)
        return sorted(terms, key=lambda value: (-len(value), value))[:80]

    @staticmethod
    def _searchable_labels(entity: KnowledgeEntity) -> Iterable[str]:
        yield entity.ref.canonical_name
        yield from (item.value for item in entity.names)
        yield from (item.value for item in entity.aliases)

    def _candidate_entities(
        self,
        question: str,
        *,
        limit: int = 3,
    ) -> list[KnowledgeEntity]:
        normalized = question.casefold()
        published = [
            entity
            for entity in self.repository.list_entities()
            if entity.publication_status == "published"
        ]
        mentions: list[tuple[int, KnowledgeEntity]] = []
        for entity in published:
            matching = [
                label
                for label in self._searchable_labels(entity)
                if label.strip() and label.casefold() in normalized
            ]
            if matching:
                mentions.append((max(len(label) for label in matching), entity))
        if mentions:
            return [
                entity
                for _, entity in sorted(
                    mentions,
                    key=lambda item: (-item[0], item[1].ref.slug),
                )[:limit]
            ]

        slugs: list[str] = []
        try:
            for term in [question, *self._query_terms(question)]:
                if len(slugs) >= limit:
                    break
                for hit in self.search_backend.search(term, limit=limit):
                    if hit.slug not in slugs:
                        slugs.append(hit.slug)
                        if len(slugs) >= limit:
                            break
        except SearchBackendError:
            return []
        by_slug = {entity.ref.slug: entity for entity in published}
        return [by_slug[slug] for slug in slugs if slug in by_slug]

    def _entity_evidence(
        self,
        entity: KnowledgeEntity,
        question: str,
        locale: str,
    ) -> list[AnswerEvidence]:
        citations = {citation.id for citation in entity.citations}
        terms = self._query_terms(question)
        ranked: list[tuple[int, int, AnswerEvidence]] = []
        for section in entity.sections:
            citation_ids = [
                citation_id
                for citation_id in section.citation_ids
                if citation_id in citations
            ]
            if not citation_ids:
                continue
            label = self._localized(section.heading, locale) or section.key
            text = self._localized(section.body, locale)
            haystack = f"{label} {text}".casefold()
            score = sum(len(term) for term in terms if term in haystack)
            ranked.append(
                (
                    score,
                    -section.order,
                    AnswerEvidence(
                        id=f"evidence:{entity.ref.id}:section:{section.id}",
                        entity=entity.ref,
                        revision_id=entity.revision.revision_id,
                        kind="section",
                        label=label,
                        text=text,
                        citation_ids=citation_ids,
                        confidence=0.95,
                    ),
                )
            )
        for index, claim in enumerate(entity.claims):
            citation_ids = [
                citation_id
                for citation_id in claim.citation_ids
                if citation_id in citations
            ]
            if not citation_ids:
                continue
            text = self._localized(claim.display_value, locale)
            definition_document = self.repository.get_schema_document(
                document_id=claim.attribute_definition_id,
                kind="attribute-definition",
            )
            definition = (
                AttributeDefinition.model_validate(definition_document)
                if definition_document is not None
                else None
            )
            label = (
                self._localized(definition.name, locale)
                if definition is not None
                else claim.attribute_definition_id
            )
            haystack = f"{label} {text}".casefold()
            score = sum(len(term) for term in terms if term in haystack)
            ranked.append(
                (
                    score,
                    -10_000 - index,
                    AnswerEvidence(
                        id=f"evidence:{entity.ref.id}:claim:{claim.id}",
                        entity=entity.ref,
                        revision_id=entity.revision.revision_id,
                        kind="claim",
                        label=label,
                        text=text,
                        citation_ids=citation_ids,
                        confidence=claim.confidence,
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2].id))
        relevant = [item for item in ranked if item[0] > 0]
        selected = relevant if relevant else ranked
        return [item[2] for item in selected[:4]]

    @staticmethod
    def _citations(
        entities: list[KnowledgeEntity],
        evidence: list[AnswerEvidence],
    ) -> list[Citation]:
        selected_ids = {
            citation_id
            for item in evidence
            for citation_id in item.citation_ids
        }
        citations = {
            citation.id: citation
            for entity in entities
            for citation in entity.citations
            if citation.id in selected_ids
        }
        return [citations[citation_id] for citation_id in sorted(citations)]

    @staticmethod
    def _retrieval_answer(evidence: list[AnswerEvidence]) -> str:
        if not evidence:
            return "当前发布内容中没有足够的可引用证据来可靠回答这个问题。"
        parts: list[str] = []
        for index, item in enumerate(evidence[:6], start=1):
            text = item.text.strip().rstrip("。.!！")
            parts.append(f"{text} [{index}]")
        return "根据当前已发布版本，" + "；".join(parts) + "。"

    @staticmethod
    def _provenance_from_failure(
        error: HandlerExecutionError,
    ) -> AnswerModelProvenance | None:
        if not error.model_invocations:
            return None
        invocation = error.model_invocations[-1]
        return AnswerModelProvenance(
            gateway_id=invocation.gateway_id,
            model=invocation.model,
            request_id=invocation.request_id,
            status="failed",
            error_code=invocation.error_code or str(error),
            input_tokens=error.usage.input_tokens,
            output_tokens=error.usage.output_tokens,
        )

    def answer(
        self,
        *,
        question: str,
        locale: str,
        allow_model: bool,
        answer_id: str | None = None,
    ) -> KnowledgeAnswer:
        normalized_question = " ".join(question.split())
        entities = self._candidate_entities(normalized_question)
        evidence = [
            item
            for entity in entities
            for item in self._entity_evidence(entity, normalized_question, locale)
        ][:12]
        selected_evidence = evidence
        status = "answered" if evidence else "insufficient-evidence"
        mode = "retrieval-synthesis"
        answer_text = self._retrieval_answer(evidence)
        model_provenance: AnswerModelProvenance | None = None
        fallback_reason: str | None = None

        if evidence and allow_model and self.model_gateway is not None:
            try:
                result = self.model_gateway.generate_json(
                    ModelJSONRequest(
                        agent_id="public-answer-synthesizer",
                        instructions=(
                            "仅根据输入中的 evidence 回答问题。不得使用外部知识，不得补全"
                            "缺失事实。每个事实句使用 [1]、[2] 形式引用对应 evidence 的"
                            " ordinal。若证据不足，明确说明限制。evidenceIds 只能选择输入"
                            "中存在的 ID。"
                        ),
                        input_document={
                            "question": normalized_question,
                            "locale": locale,
                            "evidence": [
                                {
                                    "ordinal": index,
                                    "id": item.id,
                                    "entityId": item.entity.id,
                                    "revisionId": item.revision_id,
                                    "label": item.label,
                                    "text": item.text,
                                    "citationIds": item.citation_ids,
                                }
                                for index, item in enumerate(evidence, start=1)
                            ],
                        },
                        output_schema_name="atlas_public_answer",
                        output_schema=_SynthesisResult.model_json_schema(),
                    )
                )
                synthesis = _SynthesisResult.model_validate(result.output)
                evidence_by_id = {item.id: item for item in evidence}
                if (
                    len(synthesis.evidenceIds) != len(set(synthesis.evidenceIds))
                    or any(
                        evidence_id not in evidence_by_id
                        for evidence_id in synthesis.evidenceIds
                    )
                ):
                    raise ValueError("model selected evidence outside the retrieval set")
                selected_evidence = [
                    evidence_by_id[evidence_id]
                    for evidence_id in synthesis.evidenceIds
                ]
                answer_text = synthesis.answer
                mode = "model-proxy"
                model_provenance = AnswerModelProvenance(
                    gateway_id=result.invocation.gateway_id,
                    model=result.invocation.model,
                    request_id=result.invocation.request_id,
                    status="succeeded",
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                )
            except HandlerExecutionError as error:
                mode = "retrieval-fallback"
                fallback_reason = str(error)
                model_provenance = self._provenance_from_failure(error)
            except (ValidationError, ValueError, TypeError, KeyError):
                mode = "retrieval-fallback"
                fallback_reason = "model-output-rejected"

        selected_entity_ids = {item.entity.id for item in selected_evidence}
        selected_entities = [
            entity for entity in entities if entity.ref.id in selected_entity_ids
        ]
        if not selected_entities:
            selected_entities = entities
        answer = KnowledgeAnswer(
            id=answer_id or f"answer-{uuid4()}",
            question=normalized_question,
            locale=locale,
            status=status,
            mode=mode,
            answer=answer_text,
            entities=[entity.ref for entity in selected_entities],
            evidence=selected_evidence,
            citations=self._citations(selected_entities, selected_evidence),
            data_versions=sorted(
                {entity.revision.data_version for entity in selected_entities}
            ),
            model_provenance=model_provenance,
            fallback_reason=fallback_reason,
        )
        self.repository.save_knowledge_answer(answer)
        return answer
