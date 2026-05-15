"""Add model deployment table for K8s-managed LLM deployments.

Revision ID: 014_model_deployment
Revises: 013_cost_schedule
"""

import sqlalchemy as sa
from alembic import op

revision = "014_model_deployment"
down_revision = "013_cost_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_model_deployment",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        # Logical name (also the LiteLLM model_name that will be registered on Ready)
        sa.Column("model_name", sa.String(256), nullable=False, unique=True, index=True),
        # K8s placement
        sa.Column("namespace", sa.String(128), nullable=False, server_default="default"),
        # Container image (default vllm)
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("replicas", sa.Integer(), nullable=False, server_default="1"),
        # Resource requests / limits (per pod). All optional except gpu_count.
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_resource_key", sa.String(128), nullable=False, server_default="nvidia.com/gpu"),
        sa.Column("cpu_request", sa.String(32), nullable=True),
        sa.Column("cpu_limit", sa.String(32), nullable=True),
        sa.Column("memory_request", sa.String(32), nullable=True),
        sa.Column("memory_limit", sa.String(32), nullable=True),
        # Scheduling (admin-provided as JSON arrays/maps)
        sa.Column("node_selector", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("tolerations", sa.dialects.postgresql.JSONB(), nullable=True),
        # NAS PVC mount
        sa.Column("pvc_name", sa.String(256), nullable=True),
        sa.Column("pvc_mount_path", sa.String(512), nullable=True),
        # vLLM args
        sa.Column("model_path", sa.String(512), nullable=False),  # --model value, usually subpath under PVC
        sa.Column("vllm_extra_args", sa.dialects.postgresql.JSONB(), nullable=True),  # list[str]
        sa.Column("env", sa.dialects.postgresql.JSONB(), nullable=True),  # dict[str,str]
        # Ingress (nginx)
        sa.Column("ingress_host", sa.String(256), nullable=False),
        sa.Column("ingress_path", sa.String(256), nullable=False, server_default="/"),
        sa.Column("ingress_class", sa.String(64), nullable=False, server_default="nginx"),
        # Last observed status snapshot synced by the worker
        sa.Column("status", sa.String(32), nullable=False, server_default="Pending"),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("ready_replicas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("service_cluster_ip", sa.String(64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        # Auto-registered LiteLLM model id once the deployment goes Ready
        sa.Column("litellm_model_id", sa.String(128), nullable=True),
        # Audit
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("replicas >= 0", name="ck_deployment_replicas_nonneg"),
        sa.CheckConstraint("gpu_count >= 0", name="ck_deployment_gpu_nonneg"),
    )


def downgrade() -> None:
    op.drop_table("custom_model_deployment")
