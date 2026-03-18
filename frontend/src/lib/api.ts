// All API calls go through the Next.js server-side proxy at /api/proxy/...
// This eliminates CORS issues since browser only talks to same-origin.
// The proxy adds the Keycloak JWT server-side, so no token in browser JS.

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthError";
  }
}

let isSigningOut = false;

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  // Transform /api/foo → /api/proxy/foo
  const proxyPath = path.replace(/^\/api\//, "/api/proxy/");

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  const res = await fetch(proxyPath, { ...options, headers });

  if (res.status === 401) {
    // Session expired or token refresh failed — sign out and force re-login.
    // Use a guard to prevent multiple simultaneous signout attempts.
    if (typeof window !== "undefined" && !isSigningOut) {
      isSigningOut = true;
      // Import signOut dynamically to avoid SSR issues
      const { signOut } = await import("next-auth/react");
      await signOut({ callbackUrl: "/login" });
    }
    throw new AuthError("Session expired. Please log in again.");
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `API Error ${res.status}`);
  }
  return res.json();
}
