"""Editable workload presets stored in existing portal settings.

Runs copy their effective values; subsequent preset edits never rewrite history.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from app.services.benchmark_workload import ServiceObjectives, default_objectives

LOAD_PRESETS: dict[str, dict] = {
    # short interactive chat
    "chat": {
        "random_input_len": 512,
        "random_output_len": 256,
        "num_prompts": 300,
        "max_concurrency": 32,
        "cache_ratio": 90,
        "name": "짧은 대화",
    },
    # RAG / document summarization
    "long_input": {
        "random_input_len": 100_000,
        "random_output_len": 1_000,
        "num_prompts": 120,
        "max_concurrency": 8,
        "cache_ratio": 90,
        "name": "긴 입력",
    },
    # generation-heavy
    "long_output": {
        "random_input_len": 256,
        "random_output_len": 1024,
        "num_prompts": 200,
        "max_concurrency": 16,
        "cache_ratio": 90,
        "name": "긴 출력",
    },
}

LOAD_PRESETS["agentic"] = {
    "name": "검색·문서 분석",
    "workload_type": "agentic_replay",
    "num_sessions": 40,
    "random_input_len": 4096,
    "random_output_len": 512,
    "num_prompts": 120,
    "max_concurrency": 8,
    "cache_ratio": 0,
}

_COMMON = {"seed": 0, "ignore_eos": False}


def preset_params(key: str) -> dict:
    """Expanded run params for a preset; raises KeyError on an unknown key."""
    return {
        **{k: v for k, v in LOAD_PRESETS[key].items() if k != "name"},
        **_COMMON,
        "preset": key,
        "preset_name": LOAD_PRESETS[key]["name"],
        "workload_version": 2,
    }


SETTINGS_KEY = "benchmark_presets_v2"


class LoadPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workload_type: Literal["synthetic", "agentic_replay"] = "synthetic"
    num_sessions: int = Field(40, gt=0)
    name: str = Field(min_length=1, max_length=80)
    random_input_len: int = Field(gt=0)
    random_output_len: int = Field(gt=0)
    num_prompts: int = Field(gt=0)
    max_concurrency: int = Field(gt=0)
    cache_ratio: float = Field(ge=0, le=100)
    slo: ServiceObjectives = Field(default_factory=default_objectives)

    @model_validator(mode="after")
    def clean_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Preset name cannot be blank")
        return self


class PresetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    presets: dict[str, LoadPreset] = Field(min_length=1, max_length=50)
    default_preset: str

    @model_validator(mode="after")
    def valid_default(self):
        if self.default_preset not in self.presets:
            raise ValueError("Choose an existing default preset before deleting it")
        if any(not key.strip() or len(key) > 100 for key in self.presets):
            raise ValueError("Invalid preset identifier")
        return self


async def load_presets(db):
    raw = (
        await db.execute(text("SELECT value FROM custom_portal_settings WHERE key = :key"), {"key": SETTINGS_KEY})
    ).scalar_one_or_none()
    return PresetSettings.model_validate(
        json.loads(raw) if raw else {"presets": LOAD_PRESETS, "default_preset": "long_input"}
    ).model_dump()


async def save_presets(db, value, user_id):
    await db.execute(
        text(
            "INSERT INTO custom_portal_settings (key, value, updated_by) VALUES (:key, :value, :user) "
            "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :user, updated_at = now()"
        ),
        {"key": SETTINGS_KEY, "value": value.model_dump_json(), "user": user_id},
    )
    await db.commit()
    return value.model_dump()
