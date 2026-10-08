import type { ModelWithCatalog, PersonalAccess } from "@/types";
import { type AccessGroupIndex, type ExpandedGrant, expandModelGrants } from "@/lib/access-groups";

export const NO_DEFAULT_MODELS = "no-default-models";
export const ALL_PROXY_MODELS = "all-proxy-models";

/** Mirrors backend personal_access.grant_state. */
export function grantState(models: string[] | null | undefined): PersonalAccess {
  const names = (models ?? []).filter(Boolean);
  if (names.length === 0 || names.every((m) => m === NO_DEFAULT_MODELS)) return "none";
  if (names.includes(ALL_PROXY_MODELS)) return "all";
  return "custom";
}

/** The custom entries (model / group names) without sentinels. */
export function grantEntries(models: string[] | null | undefined): string[] {
  return (models ?? []).filter((m) => m && m !== NO_DEFAULT_MODELS && m !== ALL_PROXY_MODELS);
}

/**
 * The concrete models a personal grant reaches, as table rows: `all` → every
 * visible model, `custom` → groups expanded to their members (tagged
 * `viaGroup`), `none` → nothing.
 */
export function expandPersonalScope(
  models: string[] | null | undefined,
  index: AccessGroupIndex,
  modelsByName: Map<string, ModelWithCatalog>,
  allModels: ModelWithCatalog[] | undefined,
): ExpandedGrant[] {
  const state = grantState(models);
  if (state === "none") return [];
  if (state === "all") {
    return (allModels ?? [])
      .filter((m) => m.catalog && m.catalog.visible !== false)
      .map((m) => ({ name: m.model_name, model: m, viaGroup: null }));
  }
  return expandModelGrants(grantEntries(models), index, modelsByName);
}
