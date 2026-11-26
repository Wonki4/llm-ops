"""Resolve a cluster's Prometheus query endpoint and enforce its isolation scope."""

import json
import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.services import crypto


class PrometheusConfigError(ValueError):
    pass


class PrometheusFields(BaseModel):
    prometheus_url: str | None = None
    prometheus_shared: bool = False
    prometheus_cluster_label: str | None = None
    prometheus_cluster_value: str | None = None
    prometheus_bearer_token: SecretStr | None = Field(default=None, repr=False, exclude=True)

    @property
    def has_prometheus_changes(self) -> bool:
        return not self.model_fields_set.isdisjoint(PrometheusFields.model_fields)


@dataclass(frozen=True)
class PrometheusConnection:
    url: str
    shared: bool
    label_name: str | None
    label_value: str | None
    token: str | None = field(default=None, repr=False)

    @property
    def cluster_label(self) -> tuple[str, str] | None:
        return (self.label_name, self.label_value) if self.label_name and self.label_value else None


def _url(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    message = "Prometheus 주소에는 인증 정보나 쿼리 없이 HTTP(S) 기본 URL을 입력하세요."
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        url = httpx.URL(raw)
    except (httpx.InvalidURL, ValueError, UnicodeError) as exc:
        raise PrometheusConfigError(message) from exc
    host = url.host.rstrip(".")
    if (
        url.scheme not in {"http", "https"}
        or not host
        or port == 0
        or "@" in parsed.netloc
        or any(c in raw for c in "?#\\")
        or any(c.isspace() or ord(c) < 32 for c in raw)
        or len(raw) > 1024
    ):
        raise PrometheusConfigError(message)
    normalized = str(url.copy_with(host=host)).rstrip("/")
    if len(normalized) > 1024:
        raise PrometheusConfigError(message)
    return normalized


def _connection(
    raw_url: str | None, shared: bool, label: str | None, value: str | None, token: str | None = None
) -> PrometheusConnection | None:
    url = _url(raw_url)
    label, value = (label or "").strip(), (value or "").strip()
    if not url:
        if shared or label or value or token:
            raise PrometheusConfigError("Prometheus 조회 주소를 설정하세요.")
        return None
    if bool(label) != bool(value) or (shared and not label):
        raise PrometheusConfigError("중앙 Prometheus 조회에는 클러스터 라벨 이름과 고유 값이 모두 필요합니다.")
    if label and (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", label)
        or label.startswith("__")
        or label in {"le", "pod", "namespace", "model_name", "portal_latency_kind"}
    ):
        raise PrometheusConfigError("유효한 클러스터 식별 라벨 이름을 입력하세요.")
    if len(value) > 256 or any(not c.isprintable() for c in value):
        raise PrometheusConfigError("유효한 클러스터 식별 라벨 값을 입력하세요.")
    if token and (not token.isascii() or not token.isprintable() or any(c.isspace() for c in token)):
        raise PrometheusConfigError("올바른 Prometheus Bearer 토큰을 입력하세요.")
    return PrometheusConnection(url, shared, label or None, value or None, token or None)


def _token(cluster: CustomK8sCluster) -> str | None:
    if not cluster.prometheus_token_encrypted:
        return None
    try:
        return crypto.decrypt(cluster.prometheus_token_encrypted)
    except crypto.InvalidToken as exc:
        raise PrometheusConfigError("저장된 Prometheus 토큰을 읽을 수 없습니다. 다시 입력하세요.") from exc


def _check_peer(own: PrometheusConnection, peer):
    # A central URL may already have been configured as dedicated before a
    # second cluster registers it. Enforce the same rules for saves and reads.
    try:
        peer_url = _url(peer.prometheus_url)
    except PrometheusConfigError:
        # An unusable environment URL has no query destination. Resolving that
        # portal cluster fails on its own; other endpoints can still serve.
        return
    if peer_url == own.url:
        other = _connection(
            peer.prometheus_url, peer.prometheus_shared, peer.prometheus_cluster_label, peer.prometheus_cluster_value
        )
        if not own.cluster_label or not other.cluster_label or own.label_name != other.label_name:
            raise PrometheusConfigError("같은 Prometheus 주소를 쓰는 모든 클러스터에 같은 식별 라벨 이름을 설정하세요.")
        if own.label_value == other.label_value:
            raise PrometheusConfigError("이 Prometheus 주소에 이미 같은 클러스터 라벨 값이 있습니다.")


async def _check_peers(db: AsyncSession, own: PrometheusConnection, cluster_id: uuid.UUID | None, *, portal: bool):
    table = CustomK8sCluster
    stmt = select(
        table.prometheus_url, table.prometheus_shared, table.prometheus_cluster_label, table.prometheus_cluster_value
    ).where(table.prometheus_url == own.url)
    if cluster_id is not None:
        stmt = stmt.where(table.id != cluster_id)
    for peer in await db.execute(stmt):
        _check_peer(own, peer)
    if not portal and settings.prometheus_url:
        _check_peer(own, settings)


async def resolve_prometheus_connection(db: AsyncSession, cluster_id: uuid.UUID | None) -> PrometheusConnection | None:
    """NULL deployments use the portal cluster even if the UI default changes."""
    if cluster_id is None:
        conn = _connection(
            settings.prometheus_url,
            settings.prometheus_shared,
            settings.prometheus_cluster_label,
            settings.prometheus_cluster_value,
            settings.prometheus_bearer_token.get_secret_value(),
        )
    else:
        cluster = await db.get(CustomK8sCluster, cluster_id)
        if cluster is None:
            raise PrometheusConfigError("배포 클러스터의 Prometheus 설정을 찾을 수 없습니다.")
        conn = _connection(
            cluster.prometheus_url,
            cluster.prometheus_shared,
            cluster.prometheus_cluster_label,
            cluster.prometheus_cluster_value,
            _token(cluster),
        )
    if conn:
        await _check_peers(db, conn, cluster_id, portal=cluster_id is None)
    return conn


async def lock_prometheus_settings(db: AsyncSession):
    # The namespace constraint spans multiple rows and the implicit portal cluster.
    # A transaction-scoped mutex prevents concurrent creates/edits from each seeing
    # an empty slot at READ COMMITTED. Always acquire it before taking any row lock.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('portal.cluster-prometheus-settings'))"))


async def proposed_connection(
    db: AsyncSession, body: PrometheusFields, cluster: CustomK8sCluster | None
) -> PrometheusConnection | None:
    def effective(name):
        if name in body.model_fields_set or cluster is None:
            return getattr(body, name)
        return getattr(cluster, name)

    url = _url(effective("prometheus_url"))
    if not url and "prometheus_url" in body.model_fields_set and cluster and cluster.prometheus_url:
        return None  # Removing the endpoint also removes its credentials and labels.
    token = None
    if "prometheus_bearer_token" in body.model_fields_set:
        token = body.prometheus_bearer_token.get_secret_value() if body.prometheus_bearer_token else None
    elif cluster and cluster.prometheus_token_encrypted:
        if url != _url(cluster.prometheus_url):
            raise PrometheusConfigError("Prometheus 주소 변경 시 새 토큰을 입력하거나 기존 토큰을 제거하세요.")
        token = _token(cluster)
    conn = _connection(
        url,
        effective("prometheus_shared"),
        effective("prometheus_cluster_label"),
        effective("prometheus_cluster_value"),
        token,
    )
    if conn:
        await _check_peers(db, conn, cluster.id if cluster else None, portal=False)
    return conn


async def apply_prometheus_fields(db: AsyncSession, body: PrometheusFields, cluster: CustomK8sCluster):
    conn = await proposed_connection(db, body, cluster)
    cluster.prometheus_url = conn.url if conn else None
    cluster.prometheus_shared = bool(conn and conn.shared)
    cluster.prometheus_cluster_label = conn.label_name if conn else None
    cluster.prometheus_cluster_value = conn.label_value if conn else None
    if not conn or not conn.token:
        cluster.prometheus_token_encrypted = None
    elif "prometheus_bearer_token" in body.model_fields_set:
        cluster.prometheus_token_encrypted = crypto.encrypt(conn.token)


async def probe_prometheus(conn: PrometheusConnection) -> dict:
    # Check the query API and whether serving histograms exist in the requested
    # scope in one request. No range scan or unscoped retry on an empty result.
    selector = (
        'namespace!="",pod!="",le="+Inf",__name__=~"(vllm|sglang):(time_to_first_token_seconds|e2e_request_latency_seconds|'
        'inter_token_latency_seconds|time_per_output_token_seconds)_bucket"'
    )
    if conn.cluster_label:
        selector += f",{conn.label_name}={json.dumps(conn.label_value, ensure_ascii=False)}"
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.get(
                f"{conn.url}/api/v1/query",
                params={"query": "count({" + selector + "})", "timeout": "4s"},
                headers={"Authorization": f"Bearer {conn.token}"} if conn.token else {},
            )
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success" or data["data"]["resultType"] != "vector":
            raise ValueError
        results = data["data"]["result"]
        if not isinstance(results, list):
            raise ValueError
        found = any(float(row["value"][1]) > 0 for row in results)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        return {
            "ok": False,
            "series_found": None,
            "message": "Prometheus 조회 API 연결에 실패했습니다. 주소와 인증을 확인하세요.",
        }
    message = "서빙 시계열을 확인했습니다." if found else "선택한 범위에서 서빙 시계열을 찾지 못했습니다."
    return {"ok": True, "series_found": found, "message": "Prometheus 조회 API에 연결했습니다. " + message}
