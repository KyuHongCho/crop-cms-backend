"""member role

Revision ID: a7f3c1d90b52
Revises: 82c1cba61444
Create Date: 2026-10-06 09:00:00.000000

Text + CHECK rather than a native enum: a widened CHECK can use its new value
in the same transaction, an enum value cannot. Existing rows become 'member'.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7f3c1d90b52'
down_revision: Union[str, Sequence[str], None] = '82c1cba61444'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('members', sa.Column('role', sa.Text(), server_default=sa.text("'member'"), nullable=False))
    op.create_check_constraint('role_valid', 'members', "role IN ('member', 'editor', 'admin')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('role_valid', 'members', type_='check')
    op.drop_column('members', 'role')
