"""Immutable validation conditions and server-side, fail-closed selection rules."""

import hashlib
import json
import math
import statistics
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.benchmark_workload import AccuracyWorkload, PerformanceWorkload, default_objectives
from app.services.clusters import validate_namespace


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TimeLimits(StrictModel):
    assets: int = Field(3600, ge=60, le=86400)
    scheduling: int = Field(1800, ge=60, le=86400)
    startup: int = Field(7200, ge=60, le=86400)
    measurement: int = Field(3600, ge=60, le=86400)
    total: int = Field(86400, ge=300, le=604800)


class QualityGate(StrictModel):
    task: str = Field(min_length=1)
    metric: str = Field(default="acc,none", min_length=1)
    minimum: float = Field(ge=0)


class RecipeSearch(StrictModel):
    """Immutable scope for a recipe search driven by an external MCP client."""

    target_model: str = Field(min_length=1, max_length=512)
    alternative_models: list[str] = Field(default_factory=list, max_length=20)
    max_candidates: int = Field(12, ge=1, le=100)
    max_search_seconds: int = Field(21600, ge=60, le=604740)
    patience: int = Field(3, ge=1, le=100)
    min_improvement_percent: float = Field(1, ge=0, le=100)
    min_context_length: int = Field(4096, ge=1)

    @field_validator("target_model")
    @classmethod
    def clean_target(cls, value):
        if not value.strip():
            raise ValueError("A target model is required")
        return value.strip()


class CapacitySearch(StrictModel):
    max_rate: float = Field(128, gt=0, le=10000)
    refinements: int = Field(3, ge=0, le=8)
    max_lag_ms: float = Field(100, gt=0)


class ValidationConditions(StrictModel):
    version: Literal[1, 2] = 1
    ingress_host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.-]+$")
    ingress_class: str = "nginx"
    gpu_type: str | None = None
    gpu_limit: int = Field(8, ge=1, le=1024)
    time_limits: TimeLimits = Field(default_factory=TimeLimits)
    performance: PerformanceWorkload = Field(default_factory=PerformanceWorkload)
    capacity_search: CapacitySearch | None = None
    repeats: int = Field(3, ge=1, le=10)
    prefix_len: int | None = Field(None, gt=0)
    accuracy: AccuracyWorkload | None = None
    accuracy_image: str | None = None
    quality_gates: list[QualityGate] = Field(default_factory=list)
    max_ttft_p99_ms: float | None = Field(None, gt=0)
    min_output_throughput: float | None = Field(None, gt=0)
    selection_metric: Literal["output_throughput", "p99_ttft_ms", "request_throughput", "throughput_per_gpu"] = (
        "output_throughput"
    )
    # Use the same tokenizer/corpus for alternatives; throughput in tokens is otherwise incomparable.
    tokenizer: str | None = None
    airgap: bool = False
    search: RecipeSearch | None = None

    @model_validator(mode="before")
    @classmethod
    def new_workload(cls, value):
        if isinstance(value, dict) and value.get("version") == 2:
            value = {**value, "performance": {"workload_version": 2, "cache_ratio": 90, **value.get("performance", {})}}
            if value.get("accuracy") or value.get("quality_gates") or value.get("prefix_len"):
                raise ValueError("New validations use a single performance workload without accuracy evaluation")
            if value.get("selection_metric", "output_throughput") not in ("output_throughput", "throughput_per_gpu"):
                raise ValueError("Select output throughput or throughput per GPU")
            if value["performance"]["workload_version"] != 2:
                raise ValueError("New validations require workload version 2")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.capacity_search is not None:
            if self.version != 2 or not self.performance.slo:
                raise ValueError("Capacity search requires workload v2 with latency objectives")
            count = (
                self.performance.num_sessions
                if self.performance.workload_type == "agentic_replay"
                else self.performance.num_prompts
            )
            if count < 2:
                raise ValueError("Capacity search requires at least two arrivals per point")
            if (self.performance.request_rate or 1) > self.capacity_search.max_rate:
                raise ValueError("The initial arrival rate exceeds the search limit")
        if self.search:
            if self.search.max_search_seconds >= self.time_limits.total:
                raise ValueError("Search time must leave time for final restoration and validation")
            if self.version == 1 and self.search.alternative_models and not self.quality_gates:
                raise ValueError("Alternative models require predefined quality floors")
        if self.prefix_len is not None and self.prefix_len >= self.performance.random_input_len:
            raise ValueError("Shared prefix must be shorter than the total input")
        if self.quality_gates and not self.accuracy:
            raise ValueError("Quality gates require an accuracy evaluation")
        if self.accuracy and not self.quality_gates:
            raise ValueError("Set explicit task/metric quality floors for accuracy evaluation")
        if self.accuracy:
            tasks = self.accuracy.tasks
            tasks = [t.strip() for t in tasks.split(",")] if isinstance(tasks, str) else tasks
            if any(g.task not in tasks for g in self.quality_gates):
                raise ValueError("Every quality gate must name a configured evaluation task")
        return self


class CandidateInput(StrictModel):
    recipe_id: str | None = None
    recipe: dict | None = None

    @field_validator("recipe_id")
    @classmethod
    def valid_id(cls, value):
        return str(uuid.UUID(value)) if value else None

    @model_validator(mode="after")
    def source(self):
        if (self.recipe_id is not None) == (self.recipe is not None):
            raise ValueError("Provide a registered recipe or an unsaved recipe draft")
        return self


class CurrentValidationConditions(ValidationConditions):
    version: Literal[2] = 2


class ValidationCreate(StrictModel):
    request_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    model_name: str = Field(min_length=1, max_length=256)
    cluster_id: str | None = None
    namespace: str | None = None
    candidates: list[CandidateInput] = Field(default_factory=list, max_length=100)
    conditions: CurrentValidationConditions

    @field_validator("conditions", mode="before")
    @classmethod
    def current_protocol(cls, value):
        if isinstance(value, dict):
            if value.get("version", 2) != 2:
                raise ValueError("New validations require version 2; existing jobs retain their saved conditions")
            if "capacity_search" not in value:
                value = {
                    **value,
                    "capacity_search": {},
                    "performance": {"slo": default_objectives().model_dump(), **(value.get("performance") or {})},
                }
            return {**value, "version": 2}
        if value.version != 2:
            raise ValueError("New validations require version 2")
        return value

    @model_validator(mode="after")
    def candidate_count(self):
        if not self.candidates and not self.conditions.search:
            raise ValueError("Recipe comparison requires at least one candidate")
        if self.conditions.search and len(self.candidates) > self.conditions.search.max_candidates:
            raise ValueError("Initial candidates exceed the search limit")
        return self

    _namespace = field_validator("namespace")(validate_namespace)

    @field_validator("cluster_id")
    @classmethod
    def valid_cluster(cls, value):
        return str(uuid.UUID(value)) if value else None

    @field_validator("name", "model_name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class CandidateAdd(StrictModel):
    request_key: str = Field(min_length=1, max_length=128)
    source_candidate_id: str | None = None
    recipe_id: str | None = None
    recipe: dict | None = None
    patch: dict = Field(default_factory=dict)
    expected_candidate_count: int | None = Field(None, ge=0)
    hypothesis: str | None = Field(None, max_length=4000)

    @field_validator("source_candidate_id", "recipe_id")
    @classmethod
    def valid_id(cls, value):
        return str(uuid.UUID(value)) if value else None

    @field_validator("patch", "recipe")
    @classmethod
    def no_redacted_replacements(cls, value):
        def check(v):
            if v == "***":
                raise ValueError(
                    "Re-enter redacted arguments before replacing their list; unchanged credentials are preserved"
                )
            if isinstance(v, dict):
                for child in v.values():
                    check(child)
            if isinstance(v, list):
                for child in v:
                    check(child)

        check(value)
        return value

    @model_validator(mode="after")
    def source(self):
        if sum(v is not None for v in (self.source_candidate_id, self.recipe_id, self.recipe)) != 1:
            raise ValueError("Choose a source candidate, registered recipe, or unsaved recipe draft")
        return self


class SearchFinish(StrictModel):
    expected_candidate_count: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class AttemptAnalysis(StrictModel):
    explanation: str = Field(min_length=1, max_length=16000)
    proposed_patch: dict = Field(default_factory=dict)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def effective_flag(args, flag):
    """Honor the engine's last-option-wins rule, including underscore aliases."""
    value = None
    for i, arg in enumerate(args):
        name, separator, inline = arg.partition("=")
        if name.replace("_", "-") == flag:
            value = inline if separator else args[i + 1] if i + 1 < len(args) else ""
    return value


def without_flags(args, names):
    result, skip = [], False
    for arg in args or []:
        if skip:
            skip = False
            continue
        name, separator, _ = arg.partition("=")
        if name.replace("_", "-") in names:
            skip = not separator
        else:
            result.append(arg)
    return result


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def assess(runs, conditions: dict, gpu_count: int) -> dict:
    """A missing, partial, non-finite, or failed measurement can never win."""
    protocol = ValidationConditions.model_validate(conditions)
    perf = [r for r in runs if r.kind == "performance" and (r.params or {}).get("experiment") != "prefix_cache"]
    errors = []
    expected_runs = 1 if protocol.capacity_search else protocol.repeats
    if len(perf) != expected_runs:
        errors.append(f"Expected {expected_runs} performance measurements; got {len(perf)}")
    for run in runs:
        if run.status != "succeeded" or not run.result or run.result.get("error"):
            errors.append(f"Measurement {run.id}: {run.status} or incomplete result")
    metrics = {}
    for key in ("output_throughput", "p99_ttft_ms", "request_throughput"):
        values = [(r.result or {}).get("metrics", {}).get(key) for r in perf]
        if values and all(number(v) and v >= 0 for v in values):
            metrics[key] = statistics.median(values)
        else:
            errors.append(f"Missing metric: {key}")
    for run in perf:
        m = (run.result or {}).get("metrics", {})
        if protocol.capacity_search:
            search = (run.result or {}).get("capacity_search") or {}
            best = search.get("best") or {}
            rates = best.get("slo_rates") or {}
            expected = protocol.repeats * protocol.performance.request_count
            if (
                not search.get("confirmed")
                or best.get("repetitions") != protocol.repeats
                or not number(best.get("target_rate"))
                or best["target_rate"] <= 0
                or m.get("requested") != expected
                or not all(number(rates.get(key)) and rates[key] >= 0 for key in ("rpm", "input_tpm", "output_tpm"))
                or not number(m.get("slo_completed"))
                or m["slo_completed"] * 100 < expected * protocol.performance.slo.min_slo_percent
                or not number(m.get("completed"))
                or not 0 <= m["slo_completed"] <= m["completed"] <= expected
                or (expected - m["completed"]) * 100 > expected * protocol.performance.slo.max_error_percent
            ):
                errors.append(
                    f"Measurement {run.id}: no confirmed operating point ({search.get('stop_reason', 'missing')})"
                )
            else:
                metrics.update({f"slo_{k}": v for k, v in rates.items()})
                metrics["target_arrivals_per_sec"] = best["target_rate"]
                metrics["error_percent"] = 100 * (expected - m["completed"]) / expected
            continue
        if (
            m.get("completed") != protocol.performance.request_count
            or m.get("failed", 0) != 0
            or m.get("skipped", 0) != 0
        ):
            errors.append(f"Measurement {run.id}: incomplete requests")
        if (
            protocol.performance.workload_type == "agentic_replay"
            and m.get("completed_sessions") != protocol.performance.num_sessions
        ):
            errors.append(f"Measurement {run.id}: incomplete conversations")
    if metrics.get("output_throughput") is not None:
        metrics["throughput_per_gpu"] = metrics["output_throughput"] / max(gpu_count, 1)
    if protocol.max_ttft_p99_ms and metrics.get("p99_ttft_ms", math.inf) > protocol.max_ttft_p99_ms:
        errors.append("TTFT P99 exceeds the required ceiling")
    if protocol.min_output_throughput and metrics.get("output_throughput", -1) < protocol.min_output_throughput:
        errors.append("Output throughput is below the required floor")
    accuracy = [r for r in runs if r.kind == "accuracy"]
    quality = []
    for gate in protocol.quality_gates:
        results = (accuracy[0].result or {}).get("metrics", {}).get("results", {}) if len(accuracy) == 1 else {}
        value = results.get(gate.task, {}).get(gate.metric)
        passed = number(value) and value >= gate.minimum
        quality.append({**gate.model_dump(), "value": value, "passed": passed})
        if not passed:
            errors.append(f"Quality floor not met or missing: {gate.task}/{gate.metric}")
    caches = [r for r in runs if (r.params or {}).get("experiment") == "prefix_cache"]
    if protocol.prefix_len:
        if len(caches) != 1 or any(
            (caches[0].result or {}).get("phases", {}).get(p, {}).get("completed") != protocol.performance.num_prompts
            for p in ("individual", "shared")
        ):
            errors.append("Cache comparison is missing or incomplete")
    return {
        "passed": not errors,
        "errors": errors,
        "metrics": metrics,
        "quality": quality,
        "measurement_ids": [str(r.id) for r in runs],
        "selection_metric": protocol.selection_metric,
    }


def rank(candidates, metric):
    eligible = [
        c
        for c in candidates
        if (c.assessment or {}).get("passed") and number(c.assessment.get("metrics", {}).get(metric))
    ]
    return sorted(
        eligible, key=lambda c: (c.assessment["metrics"][metric] * (1 if metric == "p99_ttft_ms" else -1), c.position)
    )


def classify_diagnostics(evidence):
    text = json.dumps(evidence, ensure_ascii=False).lower()
    for code, terms in (
        ("gpu_oom", ("cuda out of memory", "cuda error: out of memory", "torch.outofmemoryerror")),
        ("memory_limit", ("oomkilled",)),
        (
            "image_unavailable",
            (
                "imagepullbackoff",
                "errimagepull",
            ),
        ),
        (
            "configuration_error",
            (
                "unrecognized arguments",
                "invalid argument",
                "modulenotfounderror",
            ),
        ),
        (
            "probe_restart",
            (
                "startup probe failed",
                "liveness probe failed",
            ),
        ),
        (
            "scheduling",
            (
                "unschedulable",
                "failedscheduling",
            ),
        ),
        ("restarting", ("crashloopbackoff",)),
    ):
        if any(term in text for term in terms):
            return code
    return "loading"
