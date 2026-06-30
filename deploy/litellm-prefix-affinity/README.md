# Apply the prefix_affinity router to the running LiteLLM proxy (v1.89.0)

The running proxy uses the official image `ghcr.io/berriai/litellm:v1.89.0`, which does
not contain our `prefix_affinity` filter. Applying it has two parts: ship the code as a
custom image, and enable it in the proxy config.

Patch source: `litellm/` submodule, branch `feat/prefix-affinity-router-v1.89.0`
(fork `Wonki4/litellm`), which is vanilla v1.89.0 + 4 commits. 17/17 unit tests pass.

## 1. Build the image (where docker works)

```bash
deploy/litellm-prefix-affinity/build.sh
# -> llmops/litellm:v1.89.0-prefix-affinity ; build prints "PREFIX_AFFINITY PATCH OK"
```

The build overlays only 3 changed files onto the official v1.89.0 image (no full rebuild),
and the final `RUN` step asserts the patch imports and that `prefix_affinity_config` is a
valid router arg.

## 2. Point compose at the custom image

`docker-compose.yml`, the `litellm` service:

```diff
-    image: ghcr.io/berriai/litellm:v1.89.0
+    image: llmops/litellm:v1.89.0-prefix-affinity
```

## 3. Enable it in the proxy config

In `litellm/proxy_server_config.yaml`, under the existing `router_settings:` block, add:

```yaml
  optional_pre_call_checks: ["prefix_affinity"]   # do NOT also enable "prompt_caching"
  prefix_affinity_config:
    prefix_strategy: "leading_slice"   # OpenAI/automatic caching (use "cache_control" for Anthropic)
    leading_slice_messages: 2          # size so the slice covers your stable shared prefix
    min_prefix_tokens: 1024            # OpenAI caches >= 1024 tokens
    ttl_seconds: 600                   # align to provider cache window
```

`enable_pre_call_checks: true` and Redis are already set, so the affinity cache is shared
across proxy replicas.

## 4. Restart and verify

```bash
docker compose up -d litellm
docker logs litellm_proxy 2>&1 | grep -i "prefix_affinity\|not a valid router_settings"   # expect NO "not a valid" warning
```

Functional check: a `model_group` must contain deployments across **different OpenAI
orgs/accounts** (provider prompt cache is org-scoped; multiple keys in one org share a cache
and gain nothing). Send two requests sharing a >= 1024-token prefix and confirm they land on
the same deployment; the affinity entries appear in Redis as `deployment:*:prefix_affinity`.

## Known issue: proxy config lives inside the submodule

`docker-compose.yml` mounts `./litellm/proxy_server_config.yaml`, which is tracked by the
`litellm/` fork submodule. A `git checkout` of a different litellm version inside the
submodule **overwrites this deployment config**. Recommended fix (separate change): move the
proxy config to the parent repo (e.g. `deploy/litellm/proxy_server_config.yaml`) and update
the compose mount, so version bumps never touch deployment config.
