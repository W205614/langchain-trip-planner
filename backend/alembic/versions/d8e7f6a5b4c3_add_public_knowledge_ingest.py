"""add public multimodal knowledge ingest

Revision ID: d8e7f6a5b4c3
Revises: c9d3e7b0f4a1
"""

from alembic import op
import sqlalchemy as sa

revision = "d8e7f6a5b4c3"
down_revision = "c9d3e7b0f4a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("submitted_by", sa.Integer(), nullable=False), sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=False), sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False), sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False), sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"), sa.Column("review_note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    for name, columns in (("ix_knowledge_documents_submitted_by", ["submitted_by"]), ("ix_knowledge_documents_reviewed_by", ["reviewed_by"]), ("ix_knowledge_documents_city", ["city"]), ("ix_knowledge_documents_status", ["status"]), ("ix_knowledge_documents_sha256", ["sha256"]), ("ix_knowledge_documents_created_at", ["created_at"])):
        op.create_index(name, "knowledge_documents", columns, unique=False)
    op.create_table(
        "knowledge_ingest_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    for name, columns in (("ix_knowledge_ingest_jobs_document_id", ["document_id"]), ("ix_knowledge_ingest_jobs_status", ["status"]), ("ix_knowledge_ingest_jobs_next_retry_at", ["next_retry_at"])):
        op.create_index(name, "knowledge_ingest_jobs", columns, unique=False)


def downgrade() -> None:
    for name in ("ix_knowledge_ingest_jobs_next_retry_at", "ix_knowledge_ingest_jobs_status", "ix_knowledge_ingest_jobs_document_id"):
        op.drop_index(name, table_name="knowledge_ingest_jobs")
    op.drop_table("knowledge_ingest_jobs")
    for name in ("ix_knowledge_documents_created_at", "ix_knowledge_documents_sha256", "ix_knowledge_documents_status", "ix_knowledge_documents_city", "ix_knowledge_documents_reviewed_by", "ix_knowledge_documents_submitted_by"):
        op.drop_index(name, table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_admin")
