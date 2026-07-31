from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentGraphModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: "".join(
            word.capitalize() if index else word for index, word in enumerate(value.split("_"))
        ),
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class AgentNodeSpec(AgentGraphModel):
    id: str
    handler_key: str | None = None
    agent_id: str | None = None
    agent_version: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    max_attempts: int = Field(default=3, ge=1, le=10)
    required: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_execution_target(self) -> "AgentNodeSpec":
        if not self.handler_key and not self.agent_id:
            raise ValueError("agent graph node requires handler_key or agent_id")
        if self.agent_version and not self.agent_id:
            raise ValueError("agent_version requires agent_id")
        return self


class RunBudget(AgentGraphModel):
    max_attempts_per_node: int = Field(default=3, ge=1, le=20)
    max_total_attempts: int = Field(default=32, ge=1, le=10_000)
    max_model_calls: int = Field(default=16, ge=0, le=100_000)
    max_input_tokens: int = Field(default=200_000, ge=0)
    max_output_tokens: int = Field(default=50_000, ge=0)
    max_cost_microusd: int = Field(default=5_000_000, ge=0)
    deadline_seconds: int = Field(default=900, ge=1, le=86_400)


class UsageRecord(AgentGraphModel):
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)

    def add(self, other: "UsageRecord") -> "UsageRecord":
        return UsageRecord(
            model_calls=self.model_calls + other.model_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_microusd=self.cost_microusd + other.cost_microusd,
        )


class ModelInvocationRecord(AgentGraphModel):
    gateway_id: str
    gateway_base_url: str
    provider: str
    model: str
    request_id: str
    agent_id: str
    status: Literal["succeeded", "failed"] = "succeeded"
    error_code: str | None = None


class HandlerExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        usage: UsageRecord | None = None,
        model_invocations: list[ModelInvocationRecord] | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage or UsageRecord()
        self.model_invocations = model_invocations or []


class AgentGraphSpec(AgentGraphModel):
    id: str
    version: str
    name: str | None = None
    description: str | None = None
    domain_pack_ids: list[str] = Field(default_factory=list)
    trigger_types: list[
        Literal["manual", "source-change", "schedule", "schema-change"]
    ] = Field(default_factory=lambda: ["manual"])
    budget: RunBudget = Field(default_factory=RunBudget)
    nodes: list[AgentNodeSpec]

    @model_validator(mode="after")
    def validate_graph(self) -> "AgentGraphSpec":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("agent graph node ids must be unique")
        known = set(node_ids)
        for node in self.nodes:
            if node.max_attempts > self.budget.max_attempts_per_node:
                raise ValueError(
                    f"agent graph node {node.id} exceeds per-node attempt budget"
                )
            missing = set(node.depends_on) - known
            if missing:
                raise ValueError(
                    f"agent graph node {node.id} has unknown dependencies: {sorted(missing)}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {node.id: node.depends_on for node in self.nodes}

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("agent graph must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in dependencies[node_id]:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)
        return self


class HandlerContext(AgentGraphModel):
    run_id: str
    graph_id: str
    graph_version: str
    node_id: str
    input: dict[str, Any]
    dependency_outputs: dict[str, dict[str, Any]]
    config: dict[str, Any]


class HandlerResult(AgentGraphModel):
    output: dict[str, Any] = Field(default_factory=dict)
    proposal_ids: list[str] = Field(default_factory=list)
    usage: UsageRecord = Field(default_factory=UsageRecord)
    model_invocations: list[ModelInvocationRecord] = Field(default_factory=list)


class NodeRun(AgentGraphModel):
    node_id: str
    status: Literal["queued", "running", "completed", "failed", "blocked"] = "queued"
    attempts: int = 0
    output: dict[str, Any] = Field(default_factory=dict)
    proposal_ids: list[str] = Field(default_factory=list)
    model_invocations: list[ModelInvocationRecord] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class GraphRun(AgentGraphModel):
    id: str
    graph_id: str
    graph_version: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    input: dict[str, Any] = Field(default_factory=dict)
    nodes: dict[str, NodeRun]
    proposal_ids: list[str] = Field(default_factory=list)
    usage: UsageRecord = Field(default_factory=UsageRecord)
    model_invocations: list[ModelInvocationRecord] = Field(default_factory=list)
    budget_exhausted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class GraphRunSchedule(AgentGraphModel):
    id: str
    graph_id: str
    graph_version: str
    trigger_type: Literal["manual", "source-change", "schedule", "schema-change"]
    status: Literal[
        "queued",
        "dispatched",
        "running",
        "completed",
        "failed",
        "canceled",
    ] = "queued"
    input: dict[str, Any]
    requested_by: str
    idempotency_key: str
    budget: RunBudget
    run_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CheckpointStore(Protocol):
    def load(self, run_id: str) -> GraphRun | None: ...

    def save(self, run: GraphRun) -> None: ...


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self.runs: dict[str, GraphRun] = {}

    def load(self, run_id: str) -> GraphRun | None:
        run = self.runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    def save(self, run: GraphRun) -> None:
        self.runs[run.id] = run.model_copy(deep=True)


AgentHandler = Callable[[HandlerContext], HandlerResult]


class AgentDefinition(AgentGraphModel):
    id: str
    version: str
    name: str
    role: str
    handler_key: str
    description: str
    capabilities: list[str]
    allowed_proposal_types: list[
        Literal["content", "relation", "schema", "translation", "merge"]
    ] = Field(default_factory=list)
    maximum_risk: Literal["low", "medium", "high", "critical"] = "medium"
    model_policy: Literal["deterministic", "optional-model", "model-required"] = (
        "deterministic"
    )
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)


class AgentRegistry:
    def __init__(
        self,
        definitions: list[AgentDefinition] | None = None,
        graphs: list[AgentGraphSpec] | None = None,
    ) -> None:
        self.definitions: dict[str, AgentDefinition] = {}
        self.graphs: dict[str, AgentGraphSpec] = {}
        for definition in definitions or []:
            self.register_definition(definition)
        for graph in graphs or []:
            self.register_graph(graph)

    def register_definition(self, definition: AgentDefinition) -> None:
        for contract_name, contract in (
            ("input", definition.input_contract),
            ("output", definition.output_contract),
        ):
            try:
                Draft202012Validator.check_schema(contract)
            except SchemaError as error:
                raise ValueError(
                    f"agent {definition.id} has an invalid {contract_name} contract: "
                    f"{error.message}"
                ) from error
        existing = self.definitions.get(definition.id)
        if existing and existing != definition:
            raise ValueError("agent definition id is already registered")
        self.definitions[definition.id] = definition.model_copy(deep=True)

    def register_graph(self, graph: AgentGraphSpec) -> None:
        for node in graph.nodes:
            if not node.agent_id:
                continue
            definition = self.definitions.get(node.agent_id)
            if definition is None:
                raise ValueError(
                    f"agent graph node {node.id} references unknown agent: {node.agent_id}"
                )
            if node.agent_version and node.agent_version != definition.version:
                raise ValueError(
                    f"agent graph node {node.id} requires {node.agent_id} "
                    f"version {node.agent_version}, registered version is "
                    f"{definition.version}"
                )
            if node.handler_key and node.handler_key != definition.handler_key:
                raise ValueError(
                    f"agent graph node {node.id} handler conflicts with agent definition"
                )
        existing = self.graphs.get(graph.id)
        if existing and existing != graph:
            raise ValueError("agent graph id is already registered")
        self.graphs[graph.id] = graph.model_copy(deep=True)

    def handler_key(self, node: AgentNodeSpec) -> str:
        if node.agent_id:
            try:
                return self.definitions[node.agent_id].handler_key
            except KeyError as error:
                raise ValueError(f"agent is not registered: {node.agent_id}") from error
        if node.handler_key:
            return node.handler_key
        raise ValueError("agent node has no execution target")

    def validate_graph_input(
        self,
        graph: AgentGraphSpec,
        input_document: dict[str, Any],
    ) -> None:
        for node in graph.nodes:
            if not node.agent_id:
                continue
            definition = self.definitions[node.agent_id]
            errors = sorted(
                Draft202012Validator(definition.input_contract).iter_errors(
                    input_document
                ),
                key=lambda error: list(error.absolute_path),
            )
            if errors:
                raise ValueError(
                    f"agent {definition.id} input contract failed: "
                    f"{errors[0].message}"
                )

    def validate_agent_output(
        self,
        node: AgentNodeSpec,
        output_document: dict[str, Any],
    ) -> None:
        if not node.agent_id:
            return
        definition = self.definitions[node.agent_id]
        errors = sorted(
            Draft202012Validator(definition.output_contract).iter_errors(
                output_document
            ),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            raise ValueError(
                f"agent {definition.id} output contract failed: "
                f"{errors[0].message}"
            )

    def validate_proposal(
        self,
        node: AgentNodeSpec,
        *,
        proposal_type: Literal[
            "content",
            "relation",
            "schema",
            "translation",
            "merge",
        ],
        risk: Literal["low", "medium", "high", "critical"],
    ) -> None:
        if not node.agent_id:
            return
        definition = self.definitions[node.agent_id]
        if proposal_type not in definition.allowed_proposal_types:
            raise ValueError(
                f"agent {definition.id} may not create {proposal_type} proposals"
            )
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if risk_order[risk] > risk_order[definition.maximum_risk]:
            raise ValueError(
                f"agent {definition.id} may not create {risk} risk proposals"
            )


class AgentGraphRunner:
    def __init__(
        self,
        *,
        handlers: dict[str, AgentHandler],
        checkpoints: CheckpointStore,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.handlers = handlers
        self.checkpoints = checkpoints
        self.registry = registry or AgentRegistry()

    @staticmethod
    def _budget_violation(
        run: GraphRun,
        spec: AgentGraphSpec,
        *,
        before_attempt: bool = False,
    ) -> str | None:
        total_attempts = sum(node.attempts for node in run.nodes.values())
        if total_attempts > spec.budget.max_total_attempts or (
            before_attempt and total_attempts >= spec.budget.max_total_attempts
        ):
            return "maximum total attempts exceeded"
        elapsed = (datetime.now(UTC) - run.created_at).total_seconds()
        if elapsed > spec.budget.deadline_seconds:
            return "run deadline exceeded"
        if run.usage.model_calls > spec.budget.max_model_calls:
            return "model call budget exceeded"
        if run.usage.input_tokens > spec.budget.max_input_tokens:
            return "input token budget exceeded"
        if run.usage.output_tokens > spec.budget.max_output_tokens:
            return "output token budget exceeded"
        if run.usage.cost_microusd > spec.budget.max_cost_microusd:
            return "cost budget exceeded"
        return None

    def run(
        self,
        *,
        spec: AgentGraphSpec,
        run_id: str,
        input: dict[str, Any],
    ) -> GraphRun:
        self.registry.validate_graph_input(spec, input)
        run = self.checkpoints.load(run_id)
        if run is None:
            run = GraphRun(
                id=run_id,
                graph_id=spec.id,
                graph_version=spec.version,
                input=input,
                nodes={node.id: NodeRun(node_id=node.id) for node in spec.nodes},
            )
        elif run.graph_id != spec.id or run.graph_version != spec.version:
            raise ValueError("checkpoint graph identity does not match requested graph")
        elif run.input != input:
            raise ValueError("checkpoint input does not match requested run input")

        run.status = "running"
        run.completed_at = None
        self.checkpoints.save(run)
        by_id = {node.id: node for node in spec.nodes}

        while True:
            pending = [
                by_id[node_id]
                for node_id, state in run.nodes.items()
                if state.status in {"queued", "failed"}
                and state.attempts < by_id[node_id].max_attempts
            ]
            if not pending:
                break

            made_progress = False
            for node in pending:
                state = run.nodes[node.id]
                budget_violation = self._budget_violation(
                    run,
                    spec,
                    before_attempt=True,
                )
                if budget_violation:
                    run.budget_exhausted = True
                    state.status = "blocked"
                    state.error = budget_violation
                    self.checkpoints.save(run)
                    made_progress = True
                    continue
                dependency_states = [run.nodes[item] for item in node.depends_on]
                if any(item.status in {"queued", "running"} for item in dependency_states):
                    continue
                if any(
                    run.nodes[dependency].status == "failed"
                    and run.nodes[dependency].attempts < by_id[dependency].max_attempts
                    for dependency in node.depends_on
                ):
                    continue
                if any(item.status in {"failed", "blocked"} for item in dependency_states):
                    state.status = "blocked"
                    state.error = "dependency did not complete"
                    self.checkpoints.save(run)
                    made_progress = True
                    continue

                made_progress = True
                try:
                    handler_key = self.registry.handler_key(node)
                except ValueError as error:
                    state.status = "failed"
                    state.error = str(error)
                    state.attempts = node.max_attempts
                    self.checkpoints.save(run)
                    continue
                handler = self.handlers.get(handler_key)
                if handler is None:
                    state.status = "failed"
                    state.error = f"handler is not registered: {handler_key}"
                    state.attempts = node.max_attempts
                    self.checkpoints.save(run)
                    continue

                state.status = "running"
                state.attempts += 1
                state.started_at = state.started_at or datetime.now(UTC)
                state.error = None
                self.checkpoints.save(run)
                try:
                    result = handler(
                        HandlerContext(
                            run_id=run.id,
                            graph_id=spec.id,
                            graph_version=spec.version,
                            node_id=node.id,
                            input=run.input,
                            dependency_outputs={
                                dependency: run.nodes[dependency].output
                                for dependency in node.depends_on
                            },
                            config=node.config,
                        )
                    )
                    self.registry.validate_agent_output(node, result.output)
                    if node.agent_id:
                        model_policy = self.registry.definitions[
                            node.agent_id
                        ].model_policy
                        if (
                            model_policy == "model-required"
                            and result.usage.model_calls == 0
                        ):
                            raise ValueError(
                                f"agent {node.agent_id} requires a model invocation"
                            )
                        if (
                            model_policy == "deterministic"
                            and result.usage.model_calls > 0
                        ):
                            raise ValueError(
                                f"deterministic agent {node.agent_id} may not invoke a model"
                            )
                except HandlerExecutionError as error:
                    run.usage = run.usage.add(error.usage)
                    state.model_invocations.extend(error.model_invocations)
                    run.model_invocations = [
                        invocation
                        for item in run.nodes.values()
                        for invocation in item.model_invocations
                    ]
                    state.status = "failed"
                    state.error = f"{type(error).__name__}: {error}"
                    if self._budget_violation(run, spec):
                        run.budget_exhausted = True
                    self.checkpoints.save(run)
                    continue
                except Exception as error:  # handlers are an isolation boundary
                    state.status = "failed"
                    state.error = f"{type(error).__name__}: {error}"
                    self.checkpoints.save(run)
                    continue

                run.usage = run.usage.add(result.usage)
                budget_violation = self._budget_violation(run, spec)
                if budget_violation:
                    run.budget_exhausted = True
                    state.status = "failed"
                    state.error = budget_violation
                    self.checkpoints.save(run)
                    continue
                state.status = "completed"
                state.output = result.output
                state.proposal_ids = result.proposal_ids
                state.model_invocations = result.model_invocations
                state.completed_at = datetime.now(UTC)
                run.proposal_ids = sorted(
                    {
                        proposal_id
                        for item in run.nodes.values()
                        for proposal_id in item.proposal_ids
                    }
                )
                run.model_invocations = [
                    invocation
                    for item in run.nodes.values()
                    for invocation in item.model_invocations
                ]
                self.checkpoints.save(run)

            if not made_progress:
                break

        required_states = [run.nodes[node.id] for node in spec.nodes if node.required]
        if all(state.status == "completed" for state in required_states):
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
        else:
            run.status = "failed"
            run.completed_at = datetime.now(UTC)
        self.checkpoints.save(run)
        return run
