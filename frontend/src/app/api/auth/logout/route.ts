import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const cookieHeader = req.headers.get("cookie") || "";

  // Call backend to get the Keycloak logout URL
  const response = await fetch(`${BACKEND_URL}/api/auth/logout`, {
    headers: { cookie: cookieHeader },
    redirect: "manual",
  });

  const keycloakLogoutUrl = response.headers.get("location") || "/login";

  // Clear the session cookie and redirect to Keycloak logout
  const headers = new Headers();
  headers.set("Location", keycloakLogoutUrl);
  headers.append("Set-Cookie", "litellm_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax");

  return new Response(null, { status: 302, headers });
}
