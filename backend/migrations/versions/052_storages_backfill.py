"""Backfill storages from pvc_* / pool pvc_* / cluster default NFS; drop default_nfs_*.

- one storage per distinct claim name ("pvc-<claim>", kind weights)
- one pvc binding per (claim, deployment cluster, deployment namespace)
- recipes:     storages = [{storage_id, mount_path, read_only: false}]
- deployments: same + volume_name "model-weights" + resolved snapshot
  (renders byte-identical to the old pvc_* volume — no rollout)
- P/D pools:   pd_config.<role>.pvc_name/pvc_mount_path → pd_config.<role>.storages,
  each key falling back to the row's base pvc_* exactly like the old pool_row
  (a pool whose pair is still incomplete gets storages [] — it mounted nothing)
- clusters with default_nfs_*: storage "<cluster>-bench-weights" + cluster-wide
  nfs binding → bench_storage_id / bench_mount_path; default_nfs_* dropped
pvc_name / pvc_mount_path columns are left in place (unread).

Downgrade restores default_nfs_* and pool pvc_* keys, resets storages / bench_*,
and deletes the storages this migration created (created_by = 'migration'; their
bindings cascade) so a later upgrade starts clean. Storages created by admins
after the upgrade stay until 051's downgrade drops the tables.

Revision ID: 052_storages_backfill
Revises: 051_storages
"""

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "052_storages_backfill"
down_revision = "051_storages"
branch_labels = None
depends_on = None

_ROLES = ("prefill", "decode")  # migrations don't import app code (pd_serving.ROLES)
_VOLUME = "model-weights"
_BY = "migration"
_CLAIM_PREFIX = "Migrated from PVC "


def _slug(raw: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")[:40].strip("-")
    return s or "storage"


def _bench_name(cluster_name: str) -> str:
    """'<cluster>-bench-weights' within the 50-char storage name limit."""
    return f"{_slug(cluster_name)[:36].strip('-')}-bench-weights"


def _unique(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name = f"{base[:46]}-{n}"
        n += 1
    taken.add(name)
    return name


def _pool_pvcs(cfg: dict | None, base_claim: str | None, base_mount: str | None) -> dict:
    """role → (claim, mount) for pools that set either pvc key, falling back per key to
    the base (old pool_row semantics); None when the pair is still incomplete."""
    out: dict[str, tuple[str, str] | None] = {}
    for role in _ROLES:
        pool = (cfg or {}).get(role) or {}
        if not (pool.get("pvc_name") or pool.get("pvc_mount_path")):
            continue
        claim = pool.get("pvc_name") or base_claim
        mount = pool.get("pvc_mount_path") or base_mount
        out[role] = (claim, mount) if claim and mount else None
    return out


def _strip_pool_pvcs(cfg: dict | None) -> dict | None:
    if cfg is None:
        return None
    out = dict(cfg)
    for role in _ROLES:
        if isinstance(out.get(role), dict):
            out[role] = {k: v for k, v in out[role].items() if k not in ("pvc_name", "pvc_mount_path")}
    return out


def _description(claim: str) -> str:
    return f"{_CLAIM_PREFIX}{claim}"


def _migrated_claim(description: str | None) -> str | None:
    """The claim a migrated storage came from (recipe-only claims have no binding)."""
    if description and description.startswith(_CLAIM_PREFIX):
        return description[len(_CLAIM_PREFIX) :]
    return None


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def _insert_storage(bind, name: str, description: str) -> str:
    sid = str(uuid.uuid4())
    bind.execute(
        sa.text(
            "INSERT INTO custom_storage (id, name, description, kind, created_by, updated_by) "
            "VALUES (:id, :name, :desc, 'weights', :by, :by)"
        ),
        {"id": sid, "name": name, "desc": description, "by": _BY},
    )
    return sid


def upgrade() -> None:
    bind = op.get_bind()
    recipes = (
        bind.execute(sa.text("SELECT id, pvc_name, pvc_mount_path, pd_config FROM custom_serving_recipe"))
        .mappings()
        .all()
    )
    deps = (
        bind.execute(
            sa.text(
                "SELECT id, cluster_id, namespace, pvc_name, pvc_mount_path, pd_config FROM custom_model_deployment"
            )
        )
        .mappings()
        .all()
    )

    def pvcs_of(row) -> list[tuple[str, str, str | None]]:
        """[(claim, mount, role|None)] for the base pair and every renderable pool pair."""
        out = []
        if row["pvc_name"] and row["pvc_mount_path"]:
            out.append((row["pvc_name"], row["pvc_mount_path"], None))
        pools = _pool_pvcs(_load(row["pd_config"]), row["pvc_name"], row["pvc_mount_path"])
        for role, pair in pools.items():
            if pair is not None:
                out.append((pair[0], pair[1], role))
        return out

    def empty_pools(row) -> list[str]:
        """Pools that set a pvc key but still had no complete pair (rendered no volume)."""
        pools = _pool_pvcs(_load(row["pd_config"]), row["pvc_name"], row["pvc_mount_path"])
        return [role for role, pair in pools.items() if pair is None]

    taken = {r[0] for r in bind.execute(sa.text("SELECT name FROM custom_storage"))}
    storage_of: dict[str, str] = {}
    for row in [*recipes, *deps]:
        for claim, _, _ in pvcs_of(row):
            if claim not in storage_of:
                name = _unique(f"pvc-{_slug(claim)}", taken)
                storage_of[claim] = _insert_storage(bind, name, _description(claim))

    def binding_id(sid: str, cluster_id, namespace: str, claim: str) -> str:
        found = bind.execute(
            sa.text(
                "SELECT id FROM custom_storage_binding WHERE storage_id = :sid "
                "AND cluster_id IS NOT DISTINCT FROM :cid AND namespace = :ns"
            ),
            {"sid": sid, "cid": cluster_id, "ns": namespace},
        ).scalar()
        if found:
            return str(found)
        bid = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO custom_storage_binding (id, storage_id, cluster_id, namespace, type, claim_name, "
                "created_by, updated_by) VALUES (:id, :sid, :cid, :ns, 'pvc', :claim, :by, :by)"
            ),
            {"id": bid, "sid": sid, "cid": cluster_id, "ns": namespace, "claim": claim, "by": _BY},
        )
        return bid

    def write(table: str, row_id, storages: list, cfg: dict | None) -> None:
        bind.execute(
            sa.text(f"UPDATE {table} SET storages = CAST(:s AS jsonb), pd_config = CAST(:c AS jsonb) WHERE id = :id"),
            {"s": json.dumps(storages), "c": json.dumps(cfg) if cfg is not None else None, "id": row_id},
        )

    for r in recipes:
        storages = []
        new_cfg = _strip_pool_pvcs(_load(r["pd_config"]))
        for role in empty_pools(r):
            new_cfg[role]["storages"] = []
        for claim, mount, role in pvcs_of(r):
            entry = {"storage_id": storage_of[claim], "mount_path": mount, "read_only": False}
            if role is None:
                storages.append(entry)
            else:
                new_cfg[role]["storages"] = [entry]
        write("custom_serving_recipe", r["id"], storages, new_cfg)

    for d in deps:
        storages = []
        new_cfg = _strip_pool_pvcs(_load(d["pd_config"]))
        for role in empty_pools(d):
            new_cfg[role]["storages"] = []
        for claim, mount, role in pvcs_of(d):
            sid = storage_of[claim]
            bid = binding_id(sid, d["cluster_id"], d["namespace"], claim)
            resolved = {
                "binding_id": bid,
                "type": "pvc",
                "claim_name": claim,
                "namespace": d["namespace"],
                "nfs_server": None,
                "nfs_path": None,
                "sub_path": None,
            }
            entry = {
                "storage_id": sid,
                "mount_path": mount,
                "read_only": False,
                "volume_name": _VOLUME,
                "resolved": resolved,
            }
            if role is None:
                storages.append(entry)
            else:
                new_cfg[role]["storages"] = [entry]
        write("custom_model_deployment", d["id"], storages, new_cfg)

    clusters = (
        bind.execute(
            sa.text(
                "SELECT id, name, default_nfs_server, default_nfs_path, default_nfs_mount_path FROM custom_k8s_cluster "
                "WHERE default_nfs_server IS NOT NULL AND default_nfs_path IS NOT NULL "
                "AND default_nfs_mount_path IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    for c in clusters:
        sid = _insert_storage(
            bind, _unique(_bench_name(c["name"]), taken), f"Migrated from cluster {c['name']} default NFS"
        )
        bind.execute(
            sa.text(
                "INSERT INTO custom_storage_binding (id, storage_id, cluster_id, namespace, type, nfs_server, "
                "nfs_path, created_by, updated_by) VALUES (:id, :sid, :cid, NULL, 'nfs', :srv, :path, :by, :by)"
            ),
            {
                "id": str(uuid.uuid4()),
                "sid": sid,
                "cid": c["id"],
                "srv": c["default_nfs_server"],
                "path": c["default_nfs_path"],
                "by": _BY,
            },
        )
        bind.execute(
            sa.text("UPDATE custom_k8s_cluster SET bench_storage_id = :sid, bench_mount_path = :mp WHERE id = :id"),
            {"sid": sid, "mp": c["default_nfs_mount_path"], "id": c["id"]},
        )

    op.drop_column("custom_k8s_cluster", "default_nfs_mount_path")
    op.drop_column("custom_k8s_cluster", "default_nfs_path")
    op.drop_column("custom_k8s_cluster", "default_nfs_server")


def downgrade() -> None:
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_server", sa.String(253), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_path", sa.String(512), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_mount_path", sa.String(512), nullable=True))
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE custom_k8s_cluster c SET default_nfs_server = b.nfs_server, default_nfs_path = b.nfs_path, "
            "default_nfs_mount_path = c.bench_mount_path FROM custom_storage_binding b "
            "WHERE b.storage_id = c.bench_storage_id AND b.cluster_id = c.id AND b.namespace IS NULL "
            "AND b.type = 'nfs'"
        )
    )
    claim_of = {
        str(r["id"]): _migrated_claim(r["description"])
        for r in bind.execute(
            sa.text("SELECT id, description FROM custom_storage WHERE created_by = :by"), {"by": _BY}
        ).mappings()
    }
    claim_of |= {
        str(r["storage_id"]): r["claim_name"]
        for r in bind.execute(
            sa.text(
                "SELECT DISTINCT ON (storage_id) storage_id, claim_name FROM custom_storage_binding WHERE type = 'pvc'"
            )
        ).mappings()
    }
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        for row in bind.execute(sa.text(f"SELECT id, pd_config FROM {table} WHERE pd_config IS NOT NULL")).mappings():
            cfg = _load(row["pd_config"])
            changed = False
            for role in _ROLES:
                pool = cfg.get(role)
                entries = (pool or {}).pop("storages", None) if isinstance(pool, dict) else None
                if entries:
                    first = entries[0]
                    claim = (first.get("resolved") or {}).get("claim_name") or claim_of.get(first["storage_id"])
                    if claim:
                        pool["pvc_name"], pool["pvc_mount_path"] = claim, first["mount_path"]
                changed = changed or entries is not None
            if changed:
                bind.execute(
                    sa.text(f"UPDATE {table} SET pd_config = CAST(:c AS jsonb) WHERE id = :id"),
                    {"c": json.dumps(cfg), "id": row["id"]},
                )
    # Undo the rest so `downgrade -1` + `upgrade` is repeatable (no "pvc-…-2" names on re-upgrade).
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        bind.execute(sa.text(f"UPDATE {table} SET storages = '[]'::jsonb"))
    bind.execute(sa.text("UPDATE custom_k8s_cluster SET bench_storage_id = NULL, bench_mount_path = NULL"))
    bind.execute(sa.text("DELETE FROM custom_storage WHERE created_by = :by"), {"by": _BY})
