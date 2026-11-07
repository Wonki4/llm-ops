"""How a portal deployment is registered in LiteLLM.

Two names are involved and they differ on purpose:

* ``litellm_name`` is what callers ask LiteLLM for (/model/new ``model_name``,
  the catalog row). It defaults to the deployment's ``model_name`` (the K8s
  name) and may be shared: LiteLLM load-balances deployments with one name.
* ``served_model_name`` is what LiteLLM must send to the server. vLLM answers
  only to ``--served-model-name`` or, without it, the full ``--model`` value,
  so it is read from the rendered launch line, not guessed from the path.
"""

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, HttpUrl

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.benchmark_serving import served_and_model_arg, serving_cli
from app.services.model_deployment_manifests import serving_api_key
from app.services.serving_engines import container_launch


def litellm_name(dep: CustomModelDeployment) -> str:
    return getattr(dep, "litellm_model_name", None) or dep.model_name


def served_model_name(dep: CustomModelDeployment) -> str:
    """The ``model`` the serving answers to. P/D: the decode pool's (it answers through the router)."""
    if pd_serving.is_pd(dep):
        command = None
        args = pd_serving.role_args(dep, pd_serving.role_view(dep, "decode"))
    else:
        command, args = container_launch(dep)
    served, _ = served_and_model_arg(serving_cli({"command": command, "args": args}))
    return served or dep.model_path


def _http_base(value: str) -> str:
    value = value.strip()
    if not value.lower().startswith(("http://", "https://")) or any(c.isspace() for c in value):
        raise ValueError("API base must be an absolute HTTP or HTTPS URL")
    if not urlsplit(value).netloc or "\\" in value:
        raise ValueError("API base must include a valid hostname")
    url = HttpUrl(value)
    if url.username or url.password or url.query is not None or url.fragment is not None:
        raise ValueError("API base must not contain credentials, a query or a fragment")
    return value.rstrip("/")


ApiBase = Annotated[str, AfterValidator(_http_base)]


def with_v1(base: str) -> str:
    base = base.rstrip("/")
    return base if urlsplit(base).path.endswith("/v1") else f"{base}/v1"


def ingress_api_base(dep: CustomModelDeployment) -> str:
    api_base = f"https://{dep.ingress_host}".rstrip("/")
    if dep.ingress_path and dep.ingress_path != "/":
        api_base = f"{api_base}{dep.ingress_path.rstrip('/')}"
    return api_base


def registration_fields(model_name: str, served_name: str, api_base: str) -> dict:
    return {"model_name": model_name, "litellm_model": f"hosted_vllm/{served_name}", "api_base": api_base}


def registration_preview(dep: CustomModelDeployment, api_base: str | None = None) -> dict:
    """The /model/new arguments. ``api_base`` overrides the ingress URL (P/D: the router's host)."""
    base = getattr(dep, "litellm_api_base", None) or with_v1(api_base or ingress_api_base(dep))
    return registration_fields(litellm_name(dep), served_model_name(dep), base)


def registration(dep: CustomModelDeployment, api_base: str | None = None) -> dict:
    auth_source = pd_serving.role_view(dep, "decode").pool if pd_serving.is_pd(dep) else dep
    return {
        **registration_preview(dep, api_base),
        "api_key": serving_api_key(auth_source.vllm_extra_args, auth_source.env),
    }
