"""Per-stack llm-d chart source + EPP image override.

Nullable overrides on custom_llmd_stack; NULL falls back to the global
settings default. Lets an air-gapped install point the chart repo and EPP
image at an internal mirror per stack.

Revision ID: 039_llmd_stack_chart_source
Revises: 038_member_budget_boost
"""

import sqlalchemy as sa
from alembic import op

revision = "039_llmd_stack_chart_source"
down_revision = "038_member_budget_boost"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("chart_repo", 512),
    ("chart_name", 256),
    ("chart_version", 128),
    ("epp_registry", 256),
    ("epp_repository", 256),
    ("epp_tag", 128),
)


def upgrade() -> None:
    for name, length in _COLUMNS:
        op.add_column("custom_llmd_stack", sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("custom_llmd_stack", name)
