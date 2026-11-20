"""Bounded MCP planning protocol. The backend judges measurements, never LLM opinions."""

from datetime import UTC, datetime

from fastapi import HTTPException

from app.services.validation_protocol import digest, rank


def fingerprint(recipe):
    # Names and descriptions are presentation; renaming an identical trial must not burn GPUs.
    return digest({k: v for k, v in recipe.items() if k not in {"name", "description"}})


def progress(job, candidates, *, current=None):
    policy = job.conditions.get("search")
    if not policy:
        return None
    current = current or datetime.now(UTC)
    closed_at = (job.search_state or {}).get("closed_at")
    if closed_at:
        current = min(current, datetime.fromisoformat(closed_at))
    elif getattr(job, "finished_at", None):
        end = job.finished_at
        current = min(current, end.replace(tzinfo=end.tzinfo or UTC))
    start = job.started_at or job.created_at or current
    seconds = max(0, (current - start.replace(tzinfo=start.tzinfo or UTC)).total_seconds())
    remaining = max(0, int(policy["max_search_seconds"] - seconds))
    metric = job.conditions["selection_metric"]
    best = rank(candidates, metric)
    # Compare improvements to the last meaningful best, so several small gains can accumulate.
    reference, stale = None, 0
    for candidate in sorted(candidates, key=lambda c: c.position):
        if candidate.status in {"queued", "validating"}:
            continue
        eligible = rank([candidate], metric)
        value = candidate.assessment["metrics"][metric] if eligible else None
        if value is not None and reference is None:
            reference, stale = value, 0
        elif reference is not None:
            delta = (reference - value if metric == "p99_ttft_ms" else value - reference) if value is not None else 0
            if delta > 0 and delta >= abs(reference) * policy["min_improvement_percent"] / 100:
                reference, stale = value, 0
            else:
                stale += 1
    closed = (job.search_state or {}).get("closed_reason")
    stop = closed or (
        "search_time_limit"
        if remaining == 0
        else "candidate_limit"
        if len(candidates) >= policy["max_candidates"]
        else "no_improvement"
        if stale >= policy["patience"]
        else None
    )
    return {
        "candidate_count": len(candidates),
        "remaining_candidates": max(0, policy["max_candidates"] - len(candidates)),
        "remaining_search_seconds": remaining,
        "stale_candidates": stale,
        "best_candidate_id": str(best[0].id) if best else None,
        "best_value": best[0].assessment["metrics"][metric] if best else None,
        "stop_reason": stop,
        "closed": bool(closed),
        "next_action": "finished"
        if job.status in {"succeeded", "failed", "cancelled"}
        else "wait"
        if job.status != "awaiting_input"
        else "finish"
        if stop
        else "propose_candidate",
    }


def check_submission(job, candidates, expected_count):
    if not job.conditions.get("search"):
        return
    if expected_count != len(candidates):
        raise HTTPException(409, "Candidate history changed; read search context and replan before submitting")
    status = progress(job, candidates)
    if status["stop_reason"]:
        raise HTTPException(409, f"Recipe search has stopped: {status['stop_reason']}")


def close(job, reason, current):
    if not (job.search_state or {}).get("closed_reason"):
        job.search_state = {**(job.search_state or {}), "closed_reason": reason, "closed_at": current.isoformat()}
