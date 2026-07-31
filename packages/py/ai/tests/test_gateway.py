import json
from pathlib import Path

import httpx
import pytest
from hardatlas_ai import (
    AgentGraphRunner,
    HandlerContext,
    MemoryCheckpointStore,
    ModelGatewayConfig,
    ModelJSONRequest,
    OpenAICompatibleModelGateway,
    core_handlers,
    entity_resolution_handler,
    extraction_handler,
    load_agent_pack,
)
from pydantic import ValidationError


def test_gateway_uses_only_configured_proxy_and_records_provenance() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["agent"] = request.headers.get("x-hardatlas-agent-id")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-request-id": "proxy-request-123"},
            json={
                "id": "response-1",
                "model": "approved-model-v7",
                "provider": "internal-router",
                "output_text": '{"value":"银杏"}',
                "usage": {
                    "input_tokens": 41,
                    "output_tokens": 7,
                    "cost_microusd": 23,
                },
            },
        )

    gateway = OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url="https://models.example.internal/gateway",
            model="encyclopedia-maintainer",
            api_key="server-secret",
            gateway_id="test-proxy",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = gateway.generate_json(
        ModelJSONRequest(
            agent_id="agent-content-extractor",
            instructions="Return the value.",
            input_document={"sourceExcerpt": "银杏"},
            output_schema_name="candidate",
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
    )

    assert captured["url"] == "https://models.example.internal/gateway/v1/responses"
    assert captured["authorization"] == "Bearer server-secret"
    assert captured["agent"] == "agent-content-extractor"
    assert captured["payload"]["model"] == "encyclopedia-maintainer"
    assert result.output == {"value": "银杏"}
    assert result.invocation.gateway_base_url == "https://models.example.internal/gateway"
    assert result.invocation.gateway_id == "test-proxy"
    assert result.invocation.provider == "internal-router"
    assert result.invocation.model == "approved-model-v7"
    assert result.invocation.request_id == "proxy-request-123"
    assert result.usage.model_calls == 1
    assert result.usage.input_tokens == 41
    assert "server-secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com",
        "https://tenant.openai.azure.com",
        "https://user:secret@models.example.internal",
        "https://models.example.internal?token=secret",
    ],
)
def test_gateway_rejects_direct_or_secret_bearing_urls_by_default(
    base_url: str,
) -> None:
    with pytest.raises(ValidationError):
        ModelGatewayConfig(base_url=base_url, model="model-alias")


def test_optional_model_handler_is_explicitly_deterministic_without_gateway() -> None:
    result = extraction_handler(
        HandlerContext(
            run_id="run-1",
            graph_id="graph-1",
            graph_version="1",
            node_id="extract",
            input={
                "label": "Ginkgo biloba",
                "fieldPath": "/summary",
                "proposedValue": "银杏",
                "citationId": "citation-1",
                "confidence": 0.9,
            },
            dependency_outputs={},
            config={},
        )
    )
    assert result.output["executionMode"] == "deterministic"
    assert result.usage.model_calls == 0
    assert result.model_invocations == []


def test_optional_handlers_call_proxy_and_restrict_resolution_candidates() -> None:
    responses = iter(
        [
            {
                "output_json": {"proposedValue": "银杏是银杏科植物。", "confidence": 0.97},
                "usage": {"input_tokens": 20, "output_tokens": 8},
            },
            {
                "output_json": {
                    "entityId": "ginkgo",
                    "normalizedLabel": "ginkgo biloba",
                    "rationale": "学名与候选条目一致",
                },
                "usage": {"input_tokens": 12, "output_tokens": 6},
            },
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    gateway = OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url="http://model-proxy.internal",
            model="maintainer",
        ),
        transport=httpx.MockTransport(handler),
    )
    context = HandlerContext(
        run_id="run-2",
        graph_id="graph-1",
        graph_version="1",
        node_id="extract",
        input={
            "label": "Ginkgo biloba",
            "fieldPath": "/summary",
            "proposedValue": "银杏",
            "citationId": "citation-1",
            "confidence": 0.8,
            "sourceExcerpt": "银杏是银杏科、银杏属植物。",
            "modelProcessingAllowed": True,
            "entityId": "ginkgo",
            "candidateEntityIds": ["ginkgo", "ginkgo-fossil"],
        },
        dependency_outputs={},
        config={},
    )
    extraction = extraction_handler(context, gateway)
    resolution = entity_resolution_handler(
        context.model_copy(
            update={
                "node_id": "resolve",
                "dependency_outputs": {"extract": extraction.output},
            }
        ),
        gateway,
    )

    assert extraction.output["executionMode"] == "model"
    assert extraction.output["citationId"] == "citation-1"
    assert extraction.usage.model_calls == 1
    assert resolution.output["resolutionMode"] == "model"
    assert resolution.output["entityId"] == "ginkgo"
    assert resolution.usage.model_calls == 1


def test_failed_proxy_calls_are_checkpointed_and_count_against_budget() -> None:
    calls = 0

    def unavailable_proxy(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            headers={"x-request-id": f"failed-{calls}"},
            json={"error": "sensitive provider response must not be persisted"},
        )

    gateway = OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url="https://proxy.example.internal",
            model="maintainer",
            gateway_id="approved-proxy",
        ),
        transport=httpx.MockTransport(unavailable_proxy),
    )
    loaded = load_agent_pack(Path(__file__).parents[4] / "agent-packs" / "core")
    graph = loaded.graphs[0]
    run = AgentGraphRunner(
        checkpoints=MemoryCheckpointStore(),
        handlers=core_handlers(gateway),
        registry=loaded.registry(),
    ).run(
        spec=graph,
        run_id="run-failed-proxy",
        input={
            "sourceId": "source-approved",
            "snapshotHash": "sha256:test",
            "entityId": "entity-ginkgo",
            "candidateEntityIds": [],
            "label": "银杏",
            "fieldPath": "/summary",
            "proposedValue": "候选",
            "sourceExcerpt": "银杏是银杏科植物。",
            "citationId": "citation-1",
            "citationAlreadyPresent": True,
            "currentRevisionId": "revision-ginkgo-test",
            "currentValuePresent": True,
            "currentValue": "旧候选",
            "confidence": 0.9,
            "modelProcessingAllowed": True,
        },
    )

    assert run.status == "failed"
    assert calls == 3
    assert run.usage.model_calls == 3
    assert len(run.model_invocations) == 3
    assert [item.request_id for item in run.model_invocations] == [
        "failed-1",
        "failed-2",
        "failed-3",
    ]
    assert all(item.status == "failed" for item in run.model_invocations)
    assert all(item.error_code == "model_gateway_http_503" for item in run.model_invocations)
    assert "sensitive provider response" not in run.model_dump_json()
