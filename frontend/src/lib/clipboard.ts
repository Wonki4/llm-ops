/**
 * Copy `text` to the clipboard, returning whether it *actually* succeeded.
 *
 * `navigator.clipboard` only exists in a secure context (HTTPS or localhost); on
 * plain HTTP it is `undefined`, so we fall back to a hidden `<textarea>` +
 * `document.execCommand("copy")` (which still works on HTTP). Two things make the
 * fallback reliable — and are why the old inline version failed inside modals on
 * HTTP:
 *   1. The temp textarea is appended INSIDE the open dialog (its `[role=dialog]`
 *      ancestor) when there is one, so the dialog's focus trap doesn't steal
 *      focus back and drop the selection before the copy runs.
 *   2. We return `execCommand`'s boolean result instead of assuming success, so
 *      callers can report an honest success/failure to the user.
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Insecure context or permission denied — fall through to the legacy path.
    }
  }
  return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
  if (typeof document === "undefined") return false;
  const active = document.activeElement as HTMLElement | null;
  const host = (active?.closest("[role='dialog']") as HTMLElement | null) ?? document.body;

  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  // Off-screen (not display:none) so it stays selectable but invisible.
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  ta.style.top = "0";
  host.appendChild(ta);

  let ok = false;
  try {
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}
