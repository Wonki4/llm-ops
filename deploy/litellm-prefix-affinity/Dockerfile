# Thin overlay: official LiteLLM v1.89.0 + the prefix_affinity Router filter.
# The patch branch is based on vanilla v1.89.0, so only 3 files differ from the
# official image; overlaying them is byte-correct (no full rebuild needed).
# Build context is the .stage/ dir produced by build.sh (3 flat files).
ARG LITELLM_BASE=ghcr.io/berriai/litellm:v1.89.0
FROM ${LITELLM_BASE}

USER root
COPY router.py types_router.py prefix_affinity_check.py /tmp/patch/
RUN set -eux; \
    D="$(python -c 'import litellm, os; print(os.path.dirname(litellm.__file__))')"; \
    cp /tmp/patch/router.py                "$D/router.py"; \
    cp /tmp/patch/types_router.py          "$D/types/router.py"; \
    cp /tmp/patch/prefix_affinity_check.py "$D/router_utils/pre_call_checks/prefix_affinity_check.py"; \
    rm -rf /tmp/patch; \
    python -c "import litellm; from litellm.router_utils.pre_call_checks.prefix_affinity_check import PrefixAffinityDeploymentCheck; assert 'prefix_affinity_config' in litellm.Router.get_valid_args(); print('PREFIX_AFFINITY PATCH OK')"
