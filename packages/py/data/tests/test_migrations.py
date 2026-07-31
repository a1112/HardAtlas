from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_alembic_upgrade_creates_current_kernel_and_is_repeatable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "migration-test.sqlite3"
    package_root = Path(__file__).resolve().parents[1]
    config = Config(str(package_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+pysqlite:///{database_path}",
    )

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "agent_graph_schedule",
        "agent_runtime",
        "audit_event",
        "authoring_draft",
        "device_snapshot",
        "domain_pack",
        "entity_revision",
        "extraction_candidate",
        "knowledge_entity",
        "knowledge_answer",
        "quality_assessment",
        "maintenance_task",
        "maintenance_work_item",
        "saved_collection",
        "saved_collection_item",
        "schema_activation",
        "schema_migration",
        "source_acquisition_job",
        "source_definition",
        "workspace",
    } <= tables
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0007_agent_runtime"
        )
    proposal_columns = {
        column["name"] for column in inspect(engine).get_columns("governed_proposal")
    }
    assert "lock_version" in proposal_columns
