from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from hardatlas_domain import (
    CompatibilityIssue,
    CompatibilityReport,
    CompatibilityStatus,
    EvidenceRef,
    ProductRef,
    VersionContext,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class RuleModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class Predicate(RuleModel):
    field: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    operator: Literal["eq", "neq", "gte", "lte", "in", "contains", "exists"]
    value: str | int | float | bool | list[str] | None = None


class CompatibilityRule(RuleModel):
    id: str
    version: str
    priority: int = 100
    when: list[Predicate]
    status: CompatibilityStatus
    title: str
    explanation: str
    required_actions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


def _read_path(context: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = context
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _matches(predicate: Predicate, context: Mapping[str, Any]) -> bool:
    exists, actual = _read_path(context, predicate.field)
    if predicate.operator == "exists":
        return exists is bool(predicate.value)
    if not exists:
        return False
    if predicate.operator == "eq":
        return actual == predicate.value
    if predicate.operator == "neq":
        return actual != predicate.value
    if predicate.operator == "gte":
        return isinstance(actual, (int, float)) and actual >= predicate.value
    if predicate.operator == "lte":
        return isinstance(actual, (int, float)) and actual <= predicate.value
    if predicate.operator == "in":
        return isinstance(predicate.value, list) and actual in predicate.value
    if predicate.operator == "contains":
        return isinstance(actual, (str, list)) and predicate.value in actual
    return False


_SEVERITY = {
    CompatibilityStatus.COMPATIBLE: 0,
    CompatibilityStatus.UNKNOWN: 1,
    CompatibilityStatus.CONDITIONAL: 2,
    CompatibilityStatus.INCOMPATIBLE: 3,
}


class RuleEngine:
    def __init__(self, rules: list[CompatibilityRule], data_version: str) -> None:
        self.rules = sorted(rules, key=lambda rule: (rule.priority, rule.id))
        self.data_version = data_version

    def evaluate(
        self,
        *,
        subjects: list[ProductRef],
        facts: Mapping[str, Any],
        report_id: str = "compatibility-fixture-001",
    ) -> CompatibilityReport:
        matched = [rule for rule in self.rules if all(_matches(item, facts) for item in rule.when)]
        issues = [
            CompatibilityIssue(
                code=rule.id,
                status=rule.status,
                title=rule.title,
                explanation=rule.explanation,
                required_actions=rule.required_actions,
                evidence=rule.evidence,
            )
            for rule in matched
            if rule.status is not CompatibilityStatus.COMPATIBLE
        ]
        status = max(
            (rule.status for rule in matched),
            key=lambda item: _SEVERITY[item],
            default=CompatibilityStatus.UNKNOWN,
        )
        rule_versions = sorted({rule.version for rule in matched} or {"unmatched"})
        return CompatibilityReport(
            id=report_id,
            status=status,
            subject=subjects,
            issues=issues,
            versions=VersionContext(
                data_version=self.data_version,
                rule_version="+".join(rule_versions),
            ),
            created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        )
