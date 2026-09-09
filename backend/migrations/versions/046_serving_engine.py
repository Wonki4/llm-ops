"""Serving engine (vLLM / SGLang) + structured engine args.

Adds to custom_serving_recipe and custom_model_deployment:
- engine (varchar 16, not null, default 'vllm'): which OpenAI-compatible
  server the image runs. Existing rows become vLLM, matching old behaviour.
- engine_args (jsonb, nullable): {"flag-name": value} rendered generically
  into CLI flags ahead of vllm_extra_args.

Revision ID: 046_serving_engine
Revises: 045_llmd_direct_route
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "046_serving_engine"
down_revision = "045_llmd_direct_route"
branch_labels = None
depends_on = None

_TABLES = ("custom_serving_recipe", "custom_model_deployment")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("engine", sa.String(16), nullable=False, server_default="vllm"))
        op.add_column(table, sa.Column("engine_args", JSONB, nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "engine_args")
        op.drop_column(table, "engine")
