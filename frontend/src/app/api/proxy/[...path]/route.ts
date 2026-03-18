import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function proxyRequest(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  try {
    const { path } = await params;
    const backendPath = `/api/${path.join("/")}`;
    const url = new URL(backendPath, BACKEND_URL);

    req.nextUrl.searchParams.forEach((value, key) => {
      url.searchParams.set(key, value);
    });

    const headers: Record<string, string> = {
      "Content-Type": req.headers.get("Content-Type") || "application/json",
    };

    const cookieHeader = req.headers.get("cookie");
    if (cookieHeader) {
      headers["cookie"] = cookieHeader;
    }

    const init: RequestInit = {
      method: req.method,
      headers,
      redirect: "manual",
    };

    if (req.method !== "GET" && req.method !== "HEAD") {
      const body = await req.text();
      if (body) {
        init.body = body;
      }
    }

    const response = await fetch(url.toString(), init);

    const responseHeaders = new Headers();
    const contentType = response.headers.get("Content-Type");
    if (contentType) responseHeaders.set("Content-Type", contentType);

    // Forward set-cookie headers (getSetCookie may not work in all Node.js builds)
    const rawSetCookie = response.headers.get("set-cookie");
    if (rawSetCookie) {
      // Split multiple cookies: comma-separated but not commas inside values like expires dates
      // Pattern: split on comma followed by a cookie-name= pattern
      const cookies = rawSetCookie.split(/,\s*(?=[A-Za-z_][A-Za-z0-9_]*=)/);
      for (const cookie of cookies) {
        responseHeaders.append("set-cookie", cookie.trim());
      }
    }

    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (location) responseHeaders.set("location", location);
      return new NextResponse(null, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    const responseBody = await response.arrayBuffer();
    return new NextResponse(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("[API Proxy] Error:", message);
    return NextResponse.json(
      { detail: "Proxy error", message },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const DELETE = proxyRequest;
export const PATCH = proxyRequest;
