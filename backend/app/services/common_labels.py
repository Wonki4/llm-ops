"""Common pod labels: label keys the portal puts on every serving pod, each value
fixed text or filled from the deployment (``{{model_name}}``, ``{{namespace}}``,
``{{gpu_type}}``).

The rules live in portal settings (``serving_pod_labels``: JSON ``{key: value}``).
A deployment keeps a copy of the rules it was last applied with
(``custom_model_deployment.common_labels``), taken on create and on re-apply, so a
settings change reaches running pods only with the next re-apply (a rolling restart)
and the re-apply comparison shows it. The manifest builder renders the copy per pool.
"""

import json
import re

from sqlalchemy import text

SETTINGS_KEY = "serving_pod_labels"
PLACEHOLDERS = ("model_name", "namespace", "gpu_type")
# The portal's own labels (selectors, llm-d's model / role markers).
RESERVED_PREFIXES = ("llm-ops/", "llm-d.ai/")
MAX_VALUE = 63

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
_KEY_NAME = re.compile(r"[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?")
_KEY_PREFIX = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")
_VALUE_TEXT = re.compile(r"[A-Za-z0-9._-]*")
_NOT_VALUE_CHAR = re.compile(r"[^A-Za-z0-9._-]")
_EDGE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


def _check_key(key: str) -> None:
    if key.startswith(RESERVED_PREFIXES):
        raise ValueError(f"pod label {key!r} is managed by the portal")
    prefix, _, name = key.rpartition("/")
    ok = len(name) <= 63 and _KEY_NAME.fullmatch(name) is not None
    if "/" in key:
        ok = ok and len(prefix) <= 253 and _KEY_PREFIX.fullmatch(prefix) is not None
    if not ok:
        raise ValueError(
            f"{key!r} is not a valid label key: [prefix/]name, the name up to 63 letters, digits, '-', '_' or '.' "
            "starting and ending with a letter or digit, the prefix a lowercase DNS name"
        )


def _check_value(key: str, value: str) -> None:
    for name in _PLACEHOLDER.findall(value):
        if name not in PLACEHOLDERS:
            known = ", ".join(f"{{{{{p}}}}}" for p in PLACEHOLDERS)
            raise ValueError(f"{key}: unknown placeholder {{{{{name}}}}}; use {known}")
    fixed = _PLACEHOLDER.sub("", value)
    if len(fixed) > MAX_VALUE or not _VALUE_TEXT.fullmatch(fixed):
        raise ValueError(
            f"{key}: {value!r} is not a valid label value: up to {MAX_VALUE} letters, digits, '-', '_' or '.' "
            "besides the placeholders"
        )


def validate_rules(rules: dict[str, str]) -> dict[str, str]:
    """``rules`` checked for saving: keys K8s accepts and the portal does not own,
    values of label-value characters and known placeholders. ValueError otherwise."""
    for key, value in rules.items():
        _check_key(key)
        _check_value(key, value)
    return dict(rules)


def render(rules: dict | None, *, model_name: str, namespace: str, gpu_type: str | None) -> dict[str, str]:
    """The labels ``rules`` give one pool: placeholders filled, then each value cut to a
    valid label value (other characters → '-', at most 63, letters or digits at both ends).
    A placeholder with nothing to fill (no GPU type) is empty; the key stays."""
    context = {"model_name": model_name, "namespace": namespace, "gpu_type": gpu_type or ""}
    out = {}
    for key, value in (rules or {}).items():
        filled = _PLACEHOLDER.sub(lambda m: context.get(m.group(1), ""), str(value))
        out[key] = _EDGE.sub("", _NOT_VALUE_CHAR.sub("-", filled)[:MAX_VALUE])
    return out


async def load_rules(db) -> dict[str, str]:
    """The rules in portal settings now ({} when none are set)."""
    result = await db.execute(text("SELECT value FROM custom_portal_settings WHERE key = :key"), {"key": SETTINGS_KEY})
    raw = result.scalar_one_or_none()
    return dict(json.loads(raw)) if raw else {}
