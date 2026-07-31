"""Create the Atlas knowledge kernel and private workspace boundary."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from hardatlas_data.models import Base

revision: str = "0001_atlas_kernel"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        _configure_postgresql_private_tables()
    else:
        op.create_table(
            "device_snapshot",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(as_uuid=False),
                sa.ForeignKey("workspace.id"),
                nullable=False,
            ),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_payload", sa.JSON(), nullable=False),
            sa.Column("data_version", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def _configure_postgresql_private_tables() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS device_snapshot (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id uuid NOT NULL REFERENCES workspace(id),
          captured_at timestamptz NOT NULL,
          approved_payload jsonb NOT NULL,
          data_version text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    for table in (
        "device_snapshot",
        "saved_collection",
        "saved_collection_item",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_device_isolation ON device_snapshot
          USING (
            workspace_id =
              nullif(current_setting('app.workspace_id', true), '')::uuid
          )
          WITH CHECK (
            workspace_id =
              nullif(current_setting('app.workspace_id', true), '')::uuid
          )
        """
    )
    op.execute(
        """
        CREATE POLICY saved_collection_workspace_isolation ON saved_collection
          USING (
            workspace_id =
              nullif(current_setting('app.workspace_id', true), '')::uuid
          )
          WITH CHECK (
            workspace_id =
              nullif(current_setting('app.workspace_id', true), '')::uuid
          )
        """
    )
    op.execute(
        """
        CREATE POLICY saved_collection_item_workspace_isolation
          ON saved_collection_item
          USING (
            workspace_id =
              nullif(current_setting('app.workspace_id', true), '')::uuid
          )
          WITH CHECK (
            workspace_id =
              nullif(current_setting('app.workspace_id', true), '')::uuid
          )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS workspace_device_isolation "
            "ON device_snapshot"
        )
        op.execute(
            "DROP POLICY IF EXISTS saved_collection_workspace_isolation "
            "ON saved_collection"
        )
        op.execute(
            "DROP POLICY IF EXISTS saved_collection_item_workspace_isolation "
            "ON saved_collection_item"
        )
    op.drop_table("device_snapshot")
    Base.metadata.drop_all(bind=bind)
