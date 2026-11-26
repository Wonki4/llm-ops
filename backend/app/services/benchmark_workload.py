"""Validate the subset of workload options executed by the portal load runner."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ServiceObjectives(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    ttft_ms: float | None = Field(None, gt=0)
    tpot_ms: float | None = Field(None, gt=0)
    e2el_ms: float | None = Field(None, gt=0)
    max_error_percent: float = Field(1, ge=0, lt=100)
    min_slo_percent: float = Field(99, gt=0, le=100)

    @model_validator(mode="after")
    def latency_target(self):
        if all(limit is None for limit in (self.ttft_ms, self.tpot_ms, self.e2el_ms)):
            raise ValueError("Set at least one latency objective")
        return self


def default_objectives():
    return ServiceObjectives(ttft_ms=2000, tpot_ms=100)


class PerformanceWorkload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    workload_version: Literal[1, 2] = 1
    workload_type: Literal["synthetic", "agentic_replay"] = "synthetic"
    input_length_mode: Literal["tokenizer", "text_estimate"] = "tokenizer"
    cache_ratio: float = Field(0, ge=0, le=100)
    num_sessions: int = Field(40, gt=0)
    scenario: dict | None = None
    random_input_len: int = Field(1024, gt=0)
    random_output_len: int = Field(128, gt=0)
    num_prompts: int = Field(200, gt=0)
    max_concurrency: int = Field(32, gt=0)
    request_rate: float | None = Field(None, gt=0)
    seed: int = 0
    ignore_eos: bool = False
    goodput: str | None = None
    slo: ServiceObjectives | None = None

    @model_validator(mode="after")
    def scenario_snapshot(self):
        if self.workload_version == 1 and self.input_length_mode != "tokenizer":
            raise ValueError("Text-estimated inputs require workload version 2")
        if self.workload_type == "agentic_replay":
            from app.services.benchmark_scenarios import pin_scenario

            self.scenario = pin_scenario(self.scenario)
            self.ignore_eos = False
            self.cache_ratio = 0
            if self.workload_version != 2:
                raise ValueError("Agentic replay requires workload version 2")
        elif self.workload_version == 1 and self.cache_ratio != 0:
            raise ValueError("Shared input ratios require workload version 2")
        elif self.scenario is not None:
            raise ValueError("Synthetic input cannot include a scenario")
        return self

    @property
    def request_count(self):
        return self.num_sessions * len(self.scenario["turns"]) if self.scenario else self.num_prompts

    @field_validator("goodput")
    @classmethod
    def valid_limits(cls, value):
        if value:
            for item in value.split():
                key, sep, limit = item.partition(":")
                if (
                    key not in {"ttft", "tpot", "e2el"}
                    or not sep
                    or not math.isfinite(float(limit))
                    or float(limit) <= 0
                ):
                    raise ValueError("Expected positive millisecond thresholds, e.g. ttft:200 tpot:50")
        return value


BOOKKEEPING = {
    "capacity_search",
    "_capacity_identity",
    "preset",
    "preset_name",
    "tokenizer",
    "external_source",
    "nfs_server",
    "nfs_path",
    "nfs_mount_path",
}


def performance_workload(params):
    if params.get("extra_args"):
        raise ValueError(
            "Legacy CLI flags (extra_args) cannot be executed by the portal runner. Remove them before rerunning."
        )
    fields = {k: v for k, v in params.items() if k not in BOOKKEEPING and k != "extra_args"}
    if "workload_version" not in fields and any(
        k in fields for k in ("cache_ratio", "workload_type", "scenario", "num_sessions", "input_length_mode")
    ):
        fields["workload_version"] = 2
    return PerformanceWorkload.model_validate(fields).model_dump(exclude_none=True)


class AccuracyWorkload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    tasks: list[str] | str = "mmlu"
    batch_size: int = Field(1, gt=0)
    num_concurrent: int = Field(4, gt=0)
    num_fewshot: int | None = Field(None, ge=0)
    limit: int | float | None = Field(None, gt=0)
    apply_chat_template: bool = False
    gen_kwargs: str | None = None
    tokenizer: str | None = None

    @field_validator("tasks")
    @classmethod
    def valid_tasks(cls, value):
        names = value.split(",") if isinstance(value, str) else value
        if not names or any(not name.strip() for name in names):
            raise ValueError("Expected at least one standard task name")
        return value


def validate_run_options(kind, params):
    if kind == "accuracy":
        AccuracyWorkload.model_validate({k: v for k, v in params.items() if k not in BOOKKEEPING - {"tokenizer"}})
    elif params.get("experiment") != "prefix_cache":
        performance_workload(params)
