"""Typed HTTP client wrapper for LiteLLM proxy management API."""

import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings


class LiteLLMClient:
    """Wrapper around LiteLLM proxy management endpoints.

    All calls use the admin API key. The frontend never sees this key.
    """

    def __init__(self) -> None:
        self._base_url = settings.litellm_base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.litellm_admin_api_key}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, headers=self._headers, timeout=30.0, verify=settings.ssl_verify)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with self._client() as client:
            resp = await client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()

    # ──── Health ────
    async def health(self) -> dict:
        return await self._request("GET", "/health")

    async def smoke_chat(self, model_id: str) -> dict:
        """Probe the exact registered deployment and verify which one answered."""
        started = time.monotonic()
        payload = {
            "model": model_id, "max_tokens": 8, "stream": False,
            "messages": [{"role": "user", "content": f"Reply OK. Request {uuid.uuid4().hex}"}],
        }
        try:
            async with self._client() as client:
                response = await client.post("/v1/chat/completions", json=payload, headers={"Cache-Control": "no-cache"})
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            return {"ok": False, "reason": f"Inference returned HTTP {exc.response.status_code}"}
        except (httpx.RequestError, ValueError):
            return {"ok": False, "reason": "Inference endpoint unavailable or returned invalid JSON"}
        if response.headers.get("x-litellm-model-id") != model_id:
            return {"ok": False, "reason": "Could not verify that the requested deployment served the response"}
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices or not isinstance(choices, list) or not isinstance(choices[0], dict):
            return {"ok": False, "reason": "No chat completion choice received"}
        message = choices[0].get("message")
        if not isinstance(message, dict) or not (message.get("content") or message.get("reasoning_content")):
            return {"ok": False, "reason": "Empty chat completion"}
        return {"ok": True, "model_id": model_id, "latency_ms": round((time.monotonic() - started) * 1000)}

    # ──── Team endpoints ────
    async def list_teams(self) -> list[dict]:
        data = await self._request("GET", "/team/list")
        return data if isinstance(data, list) else data.get("teams", [])

    async def get_team_info(self, team_id: str) -> dict:
        return await self._request("GET", "/team/info", params={"team_id": team_id})

    async def get_available_teams(self) -> list[dict]:
        data = await self._request("GET", "/team/available")
        return data if isinstance(data, list) else data.get("teams", [])

    async def add_team_member(self, team_id: str, user_id: str, role: str = "user") -> dict:
        return await self._request(
            "POST",
            "/team/member_add",
            json={"team_id": team_id, "member": [{"role": role, "user_id": user_id}]},
        )

    async def remove_team_member(self, team_id: str, user_id: str) -> dict:
        return await self._request(
            "POST",
            "/team/member_delete",
            json={"team_id": team_id, "user_id": user_id},
        )

    async def update_team_member(
        self,
        team_id: str,
        user_id: str,
        *,
        max_budget_in_team: float | None = None,
        tpm_limit: int | None = None,
        rpm_limit: int | None = None,
    ) -> dict:
        """Set a team member's per-member budget and rate limits via /team/member_update.

        The limits are scoped to this (team, user): the proxy enforces tpm/rpm
        on a ``{team_id}:{user_id}`` counter, so they hold across all of the
        member's keys in the team (not multiplied per key). LiteLLM
        clone-on-writes a dedicated budget row when the membership still points
        at the team's shared default budget, so the change only affects this
        member — never the others sharing the default.

        Only non-None fields are sent (LiteLLM merges, leaving omitted fields
        untouched).
        """
        payload: dict[str, Any] = {"team_id": team_id, "user_id": user_id}
        if max_budget_in_team is not None:
            payload["max_budget_in_team"] = max_budget_in_team
        if tpm_limit is not None:
            payload["tpm_limit"] = tpm_limit
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit
        return await self._request("POST", "/team/member_update", json=payload)

    # ──── Key endpoints ────
    async def generate_key(
        self,
        user_id: str,
        team_id: str | None,
        key_alias: str | None = None,
        models: list[str] | None = None,
        max_budget: float | None = None,
        budget_duration: str | None = None,
        key: str | None = None,
        tpm_limit: int | None = None,
        rpm_limit: int | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """``team_id=None`` mints a personal key (no team; the user row governs it)."""
        payload: dict[str, Any] = {"user_id": user_id}
        if team_id:
            payload["team_id"] = team_id
        if tags:
            payload["tags"] = tags
        if key_alias:
            payload["key_alias"] = key_alias
        if models:
            payload["models"] = models
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if budget_duration:
            payload["budget_duration"] = budget_duration
        if key:
            payload["key"] = key
        if tpm_limit is not None:
            payload["tpm_limit"] = tpm_limit
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit
        if metadata is not None:
            payload["metadata"] = metadata
        return await self._request("POST", "/key/generate", json=payload)

    async def list_keys(self, user_id: str | None = None, team_id: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if user_id:
            params["user_id"] = user_id
        if team_id:
            params["team_id"] = team_id
        data = await self._request("GET", "/key/list", params=params)
        return data if isinstance(data, list) else data.get("keys", [])

    async def get_key_info(self, key: str) -> dict:
        return await self._request("GET", "/key/info", params={"key": key})

    async def delete_key(self, key: str) -> dict:
        return await self._request("POST", "/key/delete", json={"keys": [key]})

    async def update_key(self, key: str, **kwargs: Any) -> dict:
        payload = {"key": key, **kwargs}
        return await self._request("POST", "/key/update", json=payload)

    async def update_team(self, team_id: str, **kwargs: Any) -> dict:
        payload = {"team_id": team_id, **kwargs}
        return await self._request("POST", "/team/update", json=payload)

    async def create_team(
        self,
        *,
        team_alias: str,
        models: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Create a new team via /team/new.

        Extra budget/limit fields (max_budget, budget_duration, tpm_limit,
        rpm_limit, ...) are passed straight through as kwargs.
        """
        payload: dict[str, Any] = {
            "team_alias": team_alias,
            "models": models if models is not None else [],
            **kwargs,
        }
        return await self._request("POST", "/team/new", json=payload)

    # ──── User endpoints ────
    async def get_user_info(self, user_id: str) -> dict:
        return await self._request("GET", "/user/info", params={"user_id": user_id})

    async def create_user(self, user_id: str, user_email: str | None = None, **fields: Any) -> dict:
        payload: dict[str, Any] = {"user_id": user_id, "auto_create_key": False, **fields}
        if user_email:
            payload["user_email"] = user_email
        return await self._request("POST", "/user/new", json=payload)

    async def update_user(self, user_id: str, **fields: Any) -> dict:
        """``POST /user/update`` with only the given fields (models, max_budget, budget_duration, tpm/rpm)."""
        payload: dict[str, Any] = {"user_id": user_id, **{k: v for k, v in fields.items() if v is not ...}}
        return await self._request("POST", "/user/update", json=payload)

    # ──── Model endpoints ────
    async def list_models(self) -> list[dict]:
        data = await self._request("GET", "/v1/models")
        return data.get("data", []) if isinstance(data, dict) else data

    async def get_model_info(self) -> list[dict]:
        data = await self._request("GET", "/model/info")
        return data.get("data", []) if isinstance(data, dict) else data

    async def delete_model(self, model_id: str) -> dict:
        return await self._request("POST", "/model/delete", json={"id": model_id})

    # ──── Model access groups (model_info.access_groups; DB models only) ────
    async def create_access_group(self, name: str, model_names: list[str]) -> dict:
        """``/access_group/new``: tag every DB deployment of ``model_names`` with ``name`` (409 if it exists)."""
        return await self._request("POST", "/access_group/new", json={"access_group": name, "model_names": model_names})

    async def update_access_group(self, name: str, model_names: list[str]) -> dict:
        """``/access_group/{name}/update``: the group's DB models become exactly ``model_names``."""
        return await self._request(
            "PUT", f"/access_group/{quote(name, safe='')}/update", json={"model_names": model_names}
        )

    async def delete_access_group(self, name: str) -> dict:
        """``/access_group/{name}/delete``: untag every DB deployment (config models keep theirs)."""
        return await self._request("DELETE", f"/access_group/{quote(name, safe='')}/delete")

    async def update_model(self, model_id: str, *, litellm_params: dict | None = None) -> dict:
        """Update a deployment's litellm_params (e.g. input/output_cost_per_token)."""
        payload: dict[str, Any] = {"model_info": {"id": model_id}}
        if litellm_params is not None:
            payload["litellm_params"] = litellm_params
        return await self._request("POST", "/model/update", json=payload)

    async def create_model(
        self,
        *,
        model_name: str,
        litellm_model: str,
        api_base: str,
        api_key: str | None = None,
        extra_params: dict | None = None,
    ) -> dict:
        """Register a new model deployment in LiteLLM via /model/new.

        `litellm_model` includes the provider prefix (e.g. 'hosted_vllm/<served-name>').
        Returns LiteLLM's deployment record.
        """
        litellm_params: dict[str, Any] = {"model": litellm_model, "api_base": api_base}
        if api_key:
            litellm_params["api_key"] = api_key
        if extra_params:
            litellm_params.update(extra_params)
        payload = {"model_name": model_name, "litellm_params": litellm_params}
        return await self._request("POST", "/model/new", json=payload)


def get_litellm_client() -> LiteLLMClient:
    """FastAPI dependency for LiteLLM client."""
    return LiteLLMClient()
