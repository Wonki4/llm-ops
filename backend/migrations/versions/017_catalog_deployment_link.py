"""Move LiteLLM-side ownership from deployment to catalog (1:N).

Revision ID: 017_catalog_deployment_link
Revises: 016_k8s_cluster

A single K8s deployment can serve N LiteLLM-registered models (e.g. multi-
LoRA or multiple logical aliases pointing at the same endpoint). The 1:1
assumption in PR-A/B kept the LiteLLM model id on the deployment row, which
breaks that case.

This migration:
1. Adds `deployment_id` (FK) and `litellm_model_id` columns to
   custom_model_catalog. `deployment_id` nullable so catalog-only / external
   entries keep working.
2. Backfills the link: for every existing deployment, locate or create the
   matching catalog row (by model_name) and copy the LiteLLM id over.
3. Drops the now-redundant `litellm_model_id` from custom_model_deployment.
"""

import sqlalchemy as sa
from alembic import op

revision = "017_catalog_deployment_link"
down_revision = "016_k8s_cluster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_catalog",
        sa.Column("deployment_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "custom_model_catalog",
        sa.Column("litellm_model_id", sa.String(128), nullable=True),
    )
    op.create_foreign_key(
        "fk_catalog_deployment",
        "custom_model_catalog",
        "custom_model_deployment",
        ["deployment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_catalog_deployment_id",
        "custom_model_catalog",
        ["deployment_id"],
    )

    bind = op.get_bind()

    # For each existing deployment, locate-or-create a catalog row matching
    # its model_name and attach. The "system" user owns auto-created rows.
    deployments = bind.execute(
        sa.text("SELECT id, model_name, litellm_model_id FROM custom_model_deployment")
    ).mappings().all()
    for d in deployments:
        existing = bind.execute(
            sa.text("SELECT id FROM custom_model_catalog WHERE model_name = :n"),
            {"n": d["model_name"]},
        ).first()
        if existing is None:
            bind.execute(
                sa.text(
                    "INSERT INTO custom_model_catalog "
                    "(id, model_name, display_name, status, visible, "
                    " deployment_id, litellm_model_id, created_by, updated_by) "
                    "VALUES (gen_random_uuid(), :n, :n, 'testing', true, "
                    " :did, :lid, 'system', 'system')"
                ),
                {"n": d["model_name"], "did": d["id"], "lid": d["litellm_model_id"]},
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE custom_model_catalog SET deployment_id = :did, "
                    "litellm_model_id = COALESCE(:lid, litellm_model_id) "
                    "WHERE id = :cid"
                ),
                {"did": d["id"], "lid": d["litellm_model_id"], "cid": existing[0]},
            )

    op.drop_column("custom_model_deployment", "litellm_model_id")


def downgrade() -> None:
    op.add_column(
        "custom_model_deployment",
        sa.Column("litellm_model_id", sa.String(128), nullable=True),
    )
    # Best-effort restore: pick any catalog row attached to the deployment.
    bind = op.get_bind()
    deployments = bind.execute(sa.text("SELECT id FROM custom_model_deployment")).all()
    for (did,) in deployments:
        bind.execute(
            sa.text(
                "UPDATE custom_model_deployment SET litellm_model_id = ("
                "  SELECT litellm_model_id FROM custom_model_catalog "
                "  WHERE deployment_id = :did AND litellm_model_id IS NOT NULL "
                "  LIMIT 1"
                ") WHERE id = :did"
            ),
            {"did": did},
        )

    op.drop_index("ix_catalog_deployment_id", table_name="custom_model_catalog")
    op.drop_constraint("fk_catalog_deployment", "custom_model_catalog", type_="foreignkey")
    op.drop_column("custom_model_catalog", "litellm_model_id")
    op.drop_column("custom_model_catalog", "deployment_id")
