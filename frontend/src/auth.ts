import NextAuth from "next-auth";
import KeycloakProvider from "next-auth/providers/keycloak";

declare module "next-auth" {
  interface Session {
    accessToken?: string;
    error?: string;
    user: {
      id: string;
      name?: string | null;
      email?: string | null;
      role?: string;
    };
  }
}

declare module "@auth/core/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    expiresAt?: number;
    role?: string;
    error?: string;
  }
}

// KEYCLOAK_ISSUER is the browser-facing URL (http://localhost:8082/realms/litellm)
// KEYCLOAK_ISSUER_INTERNAL is the Docker-internal URL for server-side OIDC calls
const issuer = process.env.KEYCLOAK_ISSUER!;
const issuerInternal = process.env.KEYCLOAK_ISSUER_INTERNAL || issuer;

async function refreshAccessToken(token: import("@auth/core/jwt").JWT) {
  if (!token.refreshToken) {
    console.warn("[Auth] No refresh token available, session expired");
    return { ...token, accessToken: undefined, expiresAt: 0, error: "RefreshTokenMissing" as const };
  }

  try {
    // Keycloak is configured with --hostname-strict=true --hostname=localhost
    // --hostname-port=8082 so all tokens use issuer=http://localhost:8082/...
    // regardless of which URL Keycloak is accessed through. This means we can
    // safely refresh via the Docker-internal URL (keycloak:8080).
    const response = await fetch(`${issuerInternal}/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.KEYCLOAK_CLIENT_ID!,
        client_secret: process.env.KEYCLOAK_CLIENT_SECRET!,
        grant_type: "refresh_token",
        refresh_token: token.refreshToken,
      }),
    });

    if (!response.ok) {
      const errorBody = await response.text().catch(() => "unknown");
      console.error(`[Auth] Token refresh failed (${response.status}): ${errorBody}`);
      // Clear refreshToken on invalid_grant to stop infinite retry loops.
      // The invalid refresh token will never succeed, so don't keep it.
      const isInvalidGrant = errorBody.includes("invalid_grant");
      return {
        ...token,
        accessToken: undefined,
        refreshToken: isInvalidGrant ? undefined : token.refreshToken,
        expiresAt: 0,
        error: "RefreshFailed" as const,
      };
    }

    const refreshed = (await response.json()) as {
      access_token: string;
      refresh_token?: string;
      expires_in: number;
    };

    console.log(`[Auth] Token refreshed successfully, expires in ${refreshed.expires_in}s`);
    return {
      ...token,
      accessToken: refreshed.access_token,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      expiresAt: Math.floor(Date.now() / 1000 + refreshed.expires_in),
      error: undefined,
    };
  } catch (err) {
    console.error("[Auth] Token refresh error:", err instanceof Error ? err.message : err);
    return { ...token, accessToken: undefined, expiresAt: 0, error: "RefreshError" as const };
  }
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    {
      id: "keycloak",
      name: "Keycloak",
      type: "oidc",
      clientId: process.env.KEYCLOAK_CLIENT_ID!,
      clientSecret: process.env.KEYCLOAK_CLIENT_SECRET!,
      // Use the browser-facing issuer so NextAuth matches the token's iss claim.
      // Keycloak is configured with --hostname-strict=true so iss is always localhost:8082.
      issuer,
      // With hostname-strict=true, the wellKnown discovery returns localhost:8082 for all
      // endpoints. NextAuth needs wellKnown for OIDC init, but we override server-side
      // endpoints explicitly so the frontend container can reach them via Docker internal URL.
      wellKnown: `${issuerInternal}/.well-known/openid-configuration`,
      authorization: {
        url: `${issuer}/protocol/openid-connect/auth`,
        params: { scope: "openid email profile" },
      },
      token: `${issuerInternal}/protocol/openid-connect/token`,
      userinfo: `${issuerInternal}/protocol/openid-connect/userinfo`,
      jwks_endpoint: `${issuerInternal}/protocol/openid-connect/certs`,
    },
  ],
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        token.accessToken = account.access_token;
        token.refreshToken = account.refresh_token;
        token.expiresAt = account.expires_at;
        return token;
      }

      // Token still valid (with 30s buffer) — return as-is
      if (token.expiresAt && Math.floor(Date.now() / 1000) < token.expiresAt - 30) {
        return token;
      }

      // If we already failed to refresh and have no refresh token, don't retry.
      // This prevents infinite loops when the refresh token is expired/revoked.
      if (token.error === "RefreshFailed" && !token.refreshToken) {
        return token;
      }

      return await refreshAccessToken(token);
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string;
      if (token.error) session.error = token.error;
      if (token.sub) session.user.id = token.sub;
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
});
