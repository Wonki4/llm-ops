"""storage_fields.pvc_access_warnings: PVC access modes against how a deployment mounts them."""

from types import SimpleNamespace

from app.services.storage_fields import pvc_access_warnings


def _entry(claim="models-pvc", read_only=True):
    return {"storage_id": "s", "mount_path": "/models", "read_only": read_only,
            "resolved": {"type": "pvc", "claim_name": claim}}


def _agg(replicas=1, storages=None):
    return SimpleNamespace(serving_mode="aggregated", pd_config=None, replicas=replicas,
                           storages=[_entry()] if storages is None else storages)


def _pd(prefill=1, decode=1, prefill_storages=None, decode_storages=None, base=None):
    pool = lambda n, st: {"replicas": n, **({"storages": st} if st is not None else {})}  # noqa: E731
    return SimpleNamespace(
        serving_mode="pd", replicas=1, model_name="m", namespace="ns", cluster_id=None, engine="vllm",
        storages=[_entry()] if base is None else base,
        pd_config={"prefill": pool(prefill, prefill_storages), "decode": pool(decode, decode_storages)},
    )


def _pvcs(*modes, claim="models-pvc"):
    return {claim: {"name": claim, "phase": "Bound", "access_modes": list(modes)}}


def test_single_replica_rwo_is_fine():
    assert pvc_access_warnings(_agg(1), _pvcs("ReadWriteOnce")) == []


def test_rwo_with_two_replicas_warns_single_node():
    assert pvc_access_warnings(_agg(2), _pvcs("ReadWriteOnce")) == ["pvc_single_node:models-pvc"]


def test_pd_pools_sharing_an_rwo_claim_warn():
    assert pvc_access_warnings(_pd(), _pvcs("ReadWriteOnce")) == ["pvc_single_node:models-pvc"]


def test_rwop_warns_single_pod():
    assert pvc_access_warnings(_pd(), _pvcs("ReadWriteOncePod")) == ["pvc_single_pod:models-pvc"]


def test_rwx_and_rox_read_only_are_fine():
    assert pvc_access_warnings(_pd(2, 2), _pvcs("ReadWriteMany")) == []
    assert pvc_access_warnings(_pd(2, 2), _pvcs("ReadOnlyMany")) == []
    assert pvc_access_warnings(_pd(2, 2), _pvcs("ReadWriteOnce", "ReadOnlyMany")) == []


def test_rox_mounted_read_write_warns():
    dep = _agg(1, storages=[_entry(read_only=False)])
    assert pvc_access_warnings(dep, _pvcs("ReadOnlyMany")) == ["pvc_read_only:models-pvc"]


def test_rwo_plus_rox_mounted_read_write_by_two_pods_warns_single_node():
    dep = _agg(2, storages=[_entry(read_only=False)])
    assert pvc_access_warnings(dep, _pvcs("ReadWriteOnce", "ReadOnlyMany")) == ["pvc_single_node:models-pvc"]


def test_pool_own_lists_count_separately():
    # Each pool mounts its own RWO claim with one replica: no sharing.
    dep = _pd(prefill_storages=[_entry("a")], decode_storages=[_entry("b")])
    pvcs = {**_pvcs("ReadWriteOnce", claim="a"), **_pvcs("ReadWriteOnce", claim="b")}
    assert pvc_access_warnings(dep, pvcs) == []


def test_stopped_pool_does_not_count():
    assert pvc_access_warnings(_pd(prefill=1, decode=0), _pvcs("ReadWriteOnce")) == []


def test_unknown_modes_or_missing_pvc_are_skipped():
    assert pvc_access_warnings(_agg(2), _pvcs()) == []
    assert pvc_access_warnings(_agg(2), {}) == []
