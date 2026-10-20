"""The llm-d router a P/D serving deploys with: its Helm values and chart.

A recipe either carries the router chart's ``values.yaml`` as written
(``pd_config.router.values_yaml``, used verbatim so a chart upgrade needs no
portal change) or, for rows from before, the structured EPP/scheduler fields
that ``default_llmd_values`` turns into values. Either way the portal fills
``router.modelServers.matchLabels`` only when the values leave it empty; the
EPP image falls back to the portal default at apply time (``build_llmd_values``).
"""

from __future__ import annotations

import yaml

from app.config import settings
from app.services.llmd_manifests import LABEL_LLMD_MODEL, deep_merge, default_llmd_values
from app.services.pd_serving import (
    DEFAULT_PEAK_PREFILL_THROUGHPUT,
    DEFAULT_PREFIX_TOKENS_TO_MATCH,
    parse_router_values,
    pd_config_of,
    serving_name,
)


def _router_cfg(dep) -> dict:
    return dict(pd_config_of(dep).get("router") or {})


def epp_image(router: dict | None = None) -> dict:
    router = router or {}
    return {
        "registry": router.get("epp_registry") or settings.llmd_epp_image_registry,
        "repository": router.get("epp_repository") or settings.llmd_epp_image_repository,
        "tag": router.get("epp_tag") or settings.llmd_epp_image_tag,
    }


def router_values(dep) -> dict:
    """The Helm values the serving's router stack is created with (stored as the stack's helm_values)."""
    router = _router_cfg(dep)
    if router.get("values_yaml"):
        values = parse_router_values(router["values_yaml"], serving_name(dep))
    else:
        image = epp_image(router)
        values = default_llmd_values(
            dep.model_name,
            epp_registry=image["registry"],
            epp_repository=image["repository"],
            epp_tag=image["tag"],
            serving_mode="pd",
            pd_router=router,
        )
    ms = values.setdefault("router", {}).setdefault("modelServers", {})
    if not ms.get("matchLabels"):
        ms["matchLabels"] = {LABEL_LLMD_MODEL: dep.model_name}
    return values


def rendered_router_values(dep) -> dict:
    """What ArgoCD receives: ``router_values`` over the portal's EPP image default
    (the user's values win), mirroring ``build_llmd_values`` for a stack without
    per-stack EPP overrides."""
    return deep_merge({"router": {"epp": {"image": epp_image(_router_cfg(dep))}}}, router_values(dep))


def router_chart(dep) -> tuple[str, str, str]:
    """(repo, name, version) the router stack uses: the recipe's pins, else the portal's."""
    router = _router_cfg(dep)
    return (
        router.get("chart_repo") or settings.llmd_chart_repo,
        router.get("chart_name") or settings.llmd_chart_name,
        router.get("chart_version") or settings.llmd_chart_version,
    )


class _LiteralDumper(yaml.SafeDumper):
    """Multi-line strings (the EPP plugins config) as ``|`` blocks, readable in an editor;
    shared dicts written out rather than aliased."""

    def ignore_aliases(self, data) -> bool:
        return True


def _str(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LiteralDumper.add_representer(str, _str)


def dump_values(values: dict) -> str:
    return yaml.dump(values, Dumper=_LiteralDumper, sort_keys=False, default_flow_style=False)


VALUES_HEADER = """\
# llm-d router chart values, used as written. {{model_name}} = the model name entered at deploy.
# Left out, router.epp.image (portal default) and router.modelServers.matchLabels
# (llm-d.ai/model: <model name>) are filled in by the portal.
"""


def default_values_yaml(router: dict | None = None) -> str:
    """The starter values.yaml for a recipe's router editor: what the portal renders today
    for ``router`` (the recipe's structured fields; none = defaults), minus the parts it
    fills itself — matchLabels always, the EPP image unless the recipe overrides it."""
    router = dict(router or {})
    image = epp_image(router)
    values = default_llmd_values(
        "",
        epp_registry=image["registry"],
        epp_repository=image["repository"],
        epp_tag=image["tag"],
        serving_mode="pd",
        pd_router={
            "peak_prefill_throughput": router.get("peak_prefill_throughput") or DEFAULT_PEAK_PREFILL_THROUGHPUT,
            "prefix_tokens_to_match": router.get("prefix_tokens_to_match") or DEFAULT_PREFIX_TOKENS_TO_MATCH,
            "epp_replicas": router.get("epp_replicas"),
        },
    )
    values["router"]["modelServers"].pop("matchLabels", None)
    if not any(router.get(k) for k in ("epp_registry", "epp_repository", "epp_tag")):
        values["router"]["epp"].pop("image", None)
    return VALUES_HEADER + dump_values(values)
