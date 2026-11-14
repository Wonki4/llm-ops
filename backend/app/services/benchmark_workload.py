"""Validate the subset of workload options executed by the portal load runner."""

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PerformanceWorkload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    random_input_len: int = Field(1024, gt=0)
    random_output_len: int = Field(128, gt=0)
    num_prompts: int = Field(200, gt=0)
    max_concurrency: int = Field(32, gt=0)
    request_rate: float | None = Field(None, gt=0)
    seed: int = 0
    ignore_eos: bool = False
    goodput: str | None = None

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


BOOKKEEPING = {"preset", "tokenizer", "external_source", "nfs_server", "nfs_path", "nfs_mount_path"}


def performance_workload(params):
    if params.get("extra_args"):
        raise ValueError(
            "Legacy CLI flags (extra_args) cannot be executed by the portal runner. Remove them before rerunning."
        )
    fields = {k: v for k, v in params.items() if k not in BOOKKEEPING and k != "extra_args"}
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
