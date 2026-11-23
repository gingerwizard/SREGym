import { NextRequest, NextResponse } from "next/server";
import { COOKIE, expectedToken, timingSafeEqual } from "./lib/auth";

/** Shared-password protection for the whole app, enforced when APP_PASSWORD is set. Browsers are sent to /login,
 *  which sets the auth cookie; tools may instead send HTTP Basic credentials with the password (any username).
 *  Without APP_PASSWORD the app is open, which is the local-development case. */
export async function middleware(req: NextRequest) {
  const password = process.env.APP_PASSWORD ?? "";
  if (!password) return NextResponse.next();
  const { pathname, search } = req.nextUrl;
  if (pathname === "/login" || pathname === "/api/login") return NextResponse.next();
  const token = await expectedToken(password);
  const cookie = req.cookies.get(COOKIE)?.value ?? "";
  if (cookie && timingSafeEqual(cookie, token)) return NextResponse.next();
  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      const supplied = decoded.slice(decoded.indexOf(":") + 1);
      if (timingSafeEqual(supplied, password)) return NextResponse.next();
    } catch { /* malformed header */ }
  }
  if (pathname.startsWith("/api/")) {
    return new NextResponse(JSON.stringify({ error: "password required" }), { status: 401, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  }
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
