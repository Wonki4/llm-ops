"""Add key sequence table for sequential key ID generation.

Revision ID: 006_key_sequence
Revises: 005_catalog_visible
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa

revision = "006_key_sequence"
down_revision = "005_catalog_visible"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_key_sequence",
        sa.Column("key_seq", sa.Integer, primary_key=True),
    )
    # Seed with starting value so next is 10000
    op.execute("INSERT INTO custom_key_sequence (key_seq) VALUES (9999)")


def downgrade() -> None:
    op.drop_table("custom_key_sequence")
