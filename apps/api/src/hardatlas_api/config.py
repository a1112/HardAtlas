from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HARDATLAS_",
        extra="ignore",
    )

    environment: str = "development"
    build_sha: str = "local"
    data_version: str = "atlas-2026.07.29"
    schema_version: str = "schema-2.2.0"
    rule_version: str = "rules-2026.07"
    database_url: str = "sqlite+pysqlite:///./.local/hardatlas.db"
    search_backend: str = "memory"
    opensearch_url: str = "http://localhost:9200"
    opensearch_index_alias: str = "atlas-knowledge-read"
    auth_mode: str = "development"
    auto_create_schema: bool | None = None
    seed_fixture_content: bool = True
    oidc_issuer: str = "http://localhost:8080/realms/hardatlas"
    oidc_audience: str = "hardatlas-api"
    oidc_jwks_url: str = (
        "http://localhost:8080/realms/hardatlas/protocol/openid-connect/certs"
    )
    oidc_algorithms: str = "RS256"
    oidc_roles_claim: str = "roles"
    oidc_workspace_claim: str = "workspace_id"
    oidc_clock_skew_seconds: int = 30
    cors_origins: str = (
        "http://localhost:1420,http://127.0.0.1:1420,"
        "http://localhost:3000,http://localhost:3001,"
        "https://tauri.localhost,tauri://localhost"
    )
    enable_hardware_fixture_extension: bool = True
    agent_pack_paths: str = "agent-packs/core"
    model_gateway_url: str = ""
    model_gateway_model: str = ""
    model_gateway_api_key: SecretStr = SecretStr("")
    model_gateway_endpoint_path: str = "/v1/responses"
    model_gateway_timeout_seconds: float = 60
    model_gateway_id: str = "configured-proxy"
    model_allow_direct_provider: bool = False
    public_answer_model_enabled: bool = False
    parser_paths: str = "parser-packs/core"
    domain_pack_paths: str = (
        "domain-packs/core,domain-packs/animals,"
        "domain-packs/plants,domain-packs/electronics,domain-packs/oceanography"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
