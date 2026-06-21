"""Link an llm-d stack to a registered ArgoCD connection.

Adds nullable FK custom_llmd_stack.argocd_connection_id -> custom_argocd_connection.

Revision ID: 027_llmd_argocd_connection
Revises: 026_argocd_connection
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "027_llmd_argocd_connection"
down_revision = "026_argocd_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_llmd_stack",
        sa.Column("argocd_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_custom_llmd_stack_argocd_connection_id", "custom_llmd_stack", ["argocd_connection_id"]
    )
    op.create_foreign_key(
        "fk_custom_llmd_stack_argocd_connection_id", "custom_llmd_stack",
        "custom_argocd_connection", ["argocd_connection_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_custom_llmd_stack_argocd_connection_id", "custom_llmd_stack", type_="foreignkey")
    op.drop_index("ix_custom_llmd_stack_argocd_connection_id", table_name="custom_llmd_stack")
    op.drop_column("custom_llmd_stack", "argocd_connection_id")
