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
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/).*)"],
};
