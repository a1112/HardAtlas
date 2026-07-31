from hardatlas_domain import SourceDefinition, evaluate_source_policy


def source(**overrides: object) -> SourceDefinition:
    values = {
        "id": "source-policy-test",
        "version": "1.0.0",
        "name": "Policy test source",
        "kind": "api",
        "base_url": "https://knowledge.example.org/data",
        "allowed_hosts": ["knowledge.example.org"],
        "trust_tier": "authoritative",
        "license_id": "CC-BY-4.0",
        "license_status": "allowed",
        "robots_policy": "explicit-api",
        "robots_status": "allowed",
        "allowed_media_types": ["application/json"],
        "status": "active",
    }
    return SourceDefinition(**{**values, **overrides})


def test_model_processing_is_denied_by_default() -> None:
    decision = evaluate_source_policy(source())
    assert decision.allowed is True
    assert decision.model_processing_allowed is False


def test_model_processing_requires_both_approval_and_source_policy() -> None:
    approved = evaluate_source_policy(
        source(model_processing_policy="approved-gateway")
    )
    blocked = evaluate_source_policy(
        source(
            model_processing_policy="approved-gateway",
            license_status="review-required",
        )
    )
    assert approved.model_processing_allowed is True
    assert blocked.allowed is False
    assert blocked.model_processing_allowed is False


def test_source_definition_rejects_unsafe_base_url_components() -> None:
    invalid_cases = [
        ("https://user:pass@knowledge.example.org/data", "must not include credentials"),
        ("https://knowledge.example.org/data?query=1", "must not include query parameters"),
        ("https://knowledge.example.org/data#section", "must not include fragment"),
    ]

    for base_url, message in invalid_cases:
        try:
            source(base_url=base_url)
        except ValueError as error:
            assert message in str(error)
        else:
            raise AssertionError("unsafe base_url must be rejected")


def test_source_definition_rejects_private_loopback_host_urls() -> None:
    try:
        source(base_url="http://127.0.0.1/species", allowed_hosts=["127.0.0.1"])
    except ValueError as error:
        assert "host is blocked" in str(error)
    else:
        raise AssertionError("private host base_url must be rejected")
