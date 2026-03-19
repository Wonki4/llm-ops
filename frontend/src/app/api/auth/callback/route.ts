import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const searchParams = req.nextUrl.searchParams.toString();
  const backendUrl = `${BACKEND_URL}/api/auth/callback?${searchParams}`;

  const cookieHeader = req.headers.get("cookie") || "";

  const response = await fetch(backendUrl, {
    headers: { cookie: cookieHeader },
  });

  const host = req.headers.get("host") || "localhost:3002";
  const proto = req.headers.get("x-forwarded-proto") || "http";
  const origin = `${proto}://${host}`;

  if (!response.ok) {
    const error = await response.text().catch(() => "Token exchange failed");
    console.error("[Auth Callback] Backend error:", response.status, error);
    return NextResponse.redirect(new URL("/login", origin));
  }

  const data = (await response.json()) as {
    session_value: string;
    redirect_to: string;
    cookie_name: string;
    max_age: number;
  };

  const redirectUrl = new URL(data.redirect_to, origin).toString();
  const html = `<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=${redirectUrl}"></head><body>Redirecting...</body></html>`;

  const isSecure = process.env.COOKIE_SECURE === "true";
  const cookieValue = [
    `${data.cookie_name}=${data.session_value}`,
    `Path=/`,
    `Max-Age=${data.max_age}`,
    `HttpOnly`,
    `SameSite=Lax`,
    isSecure ? `Secure` : "",
  ].filter(Boolean).join("; ");

  const headers = new Headers();
  headers.set("Content-Type", "text/html");
  headers.append("Set-Cookie", cookieValue);
  headers.append("Set-Cookie", "_oauth_temp=; Path=/; Max-Age=0");

  return new Response(html, { status: 200, headers });
}
