import { auth } from "@/auth";
import { NextRequest, NextResponse } from "next/server";

// Server-side backend URL (Docker internal or localhost fallback)
const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function proxyRequest(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  try {
    const session = await auth();
    const accessToken = session?.accessToken;

    // If session has a refresh error or no token, return 401 immediately.
    // This prevents forwarding stale/missing tokens to the backend.
    if (!accessToken || session?.error) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { path } = await params;
    const backendPath = `/api/${path.join("/")}`;
    const url = new URL(backendPath, BACKEND_URL);

    // Forward query params
    req.nextUrl.searchParams.forEach((value, key) => {
      url.searchParams.set(key, value);
    });

    const headers: Record<string, string> = {
      "Authorization": `Bearer ${accessToken}`,
      "Content-Type": req.headers.get("Content-Type") || "application/json",
    };

    const init: RequestInit = {
      method: req.method,
      headers,
    };

    // Forward body for non-GET/HEAD requests
    if (req.method !== "GET" && req.method !== "HEAD") {
      const body = await req.text();
      if (body) {
        init.body = body;
      }
    }

    const response = await fetch(url.toString(), init);
    const responseBody = await response.arrayBuffer();

    return new NextResponse(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        "Content-Type": response.headers.get("Content-Type") || "application/json",
      },
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
