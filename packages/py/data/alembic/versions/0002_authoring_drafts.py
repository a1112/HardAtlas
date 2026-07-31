"""Add private, resumable encyclopedia authoring drafts."""

from collections.abc import Sequence

from alembic import op
from hardatlas_data.models import AuthoringDraftRow
from sqlalchemy import inspect

revision: str = "0002_authoring_drafts"
down_revision: str | None = "0001_atlas_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "authoring_draft" not in inspect(bind).get_table_names():
        AuthoringDraftRow.__table__.create(bind=bind)
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE authoring_draft ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE authoring_draft FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = 'authoring_draft'
                  AND policyname = 'authoring_draft_workspace_isolation'
              ) THEN
                CREATE POLICY authoring_draft_workspace_isolation
                  ON authoring_draft
                  USING (
                    workspace_id =
                      nullif(
                        current_setting('app.workspace_id', true),
                        ''
                      )::uuid
                  )
                  WITH CHECK (
                    workspace_id =
                      nullif(
                        current_setting('app.workspace_id', true),
                        ''
                      )::uuid
                  );
              END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS authoring_draft_workspace_isolation "
            "ON authoring_draft"
        )
    op.drop_table("authoring_draft")
