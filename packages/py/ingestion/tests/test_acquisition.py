import hashlib

import httpx
import pytest
from hardatlas_domain import SourceDefinition, evaluate_source_policy
from hardatlas_ingestion import (
    AcquisitionError,
    S3SnapshotStore,
    acquire_http_source,
)


def source(**overrides: object) -> SourceDefinition:
    document: dict[str, object] = {
        "id": "source-example",
        "version": "1.0.0",
        "name": "Example authoritative source",
        "kind": "website",
        "baseUrl": "https://knowledge.example.org/species",
        "allowedHosts": ["knowledge.example.org"],
        "trustTier": "authoritative",
        "licenseId": "CC-BY-4.0",
        "licenseStatus": "allowed",
        "robotsPolicy": "respect",
        "robotsStatus": "allowed",
        "allowedMediaTypes": ["text/html"],
        "maxBytes": 1024,
        "locales": ["zh-CN"],
        "schedule": "0 3 * * *",
        "status": "active",
    }
    document.update(overrides)
    return SourceDefinition.model_validate(document)


def test_policy_blocks_unapproved_sources_before_network_access() -> None:
    blocked = source(licenseStatus="review-required")
    decision = evaluate_source_policy(blocked)
    assert decision.allowed is False
    assert "license status is review-required" in decision.blockers

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"must not be read")

    with pytest.raises(AcquisitionError, match="license status"):
        acquire_http_source(
            blocked,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    assert calls == 0


def test_acquisition_creates_content_addressed_immutable_snapshot() -> None:
    content = b"<article>Snow leopard</article>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("AtlasSourceMonitor")
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"fixture-v1"',
            },
        )

    result = acquire_http_source(
        source(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    digest = hashlib.sha256(content).hexdigest()
    assert result.content == content
    assert result.snapshot.content_sha256 == digest
    assert result.snapshot.storage_key == f"sources/source-example/{digest}"
    assert result.snapshot.license_id == "CC-BY-4.0"


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("https://evil.example.net/data", "host is not allow-listed"),
        ("http://127.0.0.1/private", "host is not allow-listed"),
        ("https://user:secret@knowledge.example.org/species", "must not include credentials"),
        ("https://knowledge.example.org/species?source=trusted", "must not include query"),
        ("https://knowledge.example.org/species#overview", "must not include fragment"),
    ],
)
def test_acquisition_rejects_unapproved_targets(target: str, message: str) -> None:
    with pytest.raises(AcquisitionError, match=message):
        acquire_http_source(
            source(),
            url=target,
            client=httpx.Client(transport=httpx.MockTransport(lambda _: None)),
        )


def test_acquisition_rejects_private_target_host_even_when_allow_listed() -> None:
    with pytest.raises(AcquisitionError, match="blocked"):
        acquire_http_source(
            source(allowedHosts=["knowledge.example.org", "127.0.0.1"]),
            url="http://127.0.0.1/private",
            client=httpx.Client(transport=httpx.MockTransport(lambda _: None)),
        )


def test_acquisition_enforces_media_type_and_size() -> None:
    wrong_type_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"{}",
                headers={"content-type": "application/json"},
            )
        )
    )
    with pytest.raises(AcquisitionError, match="media type"):
        acquire_http_source(source(), client=wrong_type_client)

    large_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"x" * 8,
                headers={"content-type": "text/html"},
            )
        )
    )
    with pytest.raises(AcquisitionError, match="byte limit"):
        acquire_http_source(source(maxBytes=4), client=large_client)


def test_snapshot_store_uses_content_addressed_source_prefix() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeS3:
        def head_bucket(self, **kwargs: object) -> None:
            calls.append(("head", kwargs))

        def put_object(self, **kwargs: object) -> None:
            calls.append(("put", kwargs))

        def get_object(self, **kwargs: object) -> dict[str, object]:
            calls.append(("get", kwargs))

            class Body:
                def read(self) -> bytes:
                    return b"fixture"

            return {"Body": Body()}

    store = S3SnapshotStore(bucket="atlas", client=FakeS3())
    store.ensure_bucket()
    store.put(
        key=f"sources/source-example/{'a' * 64}",
        content=b"fixture",
        media_type="text/html",
        metadata={"sha256": "a" * 64},
    )
    assert calls[0] == ("head", {"Bucket": "atlas"})
    assert calls[1][1]["ContentType"] == "text/html"
    assert (
        store.get(key=f"sources/source-example/{'a' * 64}")
        == b"fixture"
    )
    with pytest.raises(ValueError, match="outside"):
        store.put(
            key="../escape",
            content=b"x",
            media_type="text/plain",
            metadata={},
        )
