"""Thin async client for the ArgoCD REST API.

Used to manage llm-d stacks' Applications through a registered ArgoCD connection
(server URL + bearer token). Opens a fresh httpx client per call — the cadence is
rare admin actions + status reads, so connection reuse isn't worth the lifecycle
complexity. ``transport`` is injectable for tests.
"""

from __future__ import annotations

import httpx


class ArgoCDClient:
    def __init__(
        self,
        server_url: str,
        token: str,
        *,
        insecure_skip_verify: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = server_url.rstrip("/")
        self._token = token
        self._verify = not insecure_skip_verify
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self._token}"},
            verify=self._verify,
            transport=self._transport,
            timeout=self._timeout,
        )

    async def version(self) -> str:
        """ArgoCD server version (also serves as a connection test)."""
        async with self._client() as c:
            r = await c.get("/api/version")
            r.raise_for_status()
            body = r.json()
            return body.get("Version") or body.get("version") or ""

    async def userinfo(self) -> dict:
        async with self._client() as c:
            r = await c.get("/api/v1/session/userinfo")
            r.raise_for_status()
            return r.json()

    async def create_application(self, body: dict) -> None:
        """Create (or upsert) an Application."""
        async with self._client() as c:
            r = await c.post("/api/v1/applications", json=body, params={"upsert": "true"})
            r.raise_for_status()

    async def get_application(self, name: str) -> dict | None:
        """Read an Application; None if it does not exist."""
        async with self._client() as c:
            r = await c.get(f"/api/v1/applications/{name}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def get_resource(
        self,
        app_name: str,
        *,
        name: str,
        namespace: str,
        kind: str,
        version: str,
        group: str = "",
    ) -> str | None:
        """Live manifest (JSON string) of one of an Application's managed
        resources; None if the resource is gone."""
        async with self._client() as c:
            r = await c.get(
                f"/api/v1/applications/{app_name}/resource",
                params={
                    "resourceName": name,
                    "namespace": namespace,
                    "kind": kind,
                    "version": version,
                    "group": group or "",
                },
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json().get("manifest")

    async def delete_application(self, name: str, *, cascade: bool = True) -> None:
        """Delete an Application (cascades to its workloads); ignore if gone."""
        async with self._client() as c:
            r = await c.delete(
                f"/api/v1/applications/{name}", params={"cascade": "true" if cascade else "false"}
            )
            if r.status_code == 404:
                return
            r.raise_for_status()


async def probe_argocd(server_url: str, token: str, *, insecure_skip_verify: bool = False) -> str:
    """Connection test: return the ArgoCD server version, or raise."""
    return await ArgoCDClient(server_url, token, insecure_skip_verify=insecure_skip_verify).version()
