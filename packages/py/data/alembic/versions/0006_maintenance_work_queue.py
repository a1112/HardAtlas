"""Add the leaseable maintenance work queue."""

from collections.abc import Sequence

from alembic import op
from hardatlas_data.models import MaintenanceWorkItemRow
from sqlalchemy import inspect

revision: str = "0006_maintenance_work_queue"
down_revision: str | None = "0005_quality_maintenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "maintenance_work_item" not in set(inspect(bind).get_table_names()):
        MaintenanceWorkItemRow.__table__.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    if "maintenance_work_item" in set(inspect(bind).get_table_names()):
        op.drop_table("maintenance_work_item")
