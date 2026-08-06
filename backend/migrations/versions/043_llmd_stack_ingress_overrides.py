"""Per-stack llm-d ingress host + class override.

Nullable overrides on custom_llmd_stack; NULL falls back to the global
settings default (ingress_host -> "{argo_app_name}.{effective_ingress_domain}",
ingress_class -> settings.llmd_ingress_class). Lets an admin set the ingress
host and class per stack from the UI.

Revision ID: 043_llmd_stack_ingress_overrides
Revises: 042_benchmark_labels_drop_sweeps
"""

import sqlalchemy as sa
from alembic import op

revision = "043_llmd_stack_ingress_overrides"
down_revision = "042_benchmark_labels_drop_sweeps"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("ingress_host", 253),
    ("ingress_class", 253),
)


def upgrade() -> None:
    for name, length in _COLUMNS:
        op.add_column("custom_llmd_stack", sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("custom_llmd_stack", name)
