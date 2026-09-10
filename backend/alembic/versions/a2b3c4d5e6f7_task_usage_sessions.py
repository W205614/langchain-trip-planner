"""Persist task usage and session revocation version."""
from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    for table, column in (
        ("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")),
        ("trip_tasks", sa.Column("usage_json", sa.Text(), nullable=False, server_default="{}")),
    ):
        if column.name not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}:
            op.add_column(table, column)


def downgrade():
    op.drop_column("trip_tasks", "usage_json")
    op.drop_column("users", "token_version")
