"""Status classification during a rollout: Updating until every pod runs the new template."""

import types
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient
from app.services.deployment_status import classify


def _obs(**kw):
    base = {"ready": 1, "available": 1, "conditions": []}
    base.update(kw)
    return base


def test_classify_updating_while_the_controller_has_not_seen_the_new_spec():
    obs = _obs(updated=1, total=1, generation=3, observed_generation=2)
    assert classify(obs, 1) == ("Updating", "Waiting for the rollout to start")


def test_classify_updating_while_pods_still_run_the_old_template():
    # replace rollout: one old pod gone, the new one not yet counted
    assert classify(_obs(ready=1, available=1, updated=1, total=2), 2) == ("Updating", "1/2 pods updated")


def test_classify_updating_while_an_old_pod_still_runs():
    # surge rollout stalled on GPU: the new pod exists (Pending), the old one keeps serving
    obs = _obs(ready=1, available=1, updated=1, total=2, generation=3, observed_generation=3)
    assert classify(obs, 1) == ("Updating", "1 old pod still running")


def test_classify_ready_once_the_rollout_is_complete():
    obs = _obs(ready=2, available=2, updated=2, total=2, generation=3, observed_generation=3)
    assert classify(obs, 2) == ("Ready", None)


def test_classify_ready_without_rollout_fields():
    # the external-serving scan reports only ready/available
    assert classify(_obs(ready=2, available=2), 2) == ("Ready", None)


def test_classify_progress_deadline_still_wins():
    obs = _obs(
        updated=1, total=2,
        conditions=[{"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded", "message": "x"}],
    )
    assert classify(obs, 1)[0] == "Failed"


async def test_read_deployment_status_reports_rollout_progress():
    live = types.SimpleNamespace(
        metadata=types.SimpleNamespace(generation=5),
        spec=types.SimpleNamespace(replicas=2),
        status=types.SimpleNamespace(
            ready_replicas=1, available_replicas=1, updated_replicas=1, replicas=3,
            observed_generation=4, conditions=None,
        ),
    )
    apps = MagicMock()
    apps.read_namespaced_deployment_status = AsyncMock(return_value=live)
    api_client = MagicMock()
    api_client.close = AsyncMock()
    with (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=api_client)),
        patch("app.clients.k8s.client.AppsV1Api", return_value=apps),
    ):
        obs = await K8sClient().read_deployment_status("ns", "m-deployment")
    assert obs["ready"] == 1 and obs["desired"] == 2 and obs["available"] == 1
    assert obs["updated"] == 1 and obs["total"] == 3
    assert obs["generation"] == 5 and obs["observed_generation"] == 4
