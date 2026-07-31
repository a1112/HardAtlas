"""Add persistent Agent runtime presence and capacity."""

from collections.abc import Sequence

from alembic import op
from hardatlas_data.models import AgentRuntimeRow
from sqlalchemy import inspect

revision: str = "0007_agent_runtime"
down_revision: str | None = "0006_maintenance_work_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "agent_runtime" not in set(inspect(bind).get_table_names()):
        AgentRuntimeRow.__table__.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    if "agent_runtime" in set(inspect(bind).get_table_names()):
        op.drop_table("agent_runtime")
