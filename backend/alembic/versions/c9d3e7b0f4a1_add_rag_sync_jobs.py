"""add rag sync outbox jobs

Revision ID: c9d3e7b0f4a1
Revises: 86a4e086782f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d3e7b0f4a1"
down_revision: Union[str, Sequence[str], None] = "86a4e086782f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_sync_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("rag_sync_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_rag_sync_jobs_record_id"), ["record_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_sync_jobs_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_sync_jobs_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_rag_sync_jobs_next_retry_at"), ["next_retry_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("rag_sync_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_rag_sync_jobs_next_retry_at"))
        batch_op.drop_index(batch_op.f("ix_rag_sync_jobs_status"))
        batch_op.drop_index(batch_op.f("ix_rag_sync_jobs_user_id"))
        batch_op.drop_index(batch_op.f("ix_rag_sync_jobs_record_id"))
    op.drop_table("rag_sync_jobs")
