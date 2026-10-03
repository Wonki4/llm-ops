"""Readiness / liveness probe settings for serving containers.

Recipes and deployments carry an optional ``probes`` JSON blob::

    {"readiness": {"path": "/health", "initial_delay_seconds": 60, ...},
     "liveness":  {"path": "/health", "initial_delay_seconds": 120, ...}}

Every field is optional: a missing readiness block (or missing fields in it)
falls back to the defaults the portal always used; a missing liveness block
means *no* liveness probe, which is the safe default for LLM servers whose
startup can take minutes. The probe port is always the serving port.

Pure functions, no I/O.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.services.serving_engines import SERVING_PORT

READINESS_DEFAULTS: dict = {
    "path": "/health",
    "initial_delay_seconds": 60,
    "period_seconds": 10,
    "timeout_seconds": 5,
    "failure_threshold": 30,
}

# Conservative: only restart a server that has been unresponsive for a while
# after a generous warm-up, so a slow model load never looks like a hang.
LIVENESS_DEFAULTS: dict = {
    "path": "/health",
    "initial_delay_seconds": 120,
    "period_seconds": 30,
    "timeout_seconds": 5,
    "failure_threshold": 3,
}


class ProbeSpec(BaseModel):
    path: str | None = None
    initial_delay_seconds: int | None = Field(None, ge=0, le=3600)
    period_seconds: int | None = Field(None, ge=1, le=3600)
    timeout_seconds: int | None = Field(None, ge=1, le=600)
    failure_threshold: int | None = Field(None, ge=1, le=1000)

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v.startswith("/") or any(ch.isspace() for ch in v):
            raise ValueError("probe path must start with '/' and contain no whitespace")
        return v


class ProbesSpec(BaseModel):
    readiness: ProbeSpec | None = None
    liveness: ProbeSpec | None = None


def validate_probes(value: dict | None) -> dict | None:
    """Normalise an API payload: validate, drop unset fields, return None when empty.

    Raises ``ValueError`` (pydantic ``ValidationError`` is a subclass) on bad input.
    """
    if value is None:
        return None
    spec = ProbesSpec.model_validate(value)
    out = spec.model_dump(exclude_none=True)
    # {"liveness": {}} still means "liveness on, defaults" — keep the empty dict.
    if spec.liveness is not None:
        out["liveness"] = spec.liveness.model_dump(exclude_none=True)
    return out or None


def effective_probes(probes: dict | None) -> dict:
    """``{"readiness": {...full...}, "liveness": {...full...} | None}`` after defaults."""
    probes = probes or {}
    readiness = {**READINESS_DEFAULTS, **(probes.get("readiness") or {})}
    liveness_in = probes.get("liveness")
    liveness = None if liveness_in is None else {**LIVENESS_DEFAULTS, **liveness_in}
    return {"readiness": readiness, "liveness": liveness}


def _k8s_probe(spec: dict) -> dict:
    return {
        "httpGet": {"path": spec["path"], "port": SERVING_PORT},
        "initialDelaySeconds": spec["initial_delay_seconds"],
        "periodSeconds": spec["period_seconds"],
        "timeoutSeconds": spec["timeout_seconds"],
        "failureThreshold": spec["failure_threshold"],
    }


def render_probes(dep) -> dict:
    """Container-level probe fields for a Deployment manifest.

    Always a ``readinessProbe``; a ``livenessProbe`` only when the row opts in.
    Rows built without the column (benchmark clones, mocks) read as None.
    """
    eff = effective_probes(getattr(dep, "probes", None))
    out = {"readinessProbe": _k8s_probe(eff["readiness"])}
    if eff["liveness"] is not None:
        out["livenessProbe"] = _k8s_probe(eff["liveness"])
    return out


def probe_from_k8s(probe: dict | None) -> tuple[dict | None, str | None]:
    """Reverse of ``_k8s_probe`` for the recipe importer.

    Returns ``(spec, warning_code)``. Non-HTTP probes cannot be expressed and
    yield ``(None, "probe_unsupported")``; a probe on another port is kept but
    flagged, since the portal always probes the serving port.
    """
    if not probe:
        return None, None
    http = probe.get("httpGet")
    if not http:
        return None, "probe_unsupported"
    spec = {"path": http.get("path") or "/health"}
    for k8s_key, key in (
        ("initialDelaySeconds", "initial_delay_seconds"),
        ("periodSeconds", "period_seconds"),
        ("timeoutSeconds", "timeout_seconds"),
        ("failureThreshold", "failure_threshold"),
    ):
        if probe.get(k8s_key) is not None:
            spec[key] = int(probe[k8s_key])
    port = http.get("port")
    warning = "probe_port_changed" if port not in (None, SERVING_PORT, str(SERVING_PORT), "http") else None
    return spec, warning
