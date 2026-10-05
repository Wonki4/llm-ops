"""Prefill/decode disaggregated serving + generic runtime options.

custom_serving_recipe / custom_model_deployment:
- serving_mode  varchar(16) not null default 'aggregated' ('aggregated' | 'pd')
- pd_config     jsonb null  — per-role overrides, NIXL port, kv-transfer extras,
                              sidecar image, router tuning (see pd_serving)
- runtime       jsonb null  — {"shm_size_gi", "host_ipc", "privileged", "extra_resources"}

custom_model_deployment only:
- pd_status             jsonb null — per-role observed status
- router_stack_id       uuid null FK custom_llmd_stack ON DELETE SET NULL
- router_stack_created  bool not null default false — the portal created the
                        router for this deployment (and deletes it with it)

Revision ID: 050_pd_serving
Revises: 049_gpu_profiles
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "050_pd_serving"
down_revision = "049_gpu_profiles"
branch_labels = None
depends_on = None

_TABLES = ("custom_serving_recipe", "custom_model_deployment")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("serving_mode", sa.String(16), nullable=False, server_default="aggregated"))
        op.add_column(table, sa.Column("pd_config", JSONB, nullable=True))
        op.add_column(table, sa.Column("runtime", JSONB, nullable=True))
    op.add_column("custom_model_deployment", sa.Column("pd_status", JSONB, nullable=True))
    op.add_column(
        "custom_model_deployment",
        sa.Column(
            "router_stack_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_llmd_stack.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "custom_model_deployment",
        sa.Column("router_stack_created", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("custom_model_deployment", "router_stack_created")
    op.drop_column("custom_model_deployment", "router_stack_id")
    op.drop_column("custom_model_deployment", "pd_status")
    for table in _TABLES:
        op.drop_column(table, "runtime")
        op.drop_column(table, "pd_config")
        op.drop_column(table, "serving_mode")
