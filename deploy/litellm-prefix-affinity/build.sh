#!/usr/bin/env bash
set -euo pipefail
# Build a LiteLLM v1.89.0 proxy image that includes the prefix_affinity Router filter.
#
# Source of truth: the litellm/ fork submodule, branch
#   feat/prefix-affinity-router-v1.89.0  (Wonki4/litellm)
# We extract the 3 patched files from that ref (no checkout needed) and overlay
# them onto the official v1.89.0 image. See Dockerfile.
#
# Usage:   ./build.sh
# Env:     REF (default origin/feat/prefix-affinity-router-v1.89.0), IMAGE, LITELLM_BASE

REF="${REF:-origin/feat/prefix-affinity-router-v1.89.0}"
IMAGE="${IMAGE:-llmops/litellm:v1.89.0-prefix-affinity}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SUB="$REPO/litellm"

stage="$HERE/.stage"
rm -rf "$stage"; mkdir -p "$stage"
git -C "$SUB" show "$REF:litellm/router.py"                                              > "$stage/router.py"
git -C "$SUB" show "$REF:litellm/types/router.py"                                        > "$stage/types_router.py"
git -C "$SUB" show "$REF:litellm/router_utils/pre_call_checks/prefix_affinity_check.py"  > "$stage/prefix_affinity_check.py"

docker build -f "$HERE/Dockerfile" -t "$IMAGE" "$stage"
rm -rf "$stage"
echo "Built $IMAGE  (look for 'PREFIX_AFFINITY PATCH OK' above)"
