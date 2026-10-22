"""How a portal deployment is registered in LiteLLM.

Two names are involved and they differ on purpose:

* ``litellm_name`` is what callers ask LiteLLM for (/model/new ``model_name``,
  the catalog row). It defaults to the deployment's ``model_name`` (the K8s
  name) and may be shared: LiteLLM load-balances deployments with one name.
* ``served_model_name`` is what LiteLLM must send to the server. vLLM answers
  only to ``--served-model-name`` or, without it, the full ``--model`` value,
  so it is read from the rendered launch line, not guessed from the path.
"""

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.benchmark_serving import served_and_model_arg, serving_cli
from app.services.model_deployment_manifests import build_all, serving_api_key


def litellm_name(dep: CustomModelDeployment) -> str:
    return getattr(dep, "litellm_model_name", None) or dep.model_name


def served_model_name(dep: CustomModelDeployment) -> str:
    """The ``model`` the serving answers to. P/D: the decode pool's (it answers through the router)."""
    deployments = [m for m in build_all(dep) if m["kind"] == "Deployment"]
    serving = next(
        (
            m
            for m in deployments
            if m["spec"]["template"]["metadata"]["labels"].get(pd_serving.LABEL_PD_ROLE) == "decode"
        ),
        deployments[0],
    )
    served, _ = served_and_model_arg(serving_cli(serving["spec"]["template"]["spec"]["containers"][0]))
    return served or dep.model_path


def ingress_api_base(dep: CustomModelDeployment) -> str:
    api_base = f"https://{dep.ingress_host}".rstrip("/")
    if dep.ingress_path and dep.ingress_path != "/":
        api_base = f"{api_base}{dep.ingress_path.rstrip('/')}"
    return api_base


def registration(dep: CustomModelDeployment, api_base: str | None = None) -> dict:
    """The /model/new arguments. ``api_base`` overrides the ingress URL (P/D: the router's host)."""
    # P/D: the decode pool answers through the router, so its auth settings apply.
    auth_source = pd_serving.role_view(dep, "decode").pool if pd_serving.is_pd(dep) else dep
    return {
        "model_name": litellm_name(dep),
        # vLLM/SGLang expose the OpenAI-compatible API; route through openai/ in LiteLLM.
        "litellm_model": f"openai/{served_model_name(dep)}",
        "api_base": api_base or ingress_api_base(dep),
        # The serving's own key so LiteLLM can reach it when auth is on (--api-key); "EMPTY" when open.
        "api_key": serving_api_key(auth_source.vllm_extra_args, auth_source.env),
    }
