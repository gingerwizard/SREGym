import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/clickhouse";

export const dynamic = "force-dynamic";

const ALLOWED_START = /^\s*(select|with|show|describe|desc|explain)\b/i;
const FORBIDDEN = /\b(insert|alter|drop|create|truncate|grant|revoke|kill|system|attach|detach|optimize|rename|exchange|delete|update)\b/i;

/** Free-form read-only SQL for the report's SQL tab, run server-side with the configured user (readonly=1, 2,000 rows,
 *  60 seconds). One statement, SELECT-shaped, no DDL or DML keywords. */
export async function POST(req: NextRequest) {
  let sql = "";
  try { sql = String((await req.json()).sql ?? ""); } catch { /* fall through */ }
  const stmt = sql.trim().replace(/;+\s*$/, "");
  if (!stmt) return new NextResponse("empty query", { status: 400 });
  if (stmt.includes(";")) return new NextResponse("one statement at a time", { status: 400 });
  if (!ALLOWED_START.test(stmt)) return new NextResponse("only SELECT-shaped queries are allowed", { status: 400 });
  if (FORBIDDEN.test(stmt)) return new NextResponse("that statement is not allowed here", { status: 400 });
  try {
    const text = await query(stmt, {}, { max_result_rows: "2000", result_overflow_mode: "break" }, "JSONCompact");
    return new NextResponse(text, { status: 200, headers: { "Content-Type": "application/json" } });
  } catch (e) {
    return new NextResponse(String(e), { status: 400 });
  }
}
