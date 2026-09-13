"""Store extracted pages for version-bound human review before publication."""
from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    if "extracted_pages_json" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("knowledge_documents")}:
        op.add_column("knowledge_documents", sa.Column("extracted_pages_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("knowledge_documents", "extracted_pages_json")
