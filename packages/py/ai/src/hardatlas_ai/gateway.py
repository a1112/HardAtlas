import json
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .graph import (
    HandlerExecutionError,
    ModelInvocationRecord,
    UsageRecord,
)


class GatewayModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: "".join(
            word.capitalize() if index else word
            for index, word in enumerate(value.split("_"))
        ),
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class ModelGatewayConfig(GatewayModel):
    base_url: str
    model: str
    api_key: str = Field(default="", repr=False)
    endpoint_path: str = "/v1/responses"
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    gateway_id: str = "configured-proxy"
    allow_direct_provider: bool = False

    @model_validator(mode="after")
    def require_explicit_proxy(self) -> "ModelGatewayConfig":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("model gateway base URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "model gateway base URL must not contain credentials, query, or fragment"
            )
        if not self.model.strip():
            raise ValueError("model gateway model must not be empty")
        hostname = parsed.hostname.casefold()
        direct_provider = hostname == "api.openai.com" or hostname.endswith(
            ".openai.azure.com"
        )
        if direct_provider and not self.allow_direct_provider:
            raise ValueError(
                "direct model provider URL is disabled; configure the approved proxy URL"
            )
        if not self.endpoint_path.startswith("/"):
            raise ValueError("model gateway endpoint path must start with '/'")
        return self

    @property
    def endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.endpoint_path}"


class ModelJSONRequest(GatewayModel):
    agent_id: str
    instructions: str
    input_document: dict[str, Any]
    output_schema_name: str
    output_schema: dict[str, Any]


class ModelJSONResult(GatewayModel):
    output: dict[str, Any]
    invocation: ModelInvocationRecord
    usage: UsageRecord


class ModelGateway(Protocol):
    def generate_json(self, request: ModelJSONRequest) -> ModelJSONResult: ...


class OpenAICompatibleModelGateway:
    """Single audited HTTP boundary for OpenAI-compatible model proxies."""

    def __init__(
        self,
        config: ModelGatewayConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    @staticmethod
    def _output_text(document: dict[str, Any]) -> str:
        direct = document.get("output_text")
        if isinstance(direct, str):
            return direct
        for output in document.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    return content["text"]
        raise ValueError("model gateway response did not contain output text")

    def generate_json(self, request: ModelJSONRequest) -> ModelJSONResult:
        headers = {
            "content-type": "application/json",
            "x-hardatlas-agent-id": request.agent_id,
        }
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "instructions": request.instructions,
            "input": json.dumps(
                request.input_document,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": request.output_schema,
                }
            },
        }
        response: httpx.Response | None = None
        response_document: dict[str, Any] = {}
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.post(
                    self.config.endpoint_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
            parsed_document = response.json()
            if not isinstance(parsed_document, dict):
                raise ValueError("response must be a JSON object")
            response_document = parsed_document
            output_document = response_document.get("output_json")
            if output_document is None:
                output_document = json.loads(
                    self._output_text(response_document)
                )
            if not isinstance(output_document, dict):
                raise ValueError("structured output must be a JSON object")
        except httpx.HTTPStatusError as error:
            self._raise_gateway_error(
                request,
                response=error.response,
                error_code=f"model_gateway_http_{error.response.status_code}",
            )
        except httpx.HTTPError:
            self._raise_gateway_error(
                request,
                response=response,
                error_code="model_gateway_transport_error",
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            self._raise_gateway_error(
                request,
                response=response,
                error_code="model_gateway_invalid_response",
                response_document=response_document,
            )
        usage_document = self._usage_document(response_document)
        request_id = self._request_id(response, response_document)
        return ModelJSONResult(
            output=output_document,
            invocation=ModelInvocationRecord(
                gateway_id=self.config.gateway_id,
                gateway_base_url=self.config.base_url,
                provider=str(response_document.get("provider", "proxy")),
                model=str(response_document.get("model", self.config.model)),
                request_id=request_id,
                agent_id=request.agent_id,
            ),
            usage=UsageRecord(
                model_calls=1,
                input_tokens=int(usage_document.get("input_tokens", 0)),
                output_tokens=int(usage_document.get("output_tokens", 0)),
                cost_microusd=int(usage_document.get("cost_microusd", 0)),
            ),
        )

    @staticmethod
    def _usage_document(
        response_document: dict[str, Any],
    ) -> dict[str, Any]:
        usage = response_document.get("usage", {})
        return usage if isinstance(usage, dict) else {}

    @staticmethod
    def _request_id(
        response: httpx.Response | None,
        response_document: dict[str, Any],
    ) -> str:
        if response is not None:
            header_id = response.headers.get(
                "x-request-id"
            ) or response.headers.get("request-id")
            if header_id:
                return header_id
        return str(response_document.get("id", "unavailable"))

    def _raise_gateway_error(
        self,
        request: ModelJSONRequest,
        *,
        response: httpx.Response | None,
        error_code: str,
        response_document: dict[str, Any] | None = None,
    ) -> None:
        document = response_document or {}
        if response is not None and not document:
            try:
                parsed = response.json()
                document = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                document = {}
        usage_document = self._usage_document(document)
        raise HandlerExecutionError(
            error_code,
            usage=UsageRecord(
                model_calls=1,
                input_tokens=int(usage_document.get("input_tokens", 0)),
                output_tokens=int(usage_document.get("output_tokens", 0)),
                cost_microusd=int(usage_document.get("cost_microusd", 0)),
            ),
            model_invocations=[
                ModelInvocationRecord(
                    gateway_id=self.config.gateway_id,
                    gateway_base_url=self.config.base_url,
                    provider=str(document.get("provider", "proxy")),
                    model=str(document.get("model", self.config.model)),
                    request_id=self._request_id(response, document),
                    agent_id=request.agent_id,
                    status="failed",
                    error_code=error_code,
                )
            ],
        )
