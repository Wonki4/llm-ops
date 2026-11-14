"""Portal accuracy runner. lm-eval supplies versioned tasks and standard scoring."""

from __future__ import annotations

import json
import os
import sys


def run_accuracy(model, base, params, tokenizer):
    from lm_eval import simple_evaluate
    from accuracy_model import PortalModel

    tasks = params.get("tasks") or ["mmlu"]
    if isinstance(tasks, str):
        tasks = [s.strip() for s in tasks.split(",") if s.strip()]
    if not tasks or not all(isinstance(t, str) and t for t in tasks):
        raise ValueError("Expected a list of standard task names")

    chat = params.get("apply_chat_template", False)
    base = base.rstrip("/")
    api = base if base.endswith("/v1") else f"{base}/v1"
    lm = PortalModel(
        model,
        api,
        tokenizer or model,
        chat,
        params.get("num_concurrent", 4),
        params.get("batch_size", 1),
    )
    options = {
        key: params[key]
        for key in ("limit", "num_fewshot", "gen_kwargs")
        if key in params
    }
    outcome = simple_evaluate(
        model=lm,
        tasks=tasks,
        batch_size=params.get("batch_size", 1),
        apply_chat_template=chat,
        log_samples=False,
        **options,
    )
    if not outcome or not outcome.get("results"):
        raise ValueError("Evaluation returned no task scores")
    return {
        k: outcome[k]
        for k in (
            "results",
            "groups",
            "versions",
            "n-samples",
            "n-shot",
            "higher_is_better",
        )
        if k in outcome
    }


def main():
    result = {
        "runner": "portal",
        "runner_version": 1,
        "experiment": "accuracy",
        "scorer": "lm-eval/0.4.4",
    }
    try:
        if os.environ["BENCH_KIND"] != "accuracy":
            raise ValueError("Expected an accuracy Job")
        os.environ["OPENAI_API_KEY"] = os.environ.get("BENCH_API_KEY", "")
        params = json.loads(os.environ.get("BENCH_PARAMS_JSON") or "{}")
        result["metrics"] = run_accuracy(
            os.environ["BENCH_MODEL"],
            os.environ["BENCH_TARGET_URL"],
            params,
            os.environ.get("BENCH_TOKENIZER"),
        )
    except Exception as exc:
        result["error"] = str(exc)[:500] or type(exc).__name__
    print("<<<RESULT>>>" + json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
