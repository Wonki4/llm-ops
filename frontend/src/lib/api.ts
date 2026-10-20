export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthError";
  }
}

/** Flatten FastAPI `detail` (string, 422 validation array, or object) into a readable message. */
function formatDetail(detail: unknown): string {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "string" ? d : formatDetail((d as { msg?: unknown })?.msg ?? d)))
      .map((m) => m.replace(/^Value error, /, ""))
      .filter(Boolean)
      .join("; ");
  }
  if (typeof detail === "object") {
    const m = (detail as { message?: unknown }).message;
    return typeof m === "string" ? m : JSON.stringify(detail);
  }
  return String(detail);
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const proxyPath = path.replace(/^\/api\//, "/api/proxy/");

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  const res = await fetch(proxyPath, { ...options, headers });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      const returnTo = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.replace(`/api/proxy/auth/login?return_to=${returnTo}`);
    }
    throw new AuthError("Session expired. Please log in again.");
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(formatDetail(error.detail) || `API Error ${res.status}`);
  }
  return res.json();
}
