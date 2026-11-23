import { NextRequest, NextResponse } from "next/server";

/** Shared-password protection for the whole app. When APP_PASSWORD is set, every request must carry HTTP Basic
 *  credentials (user APP_USER, default "sregym"); browsers prompt once and resend them. Without APP_PASSWORD the
 *  app is open, which is the local-development case. Comparison is constant-time. */

function timingSafeEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export function middleware(req: NextRequest) {
  const password = process.env.APP_PASSWORD ?? "";
  if (!password) return NextResponse.next();
  const user = process.env.APP_USER || "sregym";
  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      const sep = decoded.indexOf(":");
      const u = decoded.slice(0, sep), p = decoded.slice(sep + 1);
      if (timingSafeEqual(u, user) && timingSafeEqual(p, password)) return NextResponse.next();
    } catch { /* malformed header: fall through to the challenge */ }
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="SREGym x ClickStack results", charset="UTF-8"', "Cache-Control": "no-store" },
  });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
