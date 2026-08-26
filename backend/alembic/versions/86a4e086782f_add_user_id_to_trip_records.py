"""add user_id to trip_records

Revision ID: 86a4e086782f
Revises: a433f57d0acb
Create Date: 2026-08-26 10:45:02.867845

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '86a4e086782f'
down_revision: Union[str, Sequence[str], None] = 'a433f57d0acb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    trip_records 增加 user_id (归属用户, 历史记录按用户隔离)。
    存量数据(升级前生成的记录)统一归到 user_id=0 (哨兵值, 表示未归属)。
    """
    with op.batch_alter_table('trip_records', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('user_id', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.create_index(batch_op.f('ix_trip_records_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('trip_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_trip_records_user_id'))
        batch_op.drop_column('user_id')

    # ### end Alembic commands ###
