"""Tests for external vLLM/SGLang serving discovery."""

from app.services.deployment_status import classify


# ─── classify ────────────────────────────────────────────────


def test_classify_ready():
    observed = {"ready": 2, "available": 2, "conditions": []}
    assert classify(observed, 2) == ("Ready", None)


def test_classify_pending_when_no_ready_pods():
    observed = {"ready": 0, "available": 0, "conditions": []}
    status, message = classify(observed, 1)
    assert status == "Pending"


def test_classify_stopped_when_zero_desired():
    observed = {"ready": 0, "available": 0, "conditions": []}
    status, _ = classify(observed, 0)
    assert status == "Stopped"


def test_classify_failed_on_progress_deadline():
    observed = {
        "ready": 0,
        "available": 0,
        "conditions": [
            {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded", "message": "x"}
        ],
    }
    status, _ = classify(observed, 1)
    assert status == "Failed"
