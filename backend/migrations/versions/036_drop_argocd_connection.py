"""Drop the ArgoCD connection registry (llm-d now provisions via the K8s API).

Removes custom_llmd_stack.argocd_connection_id and the custom_argocd_connection
table. Existing stacks keep their cluster_id (null = portal default kubeconfig)
and argo_app_name, so a CRD apply adopts their already-existing Application CRs.

Revision ID: 036_drop_argocd_connection
Revises: 035_cluster_argocd_namespace
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "036_drop_argocd_connection"
down_revision = "035_cluster_argocd_namespace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("custom_llmd_stack", "argocd_connection_id")
    op.drop_table("custom_argocd_connection")


def downgrade() -> None:
    op.create_table(
        "custom_argocd_connection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("server_url", sa.String(512), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("insecure_skip_verify", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("argocd_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
