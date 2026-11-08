"""Initial private archive, sync checkpoints and owner sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "records",
        sa.Column("dataset", sa.String(80), primary_key=True),
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("day", sa.String(10)),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_index("ix_records_day", "records", ["day"])
    op.create_table(
        "raw_responses",
        sa.Column("dataset", sa.String(80), primary_key=True),
        sa.Column("request_key", sa.String(120), primary_key=True),
        sa.Column("payload", sa.JSON),
        sa.Column("captured_at", sa.Float, nullable=False),
    )
    op.create_table(
        "activity_files",
        sa.Column("activity_id", sa.String(40), primary_key=True),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("dedupe_key", sa.String(20), unique=True),
        sa.Column("start_date", sa.String(10), nullable=False),
        sa.Column("end_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cursor", sa.JSON, nullable=False),
        sa.Column("requests_done", sa.Integer, nullable=False),
        sa.Column("records_saved", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("retry_at", sa.Float, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("finished_at", sa.Float),
    )
    op.create_index("ix_sync_jobs_status", "sync_jobs", ["status"])
    op.create_table(
        "dataset_status",
        sa.Column("name", sa.String(80), primary_key=True),
        sa.Column("last_checked", sa.Float, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("message", sa.Text),
    )
    op.create_table(
        "worker_leases",
        sa.Column("name", sa.String(30), primary_key=True),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.Float, nullable=False),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("successful", sa.Boolean, nullable=False),
    )
    op.create_index("ix_login_attempts_created_at", "login_attempts", ["created_at"])


def downgrade():
    raise RuntimeError("Destructive downgrade disabled. Restore into a separate database instead.")
