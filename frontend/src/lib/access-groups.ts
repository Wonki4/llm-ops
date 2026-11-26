import type { ModelWithCatalog, Team } from "@/types";

export const NO_DEFAULT_MODELS = "no-default-models";
export const ALL_PROXY_MODELS = "all-proxy-models";

/** LiteLLM also grants unrestricted access to legacy teams with a literal empty list. */
export function hasAllTeamModels(team: Pick<Team, "models" | "unified_access_groups">): boolean {
  return team.models.length === 0 || team.models.includes(ALL_PROXY_MODELS)
    || !!team.unified_access_groups?.some((group) => group.models.includes(ALL_PROXY_MODELS));
}

/** The model and access-group names, excluding LiteLLM's scope markers. */
export function grantEntries(models: string[] | null | undefined): string[] {
  return (models ?? []).filter((m) => m && m !== NO_DEFAULT_MODELS && m !== ALL_PROXY_MODELS);
}

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

export function buildAccessGroupIndex(models: ModelWithCatalog[] | undefined, referencedGroups: string[] = []): AccessGroupIndex {
  const index: AccessGroupIndex = new Map(referencedGroups.map((name) => [name, []]));
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
  for (const grant of grantEntries(grants)) {
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

/** Resolve the team grants once for both the overview and the full model list. */
export function teamModelScope(
  team: Pick<Team, "models" | "unified_access_groups">,
  index: AccessGroupIndex,
  modelsByName: Map<string, ModelWithCatalog>,
) {
  const assignedGroups = new Set(grantEntries(team.models).filter((name) => index.has(name) && !modelsByName.has(name)));
  const unrestricted = hasAllTeamModels(team);
  const initial: ExpandedGrant[] = unrestricted
    ? Array.from(modelsByName.values())
        .filter((model) => model.catalog && model.catalog.visible !== false)
        .map((model) => ({ name: model.model_name, model, viaGroup: null }))
    : expandModelGrants(team.models, index, modelsByName);
  const rows = new Map(initial.map((row) => [row.name, { ...row, groups: new Set(accessGroupsOf(row.model)) }]));

  for (const group of team.unified_access_groups ?? []) {
    assignedGroups.add(group.access_group_name);
    const members = group.models.includes(ALL_PROXY_MODELS) ? initial : expandModelGrants(group.models, index, modelsByName);
    for (const member of members) {
      let row = rows.get(member.name);
      if (!row) {
        if (unrestricted) continue;
        row = { ...member, viaGroup: member.viaGroup ?? group.access_group_name, groups: new Set(accessGroupsOf(member.model)) };
        rows.set(member.name, row);
      }
      row.groups.add(group.access_group_name);
    }
  }

  return {
    assignedGroups: Array.from(assignedGroups),
    rows: Array.from(rows.values(), ({ groups, ...row }) => ({ ...row, groups: Array.from(groups) })),
  };
}

export function expandTeamModels(
  team: Pick<Team, "models" | "unified_access_groups">,
  index: AccessGroupIndex,
  modelsByName: Map<string, ModelWithCatalog>,
): ExpandedGrant[] {
  return teamModelScope(team, index, modelsByName).rows;
}
