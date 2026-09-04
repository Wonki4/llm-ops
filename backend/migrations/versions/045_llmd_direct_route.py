"""llm-d direct-route set: opt-in toggle + ingress host override.

Two nullable-safe columns on custom_llmd_stack:
- direct_route_enabled (bool, default false): when on, the portal deploys a
  plain ClusterIP Service (selecting router.modelServers.matchLabels pods)
  + an Ingress, alongside the EPP ingress.
- direct_ingress_host (nullable): full host override; NULL ->
  "{argo_app_name}-direct.{effective_ingress_domain}".

Revision ID: 045_llmd_direct_route
Revises: 044_serving_recipe
"""

import sqlalchemy as sa
from alembic import op

revision = "045_llmd_direct_route"
down_revision = "044_serving_recipe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_llmd_stack",
        sa.Column("direct_route_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("direct_ingress_host", sa.String(253), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_llmd_stack", "direct_ingress_host")
    op.drop_column("custom_llmd_stack", "direct_route_enabled")
