# Apply prefix_affinity to a litellm-helm release

Enables the native `prefix_affinity` router (see `../prefix_affinity_check.py`) on the
**official litellm-helm chart** — no fork, no custom image, survives chart/image upgrades.

## One command
```bash
NAMESPACE=litellm RELEASE=litellm \
CHART=oci://ghcr.io/berriai/litellm-helm \
VALUES=path/to/your-existing-values.yaml \
./apply.sh
```
`apply.sh` (re)creates the `prefix-affinity-plugin` ConfigMap from the single source file,
then runs `helm upgrade` with your values + `values-prefix-affinity.yaml`.

## Or manually
```bash
# 1) plugin -> ConfigMap
kubectl -n <ns> create configmap prefix-affinity-plugin \
  --from-file=prefix_affinity_check.py=../prefix_affinity_check.py \
  --dry-run=client -o yaml | kubectl -n <ns> apply -f -

# 2) upgrade with the overlay (after your own values)
helm upgrade <release> <chart> -n <ns> -f <your-values>.yaml -f values-prefix-affinity.yaml
```

## What the overlay sets (chart-native values)
- `volumes` / `volumeMounts`: mount the ConfigMap at `/extra-callbacks`.
- `envVars`: `PYTHONPATH=/extra-callbacks` (config lives at `/etc/litellm`, so the plugin dir
  must be on the path) + `PREFIX_AFFINITY_*`.
- `proxy_config.litellm_settings.callbacks` + `proxy_config.router_settings.enable_pre_call_checks: true`.

## Caveats
- **helm replaces lists.** If your `proxy_config.litellm_settings.callbacks` already has entries,
  merge `prefix_affinity_check.prefix_affinity_handler` into that existing list instead of relying
  on the overlay (otherwise it overwrites your callbacks).
- Effect requires a `model_group` whose deployments are **different OpenAI orgs/accounts**
  (provider prompt cache is org-scoped).
- Re-run `apply.sh` (or step 1) after editing `prefix_affinity_check.py`, then restart the pods
  (`kubectl -n <ns> rollout restart deploy -l app.kubernetes.io/instance=<release>`).

## Verify
```bash
kubectl -n <ns> logs -l app.kubernetes.io/instance=<release> --tail=200 \
  | grep -i "callback\|prefix_affinity"
```
Then send two requests sharing a >= 1024-token prefix and confirm they hit the same deployment.

## Air-gapped
Instead of the ConfigMap mount, bake the file into your internal-registry image
(`COPY prefix_affinity_check.py /extra-callbacks/`) and keep `PYTHONPATH` + the `proxy_config`
overlay. No other change.
