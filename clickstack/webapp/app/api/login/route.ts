import { NextRequest, NextResponse } from "next/server";
import { COOKIE, COOKIE_MAX_AGE, expectedToken, timingSafeEqual } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** Takes the password from the login form; on a match sets the auth cookie and sends the browser on. */
export async function POST(req: NextRequest) {
  const password = process.env.APP_PASSWORD ?? "";
  const form = await req.formData();
  const supplied = String(form.get("password") ?? "");
  const next = String(form.get("next") ?? "/");
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  if (!password || !timingSafeEqual(supplied, password)) {
    return NextResponse.redirect(new URL(`/login?error=1&next=${encodeURIComponent(safeNext)}`, req.url), 303);
  }
  const res = NextResponse.redirect(new URL(safeNext, req.url), 303);
  res.cookies.set(COOKIE, await expectedToken(password), { httpOnly: true, secure: req.nextUrl.protocol === "https:", sameSite: "lax", path: "/", maxAge: COOKIE_MAX_AGE });
  return res;
}
