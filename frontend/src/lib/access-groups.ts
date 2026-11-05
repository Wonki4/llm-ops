import type { ModelWithCatalog, Team } from "@/types";

/**
 * LiteLLM model access groups: each model may list `model_info.access_groups`;
 * a team or key whose `models` contains a group name can call every model in
 * that group. The portal only reads these (assignment stays in LiteLLM).
 */
export function accessGroupsOf(model: ModelWithCatalog | null | undefined): string[] {
  const raw = model?.litellm_info?.model_info?.access_groups;
  return Array.isArray(raw) ? raw.filter((g): g is string => typeof g === "string" && g.length > 0) : [];
}

/** group name → models in that group (sorted by model name). */
export type AccessGroupIndex = Map<string, ModelWithCatalog[]>;

export function buildAccessGroupIndex(models: ModelWithCatalog[] | undefined): AccessGroupIndex {
  const index: AccessGroupIndex = new Map();
  for (const m of models ?? []) {
    for (const g of accessGroupsOf(m)) {
      const list = index.get(g) ?? [];
      list.push(m);
      index.set(g, list);
    }
  }
  for (const list of index.values()) list.sort((a, b) => a.model_name.localeCompare(b.model_name));
  return index;
}

export type ExpandedGrant = { name: string; model: ModelWithCatalog | null; viaGroup: string | null };

/**
 * Expand a team/key `models` list into concrete models: a group name becomes
 * its member models (tagged with `viaGroup`), a plain model name stays as is.
 * "all-proxy-models" is left to the caller. Duplicates keep the first entry.
 */
export function expandModelGrants(
  grants: string[],
  index: AccessGroupIndex,
  modelsByName: Map<string, ModelWithCatalog>,
): ExpandedGrant[] {
  const out: ExpandedGrant[] = [];
  const seen = new Set<string>();
  for (const grant of grants) {
    if (grant === "all-proxy-models") continue;
    const members = index.get(grant);
    if (members && !modelsByName.has(grant)) {
      for (const m of members) {
        if (seen.has(m.model_name)) continue;
        seen.add(m.model_name);
        out.push({ name: m.model_name, model: m, viaGroup: grant });
      }
    } else {
      if (seen.has(grant)) continue;
      seen.add(grant);
      out.push({ name: grant, model: modelsByName.get(grant) ?? null, viaGroup: null });
    }
  }
  return out;
}

/**
 * A team's models: its own `models` (expanded as above), then the models of
 * its unified access groups (`access_group_ids`), which LiteLLM falls back to
 * when the team's own list denies a model. A unified group may name model
 * access groups too; those expand as well. "All models" is left to the caller.
 */
export function expandTeamModels(
  team: Pick<Team, "models" | "unified_access_groups">,
  index: AccessGroupIndex,
  modelsByName: Map<string, ModelWithCatalog>,
): ExpandedGrant[] {
  const out = expandModelGrants(team.models, index, modelsByName);
  const seen = new Set(out.map((g) => g.name));
  for (const group of team.unified_access_groups ?? []) {
    for (const g of expandModelGrants(group.models, index, modelsByName)) {
      if (seen.has(g.name)) continue;
      seen.add(g.name);
      out.push({ ...g, viaGroup: g.viaGroup ?? group.access_group_name });
    }
  }
  return out;
}
