"""Persist Garmin cooldown across worker restarts and queued imports."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    op.add_column("worker_leases", sa.Column("cooldown_until", sa.Float, nullable=False, server_default="0"))


def downgrade():
    raise RuntimeError("Destructive downgrade disabled")
