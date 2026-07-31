"""Persist reproducible evidence-grounded public answers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_knowledge_answers"
down_revision: str | None = "0003_proposal_optimistic_lock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "knowledge_answer" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "knowledge_answer",
        sa.Column("id", sa.String(length=160), primary_key=True),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "document",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()),
                "postgresql",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        op.f("ix_knowledge_answer_question_hash"),
        "knowledge_answer",
        ["question_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_answer_status"),
        "knowledge_answer",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_answer_mode"),
        "knowledge_answer",
        ["mode"],
        unique=False,
    )


def downgrade() -> None:
    if "knowledge_answer" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index(op.f("ix_knowledge_answer_mode"), table_name="knowledge_answer")
    op.drop_index(op.f("ix_knowledge_answer_status"), table_name="knowledge_answer")
    op.drop_index(
        op.f("ix_knowledge_answer_question_hash"),
        table_name="knowledge_answer",
    )
    op.drop_table("knowledge_answer")
