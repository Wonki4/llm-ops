import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "litellm_session";

export function middleware(req: NextRequest) {
  const hasSession = req.cookies.has(SESSION_COOKIE);
  const isLoginPage = req.nextUrl.pathname === "/login";
  const isApi = req.nextUrl.pathname.startsWith("/api/");

  if (isApi) return NextResponse.next();
  if (isLoginPage && hasSession) {
    return NextResponse.redirect(new URL("/teams", req.url));
  }
  if (!isLoginPage && !hasSession) {
    const returnTo = encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(new URL(`/api/proxy/auth/login?return_to=${returnTo}`, req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/).*)"],
};
