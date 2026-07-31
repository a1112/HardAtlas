"""Add optimistic concurrency to governed proposals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_proposal_optimistic_lock"
down_revision: str | None = "0002_authoring_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("governed_proposal")
    }
    if "lock_version" not in columns:
        op.add_column(
            "governed_proposal",
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )

    proposal_table = sa.table(
        "governed_proposal",
        sa.column("id", sa.String()),
        sa.column("lock_version", sa.Integer()),
        sa.column("document", sa.JSON()),
    )
    rows = list(
        bind.execute(
            sa.select(
                proposal_table.c.id,
                proposal_table.c.document,
            )
        ).mappings()
    )
    for row in rows:
        document = dict(row["document"])
        document["version"] = 1
        bind.execute(
            sa.update(proposal_table)
            .where(proposal_table.c.id == row["id"])
            .values(lock_version=1, document=document)
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("governed_proposal")
    }
    if "lock_version" in columns:
        op.drop_column("governed_proposal", "lock_version")
