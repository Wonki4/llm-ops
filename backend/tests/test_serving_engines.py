"""Serving engine catalogue, engine_args rendering, and launch helpers."""

import types

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe


def test_engine_columns_exist_on_both_tables():
    for model in (CustomServingRecipe, CustomModelDeployment):
        cols = model.__table__.columns
        assert cols["engine"].nullable is False
        assert cols["engine"].server_default.arg == "vllm"
        assert cols["engine_args"].nullable is True
