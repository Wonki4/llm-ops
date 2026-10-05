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
    startup: ProbeSpec | None = None


# P/D servers load weights for minutes: the llm-d guide probes /v1/models every
# 30 s for up to 60 min before giving up. Used as the default startup probe for
# P/D pools; aggregated servings get no startup probe unless the recipe sets one.
STARTUP_DEFAULTS_PD: dict = {
    "path": "/health",
    "initial_delay_seconds": 15,
    "period_seconds": 30,
    "timeout_seconds": 5,
    "failure_threshold": 120,
}


def validate_probes(value: dict | None) -> dict | None:
    """Normalise an API payload: validate, drop unset fields, return None when empty.

    Raises ``ValueError`` (pydantic ``ValidationError`` is a subclass) on bad input.
    """
    if value is None:
        return None
    spec = ProbesSpec.model_validate(value)
    out = spec.model_dump(exclude_none=True)
    # {"liveness": {}} / {"startup": {}} still mean "on, defaults" — keep the empty dict.
    if spec.liveness is not None:
        out["liveness"] = spec.liveness.model_dump(exclude_none=True)
    if spec.startup is not None:
        out["startup"] = spec.startup.model_dump(exclude_none=True)
    return out or None


def effective_probes(probes: dict | None, *, startup_default: dict | None = None) -> dict:
    """``{"readiness": {...full...}, "liveness": {...} | None, "startup": {...} | None}`` after defaults.

    ``startup_default`` (e.g. ``STARTUP_DEFAULTS_PD``) turns the startup probe on
    when the recipe did not set one; a recipe's own ``startup`` block merges on top.
    """
    probes = probes or {}
    readiness = {**READINESS_DEFAULTS, **(probes.get("readiness") or {})}
    liveness_in = probes.get("liveness")
    liveness = None if liveness_in is None else {**LIVENESS_DEFAULTS, **liveness_in}
    startup_in = probes.get("startup")
    if startup_in is None and startup_default is None:
        startup = None
    else:
        startup = {**(startup_default or STARTUP_DEFAULTS_PD), **(startup_in or {})}
    return {"readiness": readiness, "liveness": liveness, "startup": startup}


def _k8s_probe(spec: dict, port: int = SERVING_PORT) -> dict:
    return {
        "httpGet": {"path": spec["path"], "port": port},
        "initialDelaySeconds": spec["initial_delay_seconds"],
        "periodSeconds": spec["period_seconds"],
        "timeoutSeconds": spec["timeout_seconds"],
        "failureThreshold": spec["failure_threshold"],
    }


def render_probes(dep, *, port: int = SERVING_PORT, startup_default: dict | None = None) -> dict:
    """Container-level probe fields for a Deployment manifest.

    Always a ``readinessProbe``; ``livenessProbe``/``startupProbe`` only when the
    row opts in (or, for startup, when the caller passes a default). ``port`` is
    the server's listen port (8200 for a P/D decode container). Rows built
    without the column (benchmark clones, mocks) read as None.
    """
    eff = effective_probes(getattr(dep, "probes", None), startup_default=startup_default)
    out = {"readinessProbe": _k8s_probe(eff["readiness"], port)}
    if eff["liveness"] is not None:
        out["livenessProbe"] = _k8s_probe(eff["liveness"], port)
    if eff["startup"] is not None:
        out["startupProbe"] = _k8s_probe(eff["startup"], port)
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
