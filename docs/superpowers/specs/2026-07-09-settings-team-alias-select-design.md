# Settings Teams Tab — Alias Display + Team Selects — Design

**Date:** 2026-07-09
**Status:** Approved (user: "좋다")

## Problem

The settings > Teams tab handles teams as raw IDs in three places: the
hidden-teams (exclusion) card shows/accepts bare team IDs, the
prefix-rule card takes comma-separated IDs and renders ID badges, and the
default-team field is a comma-separated ID text input. Admins can't tell
which team is which.

## Decision

Frontend-only. Use the existing `useDiscoverTeams()` (`/api/teams/discover`,
returns all teams with `team_alias`) to (a) render aliases everywhere and
(b) replace ID typing with team selects. Stored formats are unchanged —
IDs (and the comma-joined `default_team_id` string) exactly as today.

- **Label rule:** `alias (first-8-chars-of-id…)`; an ID with no matching
  team (deleted) renders as the raw ID and stays removable.
- **Default team:** the comma string is parsed into a badge list +
  select-to-add (`TeamAddSelect`); save re-joins with `,`. Unknown IDs in
  the existing value survive the round-trip.
- **Prefix rules:** the comma input becomes select-to-add with staged
  badges; the Add-rule button requires prefix + ≥1 staged team. Existing
  rule rows render alias badges.
- **Hidden teams:** the ID input + Add button become a single
  select-to-hide (mutation fires on selection; options exclude already
  hidden teams). Hidden badges render aliases.
- Two shared local components in `settings/page.tsx`: `TeamAddSelect`
  (select with placeholder option, excludes given IDs) and `TeamBadge`
  (badge with remove button).

## i18n (settings namespace, en/ko)

- Add: `teamSelectPlaceholder`, `hiddenTeamSelectPlaceholder`.
- Update: `defaultTeamHelp` (drop the "commas" sentence — multi-team is now
  visual).
- Delete (render sites gone): `defaultTeamPlaceholder`, `teamIdPlaceholder`,
  `hiddenTeamPlaceholder`.

## Non-goals

- No backend/API/storage change; no validation of stale IDs (they render
  raw and can be removed).
- No search/combobox — native select, matching the codebase's existing
  form controls.

## Verification

- `npm run lint` 0 NEW (baseline 4 errors/13 warnings); `npm run build`
  passes; i18n key-presence check for both locales.
- Manual: aliases visible in all three cards; unknown ID still removable;
  default-team round-trip preserves order/unknowns.

## v1 implementation notes (post final review)

Final whole-branch review: Ready to merge — 8/8 checks PASS (old-UI payloads
load/re-save identically; deleted-team IDs render raw and stay removable in
all three places; i18n key sweep clean both locales). One Important finding
from task review was fixed in-branch (0f2bf27): the hidden-team
select-to-hide needed the old Add button's pending guard to prevent a
lost-update race. Ship-as-is minors: legacy duplicate IDs in stored data
would remove-by-value together (UI can't create dups); a brief re-enabled
window remains between mutation success and the hidden-teams refetch (stale
exclude list, idempotent resend at worst); the alias map can be briefly
stale after a team rename (cosmetic, TanStack cache).
