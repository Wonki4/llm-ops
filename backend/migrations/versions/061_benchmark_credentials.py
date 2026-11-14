"""Remove credentials from existing benchmark serving snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.services.benchmark_credentials import public_snapshot
from app.services.crypto import encrypt
from app.services.model_deployment_manifests import serving_api_key
from app.services.serving_engines import render_engine_args

revision = "061_benchmark_credentials"
down_revision = "060_recipe_hf_repo_id"
branch_labels = None
depends_on = None


def upgrade():
    runs = sa.table(
        "custom_benchmark_run",
        sa.column("id", UUID()),
        sa.column("status", sa.String()),
        sa.column("serving_snapshot", JSONB()),
    )
    conn = op.get_bind()
    query = (
        sa.select(runs.c.id, runs.c.status, runs.c.serving_snapshot)
        .where(runs.c.serving_snapshot.is_not(None))
        .order_by(runs.c.id)
    )
    page = query
    while rows := conn.execute(page.limit(200).with_for_update()).all():
        for run_id, status, snapshot in rows:
            if not snapshot:
                continue
            safe = public_snapshot(snapshot)
            if status == "provisioning":
                if snapshot.get("runner_secret"):
                    safe["runner_secret"] = True
                elif snapshot.get("legacy_runner_key_encrypted"):
                    safe["legacy_runner_key_encrypted"] = snapshot["legacy_runner_key_encrypted"]
                else:
                    # Old runs have no Secret yet. Keep only the credential the
                    # worker needs, encrypted with the portal's normal at-rest key.
                    flags = render_engine_args(snapshot.get("engine_args")) + (snapshot.get("vllm_extra_args") or [])
                    key = snapshot.get("api_key_override") or serving_api_key(flags, snapshot.get("env"))
                    safe["legacy_runner_key_encrypted"] = encrypt(key)
            if safe != snapshot:
                conn.execute(runs.update().where(runs.c.id == run_id).values(serving_snapshot=safe))
        page = query.where(runs.c.id > rows[-1].id)


def downgrade():
    # Deleted credentials cannot be reconstructed.
    pass
