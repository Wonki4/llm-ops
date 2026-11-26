"""Logical model names group multiple LiteLLM registrations without copying their state."""

from urllib.parse import urlsplit, urlunsplit

from app.clients.litellm import deployment_base_prices
from app.services.serving_topology import _cloud_provider, _endpoint

_PUBLIC_PARAMS = {"model", "custom_llm_provider", "rpm", "tpm", "timeout", "max_retries", "stream_timeout", "weight"}
_PUBLIC_INFO = {
    "id", "db_model", "litellm_provider", "mode", "base_model", "max_tokens", "max_input_tokens",
    "max_output_tokens", "input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost",
    "cache_creation_input_token_cost", "access_groups", "off_peak_pricing",
}


def public_model_info(row: dict) -> dict:
    """Return the catalog's public fields with effective deployment-specific prices."""
    params, info = row.get("litellm_params") or {}, row.get("model_info") or {}
    return {
        "model_name": row.get("model_name", ""),
        "litellm_params": {k: v for k, v in params.items() if k in _PUBLIC_PARAMS},
        "model_info": {
            **{k: v for k, v in info.items()
               if k in _PUBLIC_INFO or (k.startswith("supports_") and isinstance(v, bool))},
            **deployment_base_prices(row),
        },
    }


def safe_api_base(value: str | None) -> str | None:
    if not value:
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if url.port:
            host += f":{url.port}"
        return urlunsplit((url.scheme, host, url.path, "", ""))
    except ValueError:
        return None


def registration_view(row: dict, managed: dict[str, dict] | None = None) -> dict:
    info, params = row.get("model_info") or {}, row.get("litellm_params") or {}
    model_id = str(info.get("id") or "")
    owner = (managed or {}).get(model_id)
    upstream = str(params.get("model") or "")
    provider = info.get("litellm_provider") or params.get("custom_llm_provider") or upstream.partition("/")[0]
    return {
        "id": model_id,
        "model_name": row.get("model_name", ""),
        "model": upstream,
        "provider": provider,
        "api_base": safe_api_base(params.get("api_base")),
        "api_version": params.get("api_version"),
        "db_model": info.get("db_model") is True,
        "managed": owner,
        "origin": "cluster"
        if owner
        else "cloud"
        if _cloud_provider(params, info, _endpoint(params.get("api_base")))
        else "unknown",
        "editable": info.get("db_model") is True and not owner,
        "api_base_editable": info.get("db_model") is True
        and (owner or {}).get("validation_state") not in ("candidate", "retired"),
        "access_groups": info.get("access_groups") or [],
        "prices": deployment_base_prices(row),
    }


def merge_models(rows: list[dict], catalog: dict[str, dict], *, admin: bool, sanitize) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        name = row.get("model_name")
        if name:
            grouped.setdefault(name, []).append(sanitize(row))
    models = []
    for name in dict.fromkeys([*grouped, *catalog]):
        entry, registrations = catalog.get(name), grouped.get(name, [])
        if not admin and (not entry or entry.get("visible") is False):
            continue
        # Preserve the legacy representative but expose the complete registration list.
        representative = registrations[0] if registrations else None
        if representative:
            groups = sorted({g for r in registrations for g in (r.get("model_info", {}).get("access_groups") or [])})
            representative = {**representative, "model_info": {**representative["model_info"], "access_groups": groups}}
        models.append(
            {"model_name": name, "catalog": entry, "litellm_info": representative, "registrations": registrations}
        )
    return models
