/** Portal user ids are upper-cased Keycloak usernames (mirrors backend personal_access.USER_ID_RE). */
const USER_ID_RE = /^[A-Z0-9][A-Z0-9._@-]{0,127}$/;

/** A typed or pasted id as a canonical user id, or null when it cannot be one. */
export function normalizeUserId(raw: string): string | null {
  const value = raw.trim().toUpperCase();
  return USER_ID_RE.test(value) ? value : null;
}

/** A pasted roster split on newlines, commas, semicolons, tabs or spaces. */
export function splitPastedIds(text: string): string[] {
  return text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
}
