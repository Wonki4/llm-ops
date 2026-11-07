"""Kubernetes names derived from the portal's full model and stack names."""

import hashlib
import re


_LABEL_TEXT = re.compile(r"[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?")


def fit_name(value: str, limit: int, identity: str) -> str:
    """Keep a fitting name verbatim. Hash the full identity when dropping a suffix."""
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(identity.encode()).hexdigest()[:10]
    prefix = value[: limit - len(digest) - 1].rstrip("-_.")
    return f"{prefix}-{digest}"


def model_stem(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-") or "model"


def model_label(name: str) -> str:
    # Existing selectors can contain uppercase, dots and underscores.
    if _LABEL_TEXT.fullmatch(name):
        return fit_name(name, 63, name)
    # A path such as "org/model" must not select pods for "org-model".
    return f"{model_stem(name)[:52].rstrip('-')}-{hashlib.sha256(name.encode()).hexdigest()[:10]}"


_KIND_SUFFIX = {"deployment": "dep", "service": "svc", "ingress": "ing"}
_ROLE_SUFFIX = {"prefill": "pf", "decode": "dc"}


def resource_name(model: str, kind: str, role: str | None = None) -> str:
    stem = model_stem(model) if _LABEL_TEXT.fullmatch(model) else model_label(model)
    suffix = _KIND_SUFFIX[kind]
    if role is not None:
        suffix = f"{suffix}-{_ROLE_SUFFIX[role]}"
    if kind == "service" and not stem[0].isalpha():
        digest = hashlib.sha256(model.encode()).hexdigest()[:10]
        stem = f"m-{stem}-{digest}"
    return f"{fit_name(stem, 63 - len(suffix) - 1, model)}-{suffix}"
