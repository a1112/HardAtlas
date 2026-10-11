from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from fastapi.testclient import TestClient
from hardatlas_api import create_app
from hardatlas_api.auth import ApiAuthenticator, AuthenticationError
from hardatlas_api.config import Settings
from hardatlas_data import KnowledgeRepository
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def repository() -> KnowledgeRepository:
    return KnowledgeRepository(
        create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    )


def dev_headers(subject: str, roles: str) -> dict[str, str]:
    return {
        "x-hardatlas-dev-principal": subject,
        "x-hardatlas-dev-display-name": subject,
        "x-hardatlas-dev-roles": roles,
    }


def test_public_reads_stay_anonymous_but_mutations_require_identity() -> None:
    repo = repository()
    client = TestClient(create_app(repo))
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/search", params={"q": "雪豹"}).status_code == 200
    denied = client.post(
        "/api/v1/sources",
        json={
            "id": "source-denied",
            "version": "1.0.0",
            "name": "Denied",
            "kind": "dataset",
            "baseUrl": "https://data.example.org/items.json",
            "allowedHosts": ["data.example.org"],
            "trustTier": "secondary",
            "licenseId": "test",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert denied.status_code == 401

    audit = client.get(
        "/api/v1/audit-events",
        headers=dev_headers("auditor-one", "auditor"),
    )
    assert audit.status_code == 200
    event = audit.json()[0]
    assert event["actor"]["subject"] == "anonymous"
    assert event["action"] == "source.register"
    assert event["outcome"] == "denied"
    assert repo.verify_audit_chain()


def test_roles_are_least_privilege_and_denials_are_audited() -> None:
    repo = repository()
    client = TestClient(create_app(repo))
    denied = client.post(
        "/api/v1/releases/missing/publish",
        headers=dev_headers("editor-one", "editor"),
    )
    assert denied.status_code == 403
    events = repo.list_audit_events()
    assert events[0].actor.subject == "editor-one"
    assert events[0].metadata["reason"] == "missing permission release.publish"

    allowed_read = client.get(
        "/api/v1/proposals",
        headers=dev_headers("editor-one", "editor"),
    )
    assert allowed_read.status_code == 200


def test_successful_privileged_actions_create_hash_chained_audit_events() -> None:
    repo = repository()
    client = TestClient(create_app(repo))
    response = client.post(
        "/api/v1/sources",
        headers=dev_headers("source-admin-one", "source-admin"),
        json={
            "id": "source-audit-test",
            "version": "1.0.0",
            "name": "Audit test source",
            "kind": "api",
            "baseUrl": "https://api.example.org/data",
            "allowedHosts": ["api.example.org"],
            "trustTier": "primary",
            "licenseId": "open-test",
            "licenseStatus": "allowed",
            "robotsPolicy": "explicit-api",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        },
    )
    assert response.status_code == 200
    event = repo.list_audit_events()[0]
    assert event.actor.subject == "source-admin-one"
    assert event.outcome == "success"
    assert len(event.event_hash) == 64
    assert repo.verify_audit_chain()


def test_oidc_tokens_validate_issuer_audience_signature_and_roles() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    settings = Settings(
        environment="test",
        auth_mode="oidc",
        oidc_issuer="https://identity.example.org",
        oidc_audience="hardatlas-api",
        oidc_jwks_url="https://identity.example.org/jwks",
        oidc_algorithms="RS256",
    )
    authenticator = ApiAuthenticator(
        settings,
        signing_key_resolver=lambda _: public_key,
    )
    client = TestClient(create_app(repository(), authenticator=authenticator))
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "oidc-user-001",
            "name": "OIDC Auditor",
            "email": "auditor@example.org",
            "roles": ["auditor"],
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    me = client.get(
        "/api/v1/me",
        headers={"authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["subject"] == "oidc-user-001"
    assert me.json()["roles"] == ["auditor"]
    assert me.json()["authenticationMethod"] == "oidc"
    assert UUID(me.json()["workspaceId"])

    wrong_audience = jwt.encode(
        {
            "sub": "oidc-user-001",
            "iss": settings.oidc_issuer,
            "aud": "another-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )
    denied = client.get(
        "/api/v1/me",
        headers={"authorization": f"Bearer {wrong_audience}"},
    )
    assert denied.status_code == 401


def test_development_authentication_cannot_start_in_production() -> None:
    settings = Settings(environment="production", auth_mode="development")
    try:
        ApiAuthenticator(settings)
    except ValueError as error:
        assert "forbidden in production" in str(error)
    else:
        raise AssertionError("production accepted development authentication")


@pytest.mark.parametrize("kind", ["malformed", "expired", "wrong_algorithm", "bad_signature"])
def test_oidc_rejects_invalid_tokens_without_fetching_keys(kind: str) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="oidc",
        oidc_issuer="https://identity.example.org",
        oidc_audience="hardatlas-api",
        oidc_algorithms="RS256",
        oidc_clock_skew_seconds=0,
    )
    authenticator = ApiAuthenticator(
        settings, signing_key_resolver=lambda _: private_key.public_key()
    )
    now = datetime.now(UTC)
    claims = {
        "sub": "oidc-user-001",
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
        "iat": now - timedelta(minutes=1),
        "exp": now + timedelta(minutes=5),
    }
    if kind == "malformed":
        token = "not-a-token"
    elif kind == "wrong_algorithm":
        token = jwt.encode(claims, "test-only-hmac-key-" + "x" * 32, algorithm="HS256")
    else:
        if kind == "expired":
            claims["exp"] = now - timedelta(seconds=1)
        signing_key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if kind == "bad_signature"
            else private_key
        )
        token = jwt.encode(claims, signing_key, algorithm="RS256")
    request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    )

    with pytest.raises(AuthenticationError) as denied:
        authenticator.authenticate(request)

    assert denied.value.status_code == 401


@pytest.mark.parametrize("separator", [b"\r", b" "])
def test_pyjwt_rejects_mutated_pem_as_hmac_secret(separator: bytes) -> None:
    key = separator.join([b"-----BEGIN PUBLIC KEY-----", b"YWJj", b"-----END PUBLIC KEY-----"])
    with pytest.raises(jwt.InvalidKeyError):
        jwt.algorithms.HMACAlgorithm(jwt.algorithms.HMACAlgorithm.SHA256).prepare_key(key)
