from pathlib import Path

import pytest
from hardatlas_ai import (
    AgentDefinition,
    AgentGraphRunner,
    AgentGraphSpec,
    AgentNodeSpec,
    AgentRegistry,
    HandlerResult,
    MemoryCheckpointStore,
    RunBudget,
    UsageRecord,
    load_agent_pack,
)


def graph() -> AgentGraphSpec:
    return AgentGraphSpec(
        id="knowledge-maintenance",
        version="graph-1",
        nodes=[
            AgentNodeSpec(id="acquire", handler_key="acquire"),
            AgentNodeSpec(
                id="extract",
                handler_key="extract",
                depends_on=["acquire"],
            ),
            AgentNodeSpec(
                id="verify",
                handler_key="verify",
                depends_on=["extract"],
            ),
        ],
    )


def test_graph_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        AgentGraphSpec(
            id="cycle",
            version="1",
            nodes=[
                AgentNodeSpec(id="a", handler_key="a", depends_on=["b"]),
                AgentNodeSpec(id="b", handler_key="b", depends_on=["a"]),
            ],
        )


def test_graph_passes_typed_dependency_outputs_and_collects_proposals() -> None:
    store = MemoryCheckpointStore()
    runner = AgentGraphRunner(
        checkpoints=store,
        handlers={
            "acquire": lambda context: HandlerResult(
                output={"snapshotHash": context.input["snapshotHash"]}
            ),
            "extract": lambda context: HandlerResult(
                output={"label": context.dependency_outputs["acquire"]["snapshotHash"]}
            ),
            "verify": lambda context: HandlerResult(
                output={"verified": True},
                proposal_ids=[f"proposal-{context.dependency_outputs['extract']['label']}"],
            ),
        },
    )
    run = runner.run(spec=graph(), run_id="run-1", input={"snapshotHash": "abc"})
    assert run.status == "completed"
    assert run.proposal_ids == ["proposal-abc"]
    assert run.nodes["verify"].attempts == 1


def test_graph_retries_and_resume_does_not_repeat_completed_nodes() -> None:
    calls = {"acquire": 0, "extract": 0}

    def acquire(_context):
        calls["acquire"] += 1
        return HandlerResult(output={"snapshot": "stable"})

    def extract(context):
        calls["extract"] += 1
        if calls["extract"] == 1:
            raise RuntimeError("transient parser failure")
        return HandlerResult(output={"snapshot": context.dependency_outputs["acquire"]["snapshot"]})

    store = MemoryCheckpointStore()
    runner = AgentGraphRunner(
        checkpoints=store,
        handlers={
            "acquire": acquire,
            "extract": extract,
            "verify": lambda _context: HandlerResult(output={"verified": True}),
        },
    )
    first = runner.run(spec=graph(), run_id="run-retry", input={})
    assert first.status == "completed"
    assert first.nodes["extract"].attempts == 2

    resumed = runner.run(spec=graph(), run_id="run-retry", input={})
    assert resumed.status == "completed"
    assert calls == {"acquire": 1, "extract": 2}

    with pytest.raises(ValueError, match="input does not match"):
        runner.run(spec=graph(), run_id="run-retry", input={"changed": True})


def test_registry_resolves_versioned_agent_definitions() -> None:
    definition = AgentDefinition(
        id="agent-acquire",
        version="1.0.0",
        name="Acquire",
        role="acquisition",
        handler_key="acquire",
        description="Acquire a governed snapshot",
        capabilities=["snapshot-read"],
    )
    spec = AgentGraphSpec(
        id="registry-graph",
        version="1",
        nodes=[
            AgentNodeSpec(
                id="acquire",
                agent_id=definition.id,
                agent_version=definition.version,
            )
        ],
    )
    registry = AgentRegistry([definition], [spec])
    runner = AgentGraphRunner(
        checkpoints=MemoryCheckpointStore(),
        handlers={"acquire": lambda _: HandlerResult(output={"ok": True})},
        registry=registry,
    )
    run = runner.run(spec=spec, run_id="run-registry", input={})
    assert run.status == "completed"
    assert run.nodes["acquire"].output == {"ok": True}


def test_registry_rejects_graph_pinned_to_another_agent_version() -> None:
    definition = AgentDefinition(
        id="agent-acquire",
        version="2.0.0",
        name="Acquire",
        role="acquisition",
        handler_key="acquire",
        description="Acquire a governed snapshot",
        capabilities=["snapshot-read"],
    )
    spec = AgentGraphSpec(
        id="registry-graph",
        version="2",
        nodes=[
            AgentNodeSpec(
                id="acquire",
                agent_id=definition.id,
                agent_version="1.0.0",
            )
        ],
    )
    with pytest.raises(ValueError, match="requires agent-acquire version 1.0.0"):
        AgentRegistry([definition], [spec])


def test_registry_rejects_invalid_json_schema_contracts() -> None:
    definition = AgentDefinition(
        id="agent-invalid-contract",
        version="1",
        name="Invalid contract",
        role="test",
        handler_key="invalid",
        description="Invalid schema must fail during registration",
        capabilities=[],
        input_contract={"type": "not-a-json-schema-type"},
    )
    with pytest.raises(ValueError, match="invalid input contract"):
        AgentRegistry([definition])


def test_registry_enforces_declared_input_and_proposal_boundaries() -> None:
    definition = AgentDefinition(
        id="agent-editor",
        version="1.0.0",
        name="Editor",
        role="extraction",
        handler_key="edit",
        description="Creates low-risk content candidates",
        capabilities=["proposal-create"],
        allowed_proposal_types=["content"],
        maximum_risk="low",
        input_contract={"required": ["entityId", "citationId"]},
    )
    node = AgentNodeSpec(
        id="edit",
        agent_id=definition.id,
        agent_version=definition.version,
    )
    spec = AgentGraphSpec(id="edit-graph", version="1", nodes=[node])
    registry = AgentRegistry([definition], [spec])

    with pytest.raises(ValueError, match="citationId"):
        registry.validate_graph_input(spec, {"entityId": "entity-1"})
    with pytest.raises(ValueError, match="may not create relation"):
        registry.validate_proposal(node, proposal_type="relation", risk="low")
    with pytest.raises(ValueError, match="may not create high risk"):
        registry.validate_proposal(node, proposal_type="content", risk="high")


def test_runner_rejects_handler_output_that_breaks_agent_contract() -> None:
    definition = AgentDefinition(
        id="agent-typed-output",
        version="1",
        name="Typed output",
        role="test",
        handler_key="typed-output",
        description="Output must follow its declared schema",
        capabilities=[],
        output_contract={
            "type": "object",
            "additionalProperties": False,
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )
    spec = AgentGraphSpec(
        id="typed-output-graph",
        version="1",
        nodes=[
            AgentNodeSpec(
                id="typed-output",
                agent_id=definition.id,
                agent_version=definition.version,
                max_attempts=1,
            )
        ],
    )
    registry = AgentRegistry([definition], [spec])
    run = AgentGraphRunner(
        checkpoints=MemoryCheckpointStore(),
        handlers={"typed-output": lambda _: HandlerResult(output={"value": 42})},
        registry=registry,
    ).run(
        spec=spec,
        run_id="run-invalid-output",
        input={},
    )
    assert run.status == "failed"
    assert "output contract failed" in (run.nodes["typed-output"].error or "")
    assert "not of type 'string'" in (run.nodes["typed-output"].error or "")


def test_run_budget_blocks_model_usage_over_limit() -> None:
    spec = AgentGraphSpec(
        id="budgeted",
        version="1",
        budget=RunBudget(max_model_calls=0),
        nodes=[AgentNodeSpec(id="extract", handler_key="extract")],
    )
    runner = AgentGraphRunner(
        checkpoints=MemoryCheckpointStore(),
        handlers={
            "extract": lambda _: HandlerResult(
                output={"candidate": "value"},
                usage=UsageRecord(
                    model_calls=1,
                    input_tokens=10,
                    output_tokens=2,
                    cost_microusd=50,
                ),
            )
        },
    )
    run = runner.run(spec=spec, run_id="run-budget", input={})
    assert run.status == "failed"
    assert run.budget_exhausted is True
    assert run.nodes["extract"].error == "model call budget exceeded"
    assert run.proposal_ids == []


def test_model_policy_is_enforced_by_runner() -> None:
    required = AgentDefinition(
        id="agent-required",
        version="1",
        name="Required",
        role="extraction",
        handler_key="required",
        description="Requires a model",
        capabilities=[],
        model_policy="model-required",
    )
    deterministic = AgentDefinition(
        id="agent-deterministic",
        version="1",
        name="Deterministic",
        role="verification",
        handler_key="deterministic",
        description="Must not call a model",
        capabilities=[],
        model_policy="deterministic",
    )
    spec = AgentGraphSpec(
        id="policy",
        version="1",
        nodes=[
            AgentNodeSpec(
                id="required",
                agent_id=required.id,
                agent_version=required.version,
            ),
            AgentNodeSpec(
                id="deterministic",
                agent_id=deterministic.id,
                agent_version=deterministic.version,
            ),
        ],
    )
    registry = AgentRegistry([required, deterministic], [spec])
    runner = AgentGraphRunner(
        checkpoints=MemoryCheckpointStore(),
        handlers={
            "required": lambda _: HandlerResult(output={"ok": True}),
            "deterministic": lambda _: HandlerResult(
                output={"ok": True},
                usage=UsageRecord(model_calls=1),
            ),
        },
        registry=registry,
    )
    run = runner.run(spec=spec, run_id="run-policy", input={})
    assert run.status == "failed"
    assert "requires a model invocation" in (run.nodes["required"].error or "")
    assert "may not invoke a model" in (run.nodes["deterministic"].error or "")


def test_core_agent_pack_is_declarative_and_valid() -> None:
    root = Path(__file__).parents[4] / "agent-packs" / "core"
    loaded = load_agent_pack(root)
    assert loaded.manifest.id == "atlas-core-maintenance"
    assert len(loaded.definitions) == 8
    assert loaded.graphs[0].id == "knowledge-maintenance"
    assert loaded.graphs[0].budget.max_model_calls == 4
    assert loaded.manifest.version == "1.5.0"
    assert [node.agent_version for node in loaded.graphs[0].nodes] == [
        "1.1.0",
        "1.3.0",
        "1.2.0",
        "1.1.0",
    ]
    triage = next(graph for graph in loaded.graphs if graph.id == "quality-maintenance-triage")
    assert triage.budget.max_model_calls == 0
    assert triage.nodes[0].agent_version == "1.0.0"
