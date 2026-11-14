"""Construct a deployment request from a persisted serving recipe and its instance settings."""

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.api.model_deployments import CreateDeploymentRequest
from app.api.serving_recipes import RecipeBody

Count = Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]


class DeployTarget(BaseModel):
    """Instance settings. Serving configuration comes from the saved recipe."""

    model_config = ConfigDict(extra="forbid")
    model_name: str
    ingress_host: str
    cluster_id: uuid.UUID | None = None
    namespace: str | None = None
    ingress_path: str = "/"
    ingress_class: str | None = None
    gpu_type: str | None = None
    litellm_api_base: str | None = None
    litellm_model_name: str | None = None
    router_stack_id: uuid.UUID | None = None
    replicas: Count | None = None
    prefill_replicas: Count | None = None
    decode_replicas: Count | None = None


def deploy_body(recipe: dict, target: DeployTarget) -> CreateDeploymentRequest:
    config = {
        k: v for k, v in recipe.items() if k in RecipeBody.model_fields and k in CreateDeploymentRequest.model_fields
    }
    instance = target.model_dump(mode="json", exclude_none=True, exclude={"prefill_replicas", "decode_replicas"})
    if config.get("serving_mode") == "pd":
        if target.replicas is not None:
            raise ValueError("Specify prefill_replicas and decode_replicas for a PD deployment")
        pd = dict(config.get("pd_config") or {})
        for role, count in (("prefill", target.prefill_replicas), ("decode", target.decode_replicas)):
            if count is not None:
                pd[role] = {**(pd.get(role) or {}), "replicas": count}
        config["pd_config"] = pd
        instance["replicas"] = (pd.get("decode") or {}).get("replicas", 1)
        instance.setdefault("ingress_class", (pd.get("router") or {}).get("ingress_class") or "nginx")
    elif any(v is not None for v in (target.prefill_replicas, target.decode_replicas, target.router_stack_id)):
        raise ValueError("Pool replica counts and router_stack_id require a PD recipe")
    return CreateDeploymentRequest(**(config | instance), recipe_id=recipe["id"])
