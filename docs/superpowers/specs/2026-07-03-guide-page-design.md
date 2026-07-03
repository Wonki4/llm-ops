# Guide page (user onboarding) — Design

**Date:** 2026-07-03
**Status:** Approved (brainstorming)

## Goal

A static in-portal onboarding guide that walks a new user from joining a team to
tracking their spend, without asking an admin. Single page, no backend changes.

## Decisions (from brainstorming)

- **Audience/content:** user onboarding only — no caching deep-dive, no admin docs, no FAQ.
- **Content management:** static page (TSX + existing en/ko i18n). Updates ship with deploys.
- **Sections:** the core 4-step flow.

## Route & navigation

- `frontend/src/app/(app)/guide/page.tsx` — behind login, visible to all roles.
- Sidebar: new top entry (above announcements) — `{ key: "guide", href: "/guide", icon: BookOpen }`,
  roles `["user", "team_admin", "super_user"]`. Label via existing `sidebar` i18n namespace.

## Page structure

Single long-form page: title + subtitle, a 1→4 anchor step nav, then four section cards
(`id="step-N"`, `scroll-mt` for anchor offset):

1. **Join a team & issue an API key** — usage is team-scoped; find a team in discover,
   request to join, then create a key. Deep links: `/teams/discover`, `/keys/new`.
   Note: the key value is shown once at creation.
2. **First API call** — the gateway is OpenAI-compatible; only the base URL changes.
   Two copyable code blocks: `curl` and Python OpenAI SDK, using placeholders
   `https://<gateway-host>/v1` and `<model-name>` (static page ⇒ no env injection).
3. **Model catalog** — pick a model from `/models/calendar` (availability) and
   `/models/dashboard` (pricing incl. cache-read, specs); the catalog model name is the
   API `model` parameter.
4. **Track usage & cost** — team page Usage tab; two-line explanation of the
   `Input (cache rd)` / `Output` columns (bigger parenthetical = cheaper input).

## Components

- `frontend/src/components/code-block.tsx` — `<CodeBlock code={...} />`: bordered `<pre>`
  with a copy button (Copy→Check icon swap, `navigator.clipboard`). No syntax highlighting.
- i18n: new `guide` namespace in `messages/{en,ko}.json` (labels, bodies, notes — code
  strings themselves are not localized) + `sidebar.guide` key. en/ko parity.

## Out of scope (YAGNI)

Screenshots/images, admin-editable content, FAQ/troubleshooting, search,
environment-specific URL injection.

## Verification

`tsc --noEmit` clean; en/ko i18n key parity script; manual visual check when the stack
is next brought up.
