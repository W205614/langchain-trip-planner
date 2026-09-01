"""add user travel preferences and knowledge source tier

Revision ID: e7f3b0a9c1d2
Revises: d8e7f6a5b4c3
"""

from alembic import op
import sqlalchemy as sa

revision = "e7f3b0a9c1d2"
down_revision = "d8e7f6a5b4c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_travel_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("preferences", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("transportation", sa.String(length=32), nullable=False, server_default="公共交通"),
        sa.Column("accommodation", sa.String(length=32), nullable=False, server_default="经济型酒店"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_user_travel_preferences_user_id", "user_travel_preferences", ["user_id"], unique=True)
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_tier", sa.String(length=16), nullable=False, server_default="community"))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        batch_op.drop_column("source_tier")
    op.drop_index("ix_user_travel_preferences_user_id", table_name="user_travel_preferences")
    op.drop_table("user_travel_preferences")
