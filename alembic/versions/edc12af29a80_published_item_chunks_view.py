"""published_item_chunks view

Revision ID: edc12af29a80
Revises: d9972992c8ca
Create Date: 2026-10-01 15:22:00.620844

Hand-written: a view is not a SQLAlchemy-mapped construct, so autogenerate
neither emits it nor reports it in `alembic check`. Its SQL is written out
here rather than imported, for the same reason as the baseline's: a migration
is a frozen record of what a database was given.

The view is the published filter. item_chunks holds a vector for every
document, drafts included; this exposes only the published ones, and the chat
layer (app/chat/) reads this view, never item_chunks.
"""
from typing import Sequence, Union

from alembic import op


CREATE_VIEW_SQL = """
CREATE OR REPLACE VIEW published_item_chunks AS
SELECT c.id AS chunk_id, c.item_id AS source_id, i.crop_id, i.topic,
       c.content, c.embedding
FROM item_chunks c JOIN items i ON i.id = c.item_id
WHERE i.published IS true;
"""

# revision identifiers, used by Alembic.
revision: str = 'edc12af29a80'
down_revision: Union[str, Sequence[str], None] = 'd9972992c8ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CREATE_VIEW_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW published_item_chunks")
