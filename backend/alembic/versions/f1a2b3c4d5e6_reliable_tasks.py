"""Durable tasks, edit versions and publication revisions."""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e7f3b0a9c1d2"
branch_labels = None
depends_on = None


def upgrade():
    # Development SQLite may already have received the additive create_all fallback.
    inspector = sa.inspect(op.get_bind())
    def add(table, column):
        if op.get_bind().dialect.name == "sqlite" and column.name in {c["name"] for c in inspector.get_columns(table)}:
            return
        op.add_column(table, column)
    add("trip_records", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    add("trip_records", sa.Column("quality_json", sa.Text(), nullable=False, server_default="{}"))
    add("knowledge_documents", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    add("knowledge_ingest_jobs", sa.Column("document_version", sa.Integer(), nullable=False, server_default="1"))
    if op.get_bind().dialect.name == "sqlite" and inspector.has_table("trip_tasks"):
        return
    op.create_table("trip_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(255), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_trip_task_user_key"),
    )
    op.create_index("ix_trip_tasks_user_id", "trip_tasks", ["user_id"])
    op.create_index("ix_trip_tasks_status", "trip_tasks", ["status"])


def downgrade():
    op.drop_table("trip_tasks")
    op.drop_column("knowledge_ingest_jobs", "document_version")
    op.drop_column("knowledge_documents", "version")
    op.drop_column("trip_records", "quality_json")
    op.drop_column("trip_records", "version")
