from hardatlas_data import KnowledgeRepository
from hardatlas_data.models import AuditEventRow
from hardatlas_domain import Principal
from sqlalchemy import create_engine


def test_audit_events_form_a_tamper_evident_hash_chain() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    actor = Principal(
        subject="publisher-one",
        display_name="Publisher One",
        roles=["publisher"],
        authentication_method="development",
    )
    first = repository.append_audit_event(
        actor=actor,
        action="release.stage",
        resource_type="release",
        resource_id="release-001",
        outcome="success",
        request_id="request-001",
    )
    second = repository.append_audit_event(
        actor=actor,
        action="release.publish",
        resource_type="release",
        resource_id="release-001",
        outcome="success",
        request_id="request-002",
    )
    assert second.previous_hash == first.event_hash
    assert repository.verify_audit_chain()

    with repository.sessions.begin() as session:
        row = session.get(AuditEventRow, first.id)
        assert row is not None
        tampered = dict(row.document)
        tampered["action"] = "release.rollback"
        row.document = tampered
    assert repository.verify_audit_chain() is False
