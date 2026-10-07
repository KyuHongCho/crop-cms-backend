"""login throttle

Revision ID: c5e8a2f41d70
Revises: 02e1a03214cf
Create Date: 2026-10-07 12:00:00.000000

Hand-written. login_throttle has no foreign keys and no triggers, like member_invites.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c5e8a2f41d70'
down_revision: Union[str, Sequence[str], None] = '02e1a03214cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('login_throttle',
    sa.Column('email_key', sa.String(length=64), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('window_started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('window_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.CheckConstraint('attempts >= 0', name='attempts_non_negative'),
    sa.PrimaryKeyConstraint('email_key')
    )
    op.create_index('ix_login_throttle_window_started_at', 'login_throttle', ['window_started_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_login_throttle_window_started_at', table_name='login_throttle')
    op.drop_table('login_throttle')
