import type { ModelWithCatalog, PersonalAccess } from "@/types";
import {
  ALL_PROXY_MODELS,
  NO_DEFAULT_MODELS,
  type AccessGroupIndex,
  type ExpandedGrant,
  expandModelGrants,
} from "@/lib/access-groups";

export { ALL_PROXY_MODELS, NO_DEFAULT_MODELS, grantEntries } from "@/lib/access-groups";

/** Mirrors backend personal_access.grant_state. */
export function grantState(models: string[] | null | undefined): PersonalAccess {
  const names = (models ?? []).filter(Boolean);
  if (names.length === 0 || names.every((m) => m === NO_DEFAULT_MODELS)) return "none";
  if (names.includes(ALL_PROXY_MODELS)) return "all";
  return "custom";
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
  return expandModelGrants(models ?? [], index, modelsByName);
}
