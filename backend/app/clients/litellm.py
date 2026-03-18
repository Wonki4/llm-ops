"""Typed HTTP client wrapper for LiteLLM proxy management API."""

from typing import Any

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

    # ──── Key endpoints ────
    async def generate_key(
        self,
        user_id: str,
        team_id: str,
        key_alias: str | None = None,
        models: list[str] | None = None,
        max_budget: float | None = None,
        budget_duration: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"user_id": user_id, "team_id": team_id}
        if key_alias:
            payload["key_alias"] = key_alias
        if models:
            payload["models"] = models
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if budget_duration:
            payload["budget_duration"] = budget_duration
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

    # ──── User endpoints ────
    async def get_user_info(self, user_id: str) -> dict:
        return await self._request("GET", "/user/info", params={"user_id": user_id})

    async def create_user(self, user_id: str, user_email: str | None = None) -> dict:
        payload: dict[str, Any] = {"user_id": user_id}
        if user_email:
            payload["user_email"] = user_email
        return await self._request("POST", "/user/new", json=payload)

    # ──── Model endpoints ────
    async def list_models(self) -> list[dict]:
        data = await self._request("GET", "/v1/models")
        return data.get("data", []) if isinstance(data, dict) else data

    async def get_model_info(self) -> list[dict]:
        data = await self._request("GET", "/model/info")
        return data.get("data", []) if isinstance(data, dict) else data

    async def delete_model(self, model_id: str) -> dict:
        return await self._request("POST", "/model/delete", json={"id": model_id})


def get_litellm_client() -> LiteLLMClient:
    """FastAPI dependency for LiteLLM client."""
    return LiteLLMClient()
