# Settings Teams Tab Alias/Select Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show team aliases (not raw IDs) and replace ID typing with team selects across the settings > Teams tab's three team fields.

**Architecture:** Frontend-only. `useDiscoverTeams()` powers an id→alias map plus two small local components (`TeamAddSelect`, `TeamBadge`) in `settings/page.tsx`. Stored formats unchanged (IDs; `default_team_id` stays a comma-joined string). Spec: `docs/superpowers/specs/2026-07-09-settings-team-alias-select-design.md`.

**Tech Stack:** Next.js App Router, next-intl, TanStack Query.

## Global Constraints

- Outgoing payloads byte-compatible with today: `default_team_id` is the staged IDs joined with `","` (or `undefined` when empty); team-rule `teams` and hidden-teams arrays are raw ID arrays.
- Label rule everywhere: alias present → `${alias} (${id.slice(0, 8)}…)`; alias absent (deleted team) → raw ID, still removable.
- Unknown IDs already stored (default-team string, rules, hidden list) must survive load → display → save round-trips unchanged.
- Native `<select>` styling: `h-9 w-full rounded-md border border-input bg-background px-2 text-sm` (repo convention).
- Frontend gates: `cd frontend && npm run lint` 0 NEW (baseline 4 errors/13 warnings); `npm run build` succeeds; i18n check script prints OK.
- Branch `feat/settings-team-alias-select` (already checked out).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Alias display + team selects in the Teams tab

**Files:**
- Modify: `frontend/src/app/(app)/admin/settings/page.tsx` (imports/state lines 1–105; Teams tab lines 225–408; new components at end of file)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (inside the top-level `"settings"` object — NOT `settings.clusters`)

**Interfaces:**
- Consumes: `useDiscoverTeams()` from `@/hooks/use-api` (returns `DiscoverTeam[]`; each has `team_id: string`, `team_alias: string`).
- Produces: nothing downstream; single task.

- [ ] **Step 1: Baseline lint**

```bash
cd frontend && npm run lint 2>&1 | tail -5
```

Record counts (baseline 4 errors / 13 warnings).

- [ ] **Step 2: i18n keys (both locales)**

In `frontend/messages/en.json`, inside the top-level `"settings"` object:

1. Add (next to `hiddenTeamPlaceholder`'s old position):

```json
"teamSelectPlaceholder": "Select a team to add…",
"hiddenTeamSelectPlaceholder": "Select a team to hide…",
```

2. Replace the value of `"defaultTeamHelp"` with:

```json
"defaultTeamHelp": "Teams that all new users will be automatically added to.",
```

3. Delete the three entries `"defaultTeamPlaceholder"`, `"teamIdPlaceholder"`, `"hiddenTeamPlaceholder"`.

In `frontend/messages/ko.json`, same object:

1. Add:

```json
"teamSelectPlaceholder": "추가할 팀 선택…",
"hiddenTeamSelectPlaceholder": "숨길 팀 선택…",
```

2. Replace `"defaultTeamHelp"` value with:

```json
"defaultTeamHelp": "모든 신규 유저가 자동으로 추가될 팀입니다.",
```

3. Delete the same three entries.

Keep JSON valid; do not touch `settings.clusters` or other namespaces.

- [ ] **Step 3: Imports, data, state in `settings/page.tsx`**

1. Add `useDiscoverTeams` to the existing `@/hooks/use-api` import list (line 8).
2. Inside the component, after the `useCatalogList()` block (line 33), add:

```tsx
  const { data: allTeams } = useDiscoverTeams();
  const teamAlias = new Map((allTeams ?? []).map((tm) => [tm.team_id, tm.team_alias]));
  const teamLabel = (id: string) => {
    const alias = teamAlias.get(id);
    return alias ? `${alias} (${id.slice(0, 8)}…)` : id;
  };
```

3. State swaps:

- `const [defaultTeamId, setDefaultTeamId] = useState("");` →
  `const [defaultTeamIds, setDefaultTeamIds] = useState<string[]>([]);`
- Delete `const [newHiddenTeamId, setNewHiddenTeamId] = useState("");`
- `const [newRuleTeams, setNewRuleTeams] = useState("");` →
  `const [newRuleTeamIds, setNewRuleTeamIds] = useState<string[]>([]);`

4. In the `useEffect` (line 73), replace `setDefaultTeamId(settings.default_team_id || "");` with:

```tsx
      setDefaultTeamIds(
        (settings.default_team_id || "")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      );
```

5. In `handleSave`, replace `default_team_id: defaultTeamId || undefined,` with:

```tsx
        default_team_id: defaultTeamIds.join(",") || undefined,
```

- [ ] **Step 4: Shared local components**

Append at the end of `settings/page.tsx` (after the page component):

```tsx
function TeamAddSelect({
  id,
  teams,
  exclude,
  placeholder,
  onAdd,
}: {
  id?: string;
  teams: { team_id: string; team_alias: string }[];
  exclude: string[];
  placeholder: string;
  onAdd: (teamId: string) => void;
}) {
  return (
    <select
      id={id}
      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
      value=""
      onChange={(e) => {
        if (e.target.value) onAdd(e.target.value);
      }}
    >
      <option value="">{placeholder}</option>
      {teams
        .filter((tm) => !exclude.includes(tm.team_id))
        .map((tm) => (
          <option key={tm.team_id} value={tm.team_id}>
            {tm.team_alias || tm.team_id}
          </option>
        ))}
    </select>
  );
}

function TeamBadge({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <Badge variant="secondary" className="gap-1 pr-1">
      {label}
      <button
        type="button"
        className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
        onClick={onRemove}
      >
        <X className="size-3" />
      </button>
    </Badge>
  );
}
```

- [ ] **Step 5: Base-team block**

Replace the base-team block (lines 237–249, the `{/* Base team */}` div) with:

```tsx
              {/* Base team(s) — stored as one comma-joined default_team_id string */}
              <div className="space-y-2">
                <Label htmlFor="default-team-select">{t("defaultTeamLabel")}</Label>
                {defaultTeamIds.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {defaultTeamIds.map((id) => (
                      <TeamBadge
                        key={id}
                        label={teamLabel(id)}
                        onRemove={() =>
                          setDefaultTeamIds(defaultTeamIds.filter((x) => x !== id))
                        }
                      />
                    ))}
                  </div>
                )}
                <TeamAddSelect
                  id="default-team-select"
                  teams={allTeams ?? []}
                  exclude={defaultTeamIds}
                  placeholder={t("teamSelectPlaceholder")}
                  onAdd={(id) => setDefaultTeamIds([...defaultTeamIds, id])}
                />
                <p className="text-xs text-muted-foreground">
                  {t("defaultTeamHelp")}
                </p>
              </div>
```

- [ ] **Step 6: Prefix-rule block**

Replace the rule add-row `<div className="flex items-center gap-2">…</div>` (lines 258–295) with:

```tsx
                <div className="flex items-start gap-2">
                  <Input
                    placeholder={t("prefixPlaceholder")}
                    value={newRulePrefix}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewRulePrefix(e.target.value)}
                    className="w-32"
                  />
                  <div className="flex-1 space-y-2">
                    <TeamAddSelect
                      teams={allTeams ?? []}
                      exclude={newRuleTeamIds}
                      placeholder={t("teamSelectPlaceholder")}
                      onAdd={(id) => setNewRuleTeamIds([...newRuleTeamIds, id])}
                    />
                    {newRuleTeamIds.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {newRuleTeamIds.map((id) => (
                          <TeamBadge
                            key={id}
                            label={teamLabel(id)}
                            onRemove={() =>
                              setNewRuleTeamIds(newRuleTeamIds.filter((x) => x !== id))
                            }
                          />
                        ))}
                      </div>
                    )}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!newRulePrefix.trim() || newRuleTeamIds.length === 0 || updateTeamRules.isPending}
                    onClick={() => {
                      const updated: DefaultTeamRule[] = [
                        ...(teamRules || []),
                        { prefix: newRulePrefix.trim().toUpperCase(), teams: newRuleTeamIds },
                      ];
                      updateTeamRules.mutate(updated, {
                        onSuccess: () => {
                          toast.success(t("ruleAddSuccess"));
                          setNewRulePrefix("");
                          setNewRuleTeamIds([]);
                        },
                        onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("addFailed")),
                      });
                    }}
                  >
                    <Plus className="size-4" />
                    {t("addButton")}
                  </Button>
                </div>
```

In the existing rule LIST (line 302-303), change the badge content only:

```tsx
                          {rule.teams.map((teamId: string) => (
                            <Badge key={teamId} variant="secondary">{teamLabel(teamId)}</Badge>
                          ))}
```

- [ ] **Step 7: Hidden-teams block**

Replace the add-row `<div className="flex items-center gap-2">…</div>` (lines 340–379, Input + Button) with:

```tsx
              <TeamAddSelect
                teams={allTeams ?? []}
                exclude={hiddenTeams ?? []}
                placeholder={t("hiddenTeamSelectPlaceholder")}
                onAdd={(id) => {
                  const updated = [...(hiddenTeams || []), id];
                  updateHiddenTeams.mutate(updated, {
                    onSuccess: () => toast.success(t("teamHideSuccess")),
                    onError: (err) => toast.error(err instanceof Error ? err.message : t("addFailed")),
                  });
                }}
              />
```

In the hidden-badges map (line 382–384), change `{teamId}` to `{teamLabel(teamId)}` (keep the remove button as-is).

- [ ] **Step 8: Cleanup check**

```bash
grep -n "newHiddenTeamId\|newRuleTeams\b\|defaultTeamId\b\|defaultTeamPlaceholder\|teamIdPlaceholder\|hiddenTeamPlaceholder" "frontend/src/app/(app)/admin/settings/page.tsx" || echo "CLEAN"
```

Expected: CLEAN. Also remove any now-unused lucide imports (e.g. `Plus` is still used by the rule button and the suffix card — verify before removing anything).

- [ ] **Step 9: Gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -10
python3 -c "
import json
for lang in ('en','ko'):
    s = json.load(open(f'frontend/messages/{lang}.json'))['settings']
    assert 'teamSelectPlaceholder' in s and 'hiddenTeamSelectPlaceholder' in s, lang
    assert all(k not in s for k in ('defaultTeamPlaceholder','teamIdPlaceholder','hiddenTeamPlaceholder')), lang
print('i18n OK')
"
```

Expected: lint counts equal Step 1 baseline (0 new), build succeeds, `i18n OK`.

- [ ] **Step 10: Commit**

```bash
git add "frontend/src/app/(app)/admin/settings/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(settings): team aliases + select pickers on the Teams tab

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
