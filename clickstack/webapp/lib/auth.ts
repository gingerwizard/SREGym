/** Password-only access. The deployment has one shared password (APP_PASSWORD). A correct login sets a cookie whose
 *  value is an HMAC derived from the password, so rotating the password signs everyone out. Web Crypto only, so this
 *  runs in the Edge middleware as well as in route handlers. */

export const COOKIE = "sregym_report_auth";
export const COOKIE_MAX_AGE = 60 * 60 * 24 * 30;

export function timingSafeEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export async function expectedToken(password: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(password), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode("sregym-clickstack-report-v1"));
  return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
