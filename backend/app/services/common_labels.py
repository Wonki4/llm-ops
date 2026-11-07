"""Common pod labels and annotations: keys the portal puts on every serving pod, each
value fixed text or filled from the deployment (``{{model_name}}``, ``{{namespace}}``,
``{{gpu_type}}``, ``{{metrics_port}}``).

The rules live in portal settings (``serving_pod_labels`` / ``serving_pod_annotations``:
JSON ``{key: value}``). A deployment keeps a copy of the rules it was last applied with
(``custom_model_deployment.common_labels`` / ``common_annotations``), taken on create and
on re-apply, so a settings change reaches running pods only with the next re-apply (a
rolling restart) and the re-apply comparison shows it. The manifest builder renders the
copy per pool.
"""

import json
import re

from sqlalchemy import text

LABELS_KEY = "serving_pod_labels"
ANNOTATIONS_KEY = "serving_pod_annotations"
PLACEHOLDERS = ("model_name", "namespace", "gpu_type", "metrics_port")
# The portal's own labels (selectors, llm-d's model / role markers).
RESERVED_PREFIXES = ("llm-ops/", "llm-d.ai/")
# …and for annotations also what the Deployment controller writes (revision, …).
RESERVED_ANNOTATION_PREFIXES = (*RESERVED_PREFIXES, "deployment.kubernetes.io/")
MAX_VALUE = 63

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
_KEY_NAME = re.compile(r"[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?")
_KEY_PREFIX = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")
_VALUE_TEXT = re.compile(r"[A-Za-z0-9._-]*")
_NOT_VALUE_CHAR = re.compile(r"[^A-Za-z0-9._-]")
_EDGE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


_RESERVED = {
    "label": (RESERVED_PREFIXES, "the portal"),
    "annotation": (RESERVED_ANNOTATION_PREFIXES, "the portal or Kubernetes"),
}


def _check_key(key: str, kind: str) -> None:
    """Label and annotation keys share K8s's [prefix/]name syntax."""
    reserved, owner = _RESERVED[kind]
    if key.startswith(reserved):
        raise ValueError(f"pod {kind} {key!r} is managed by {owner}")
    prefix, _, name = key.rpartition("/")
    ok = len(name) <= 63 and _KEY_NAME.fullmatch(name) is not None
    if "/" in key:
        ok = ok and len(prefix) <= 253 and _KEY_PREFIX.fullmatch(prefix) is not None
    if not ok:
        raise ValueError(
            f"{key!r} is not a valid {kind} key: [prefix/]name, the name up to 63 letters, digits, '-', '_' or '.' "
            "starting and ending with a letter or digit, the prefix a lowercase DNS name"
        )


def _check_placeholders(key: str, value: str) -> None:
    for name in _PLACEHOLDER.findall(value):
        if name not in PLACEHOLDERS:
            known = ", ".join(f"{{{{{p}}}}}" for p in PLACEHOLDERS)
            raise ValueError(f"{key}: unknown placeholder {{{{{name}}}}}; use {known}")


def _check_value(key: str, value: str) -> None:
    _check_placeholders(key, value)
    fixed = _PLACEHOLDER.sub("", value)
    if len(fixed) > MAX_VALUE or not _VALUE_TEXT.fullmatch(fixed):
        raise ValueError(
            f"{key}: {value!r} is not a valid label value: up to {MAX_VALUE} letters, digits, '-', '_' or '.' "
            "besides the placeholders"
        )


def validate_rules(rules: dict[str, str]) -> dict[str, str]:
    """Label ``rules`` checked for saving: keys K8s accepts and the portal does not own,
    values of label-value characters and known placeholders. ValueError otherwise."""
    for key, value in rules.items():
        _check_key(key, "label")
        _check_value(key, value)
    return dict(rules)


def validate_annotation_rules(rules: dict[str, str]) -> dict[str, str]:
    """Annotation ``rules`` checked for saving: keys as for labels (also none the
    Deployment controller writes), values any text with known placeholders."""
    for key, value in rules.items():
        _check_key(key, "annotation")
        _check_placeholders(key, value)
    return dict(rules)


def _fill(
    rules: dict | None, model_name: str, namespace: str, gpu_type: str | None, metrics_port: int
) -> dict[str, str]:
    context = {
        "model_name": model_name,
        "namespace": namespace,
        "gpu_type": gpu_type or "",
        "metrics_port": str(metrics_port),
    }
    return {
        key: _PLACEHOLDER.sub(lambda m: context.get(m.group(1), ""), str(value)) for key, value in (rules or {}).items()
    }


def render(
    rules: dict | None, *, model_name: str, namespace: str, gpu_type: str | None, metrics_port: int
) -> dict[str, str]:
    """The labels ``rules`` give one pool: placeholders filled, then each value cut to a
    valid label value (other characters → '-', at most 63, letters or digits at both ends).
    A placeholder with nothing to fill (no GPU type) is empty; the key stays."""
    filled = _fill(rules, model_name, namespace, gpu_type, metrics_port)
    return {key: _EDGE.sub("", _NOT_VALUE_CHAR.sub("-", value)[:MAX_VALUE]) for key, value in filled.items()}


def render_annotations(
    rules: dict | None, *, model_name: str, namespace: str, gpu_type: str | None, metrics_port: int
) -> dict[str, str]:
    """The annotations ``rules`` give one pool: placeholders filled, the text kept as is."""
    return _fill(rules, model_name, namespace, gpu_type, metrics_port)


async def _load(db, key: str) -> dict[str, str]:
    result = await db.execute(text("SELECT value FROM custom_portal_settings WHERE key = :key"), {"key": key})
    raw = result.scalar_one_or_none()
    return dict(json.loads(raw)) if raw else {}


async def load_rules(db) -> dict[str, dict[str, str]]:
    """The rules in portal settings now: ``{"labels": {...}, "annotations": {...}}``
    ({} for a kind that is not set)."""
    return {"labels": await _load(db, LABELS_KEY), "annotations": await _load(db, ANNOTATIONS_KEY)}


async def load_deployment_copy(db) -> dict[str, dict | None]:
    """The copy of the rules in settings now a deployment keeps, as its columns
    (``common_labels`` / ``common_annotations``; None when there are none)."""
    rules = await load_rules(db)
    return {"common_labels": rules["labels"] or None, "common_annotations": rules["annotations"] or None}
