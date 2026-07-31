"""Persist versioned entity quality assessments and maintenance tasks."""

from collections.abc import Sequence

from alembic import op
from hardatlas_data.models import MaintenanceTaskRow, QualityAssessmentRow
from sqlalchemy import inspect

revision: str = "0005_quality_maintenance"
down_revision: str | None = "0004_knowledge_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "quality_assessment" not in tables:
        QualityAssessmentRow.__table__.create(bind=bind)
    if "maintenance_task" not in tables:
        MaintenanceTaskRow.__table__.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "maintenance_task" in tables:
        op.drop_table("maintenance_task")
    if "quality_assessment" in tables:
        op.drop_table("quality_assessment")
