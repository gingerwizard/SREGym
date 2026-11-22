import { NextRequest, NextResponse } from "next/server";
import { config, rows } from "@/lib/clickhouse";

export const dynamic = "force-dynamic";

/** Every SQL query the agents wrote in the given attempts (by run directory), capped at 2,000 rows. */
export async function POST(req: NextRequest) {
  let ids: string[] = [];
  try { ids = ((await req.json()).ids ?? []).filter((x: unknown) => typeof x === "string").slice(0, 20000); } catch { /* empty body */ }
  if (!ids.length) return NextResponse.json([]);
  try {
    const c = config();
    const data = await rows<Record<string, any>>(`
      SELECT model, family, problem, attempt, run_dir, pre_text, sql, error, error_text
      FROM ${c.toolCalls} FINAL WHERE sql != '' AND run_dir IN ({ids:Array(String)}) ORDER BY run_dir, i LIMIT 2000`, { ids });
    return NextResponse.json(data.map((r) => ({ a: { model: r.model, family: r.family, problem: r.problem, attempt: r.attempt },
      s: { pre_text: r.pre_text, sql: r.sql, error: r.error, error_text: r.error_text } })));
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
