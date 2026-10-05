/** Line-oriented editors for K8s placement fields, shared by the recipe form and GPU profiles. */

export const linesToList = (s: string): string[] | null => {
  const v = s.split("\n").map((x) => x.trim()).filter(Boolean);
  return v.length ? v : null;
};
export const listToLines = (v: string[] | null): string => (v ?? []).join("\n");

/** `key=value` per line → map (blank/invalid lines ignored). */
export const linesToMap = (s: string): Record<string, string> | null => {
  const out: Record<string, string> = {};
  for (const line of s.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return Object.keys(out).length ? out : null;
};
export const mapToLines = (m: Record<string, string> | null | undefined): string =>
  Object.entries(m ?? {}).map(([k, v]) => `${k}=${v}`).join("\n");

/** One toleration per line, kubectl taint style: `key=value:Effect`, `key:Effect`, or `key`. */
export type Toleration = { key: string; operator: "Equal" | "Exists"; value?: string; effect?: string };
const TOLERATION_EFFECTS = new Set(["NoSchedule", "PreferNoSchedule", "NoExecute"]);

export const linesToTolerations = (s: string): Toleration[] | null => {
  const out: Toleration[] = [];
  for (const raw of s.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const colon = line.lastIndexOf(":");
    const effect = colon > 0 && TOLERATION_EFFECTS.has(line.slice(colon + 1)) ? line.slice(colon + 1) : undefined;
    const kv = effect ? line.slice(0, colon) : line;
    const eq = kv.indexOf("=");
    const t: Toleration = eq > 0
      ? { key: kv.slice(0, eq).trim(), operator: "Equal", value: kv.slice(eq + 1).trim() }
      : { key: kv.trim(), operator: "Exists" };
    if (effect) t.effect = effect;
    if (t.key) out.push(t);
  }
  return out.length ? out : null;
};

export const tolerationsToLines = (v: unknown[] | null | undefined): string =>
  (v ?? [])
    .map((item) => {
      const t = item as Partial<Toleration>;
      if (!t.key) return "";
      const kv = t.operator === "Exists" || t.value == null ? t.key : `${t.key}=${t.value}`;
      return t.effect ? `${kv}:${t.effect}` : kv;
    })
    .filter(Boolean)
    .join("\n");

/** Mirror of the backend resolver (app/services/gpu_profiles.py) for the deploy-dialog preview. */
export function resolvePlacementPreview(
  profile: { effective_label_key: string; label_value: string; gpu_resource_key: string; tolerations: unknown[] | null } | null,
  explicit: { node_selector: Record<string, string> | null; tolerations: unknown[] | null; gpu_resource_key: string },
): { node_selector: Record<string, string> | null; tolerations: unknown[] | null; gpu_resource_key: string } {
  if (!profile) return explicit;
  const node_selector = { ...(explicit.node_selector ?? {}), [profile.effective_label_key]: profile.label_value };
  const seen = new Set<string>();
  const tolerations: unknown[] = [];
  for (const t of [...(explicit.tolerations ?? []), ...(profile.tolerations ?? [])]) {
    const p = t as Partial<Toleration>;
    const k = `${p.key}|${p.operator}|${p.value ?? ""}|${p.effect ?? ""}`;
    if (seen.has(k)) continue;
    seen.add(k);
    tolerations.push(t);
  }
  return { node_selector, tolerations: tolerations.length ? tolerations : null, gpu_resource_key: profile.gpu_resource_key };
}
