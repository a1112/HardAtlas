import json
from pathlib import Path

from hardatlas_domain import CompatibilityStatus, ProductRef
from hardatlas_rules import CompatibilityRule, Predicate, RuleEngine
from hypothesis import given
from hypothesis import strategies as st

RULES = [
    CompatibilityRule(
        id="power.minimum",
        version="rules-2026.07",
        priority=10,
        when=[Predicate(field="power.available_watts", operator="lte", value=649)],
        status=CompatibilityStatus.INCOMPATIBLE,
        title="电源额定功率不足",
        explanation="可用功率低于该组合的 650 W 最低要求。",
        required_actions=["更换为额定功率至少 650 W 的电源"],
    ),
    CompatibilityRule(
        id="bios.required",
        version="rules-2026.07",
        priority=20,
        when=[Predicate(field="bios.version", operator="in", value=["1.0", "1.1"])],
        status=CompatibilityStatus.CONDITIONAL,
        title="需要更新 BIOS",
        explanation="该处理器需要 BIOS 1.2 或更新版本。",
        required_actions=["在更换处理器前更新 BIOS"],
    ),
    CompatibilityRule(
        id="socket.match",
        version="rules-2026.07",
        priority=100,
        when=[Predicate(field="socket_match", operator="eq", value=True)],
        status=CompatibilityStatus.COMPATIBLE,
        title="插槽兼容",
        explanation="处理器与主板插槽一致。",
    ),
    CompatibilityRule(
        id="source.conflict",
        version="rules-2026.07",
        priority=15,
        when=[Predicate(field="source_conflict", operator="eq", value=True)],
        status=CompatibilityStatus.UNKNOWN,
        title="来源冲突",
        explanation="关键来源存在冲突，不能给出确定结论。",
    ),
    CompatibilityRule(
        id="pcie.degradation",
        version="rules-2026.07",
        priority=20,
        when=[Predicate(field="pcie.available", operator="lte", value=4)],
        status=CompatibilityStatus.CONDITIONAL,
        title="PCIe 带宽降级",
        explanation="设备可运行，但链路版本低于目标。",
    ),
    CompatibilityRule(
        id="clearance.minimum",
        version="rules-2026.07",
        priority=10,
        when=[Predicate(field="clearance.available_mm", operator="lte", value=359)],
        status=CompatibilityStatus.INCOMPATIBLE,
        title="物理空间不足",
        explanation="可用空间小于组件要求。",
    ),
]


@given(st.integers(min_value=0, max_value=649))
def test_low_power_is_always_incompatible(watts: int) -> None:
    report = RuleEngine(RULES, "data-2026.07.28").evaluate(
        subjects=[ProductRef(model_id="fixture")],
        facts={"power": {"available_watts": watts}, "socket_match": True},
    )
    assert report.status is CompatibilityStatus.INCOMPATIBLE
    assert report.issues[0].code == "power.minimum"


def test_unmatched_rules_return_unknown_with_versions() -> None:
    report = RuleEngine(RULES, "data-2026.07.28").evaluate(
        subjects=[ProductRef(model_id="fixture")],
        facts={},
    )
    assert report.status is CompatibilityStatus.UNKNOWN
    assert report.versions.rule_version == "unmatched"


def test_core_golden_set_replays_all_required_outcomes() -> None:
    golden_path = Path(__file__).parents[4] / "tests/golden/compatibility/core-v1.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    engine = RuleEngine(RULES, golden["dataVersion"])

    for case in golden["cases"]:
        report = engine.evaluate(
            subjects=[ProductRef(model_id="fixture")],
            facts=case["facts"],
            report_id=case["id"],
        )
        assert report.status.value == case["expected"], case["id"]
        assert report.versions.data_version == golden["dataVersion"]
