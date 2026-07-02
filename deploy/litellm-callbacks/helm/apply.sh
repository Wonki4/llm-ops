#!/usr/bin/env bash
set -euo pipefail
# Apply the prefix_affinity native router to a litellm-helm release.
#
# The plugin ConfigMap is regenerated from the single source file
# (../prefix_affinity_check.py) each run, so it never drifts.
#
# Usage (litellm-helm released DIRECTLY):
#   NAMESPACE=litellm RELEASE=litellm CHART=oci://ghcr.io/berriai/litellm-helm \
#   VALUES=path/to/your-values.yaml ./apply.sh
#
# Usage (litellm-helm as a SUBCHART of deploy/helm/litellm-platform):
#   NAMESPACE=litellm RELEASE=platform CHART=deploy/helm/litellm-platform \
#   OVERLAY=values-prefix-affinity-platform.yaml VALUES=... ./apply.sh
#
#   Helm only passes values to a subchart from the block named after it, so the
#   platform chart needs the `litellm-helm:`-nested overlay — the top-level one
#   silently does nothing there (no mount, no PYTHONPATH -> ModuleNotFoundError).
#
#   CHART may also be a local chart path or a repo chart (e.g. litellm/litellm-helm).
#   VALUES is optional but recommended (your existing release values).
#   OVERLAY picks the overlay file in this directory (default: standalone chart).

HERE="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$HERE/../prefix_affinity_check.py"

NAMESPACE="${NAMESPACE:?set NAMESPACE}"
RELEASE="${RELEASE:?set RELEASE}"
CHART="${CHART:?set CHART (e.g. oci://ghcr.io/berriai/litellm-helm or a local path)}"
OVERLAY="${OVERLAY:-values-prefix-affinity.yaml}"
[ -f "$HERE/$OVERLAY" ] || { echo "overlay not found: $HERE/$OVERLAY" >&2; exit 1; }

[ -f "$PLUGIN" ] || { echo "plugin not found: $PLUGIN" >&2; exit 1; }

# 1) plugin -> ConfigMap (idempotent)
kubectl -n "$NAMESPACE" create configmap prefix-affinity-plugin \
  --from-file=prefix_affinity_check.py="$PLUGIN" \
  --dry-run=client -o yaml | kubectl -n "$NAMESPACE" apply -f -

# 2) helm upgrade with your values + the overlay (overlay last so it merges on top)
extra=()
[ -n "${VALUES:-}" ] && extra+=(-f "$VALUES")
helm upgrade "$RELEASE" "$CHART" -n "$NAMESPACE" \
  "${extra[@]}" -f "$HERE/$OVERLAY"

cat <<EOF

Applied. Verify:
  kubectl -n $NAMESPACE logs -l app.kubernetes.io/instance=$RELEASE --tail=200 | grep -i "callback\\|prefix_affinity"
  # expect the callback to load and NO "not a valid router_settings parameter" warning
EOF
