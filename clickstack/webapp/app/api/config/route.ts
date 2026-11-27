import { NextResponse } from "next/server";
import constants from "@/lib/report-constants.json";
import { config, rows } from "@/lib/clickhouse";

export const dynamic = "force-dynamic";

/** Vocabulary and configuration the report core needs: arm order and labels, problem sets, where the SQL tab should
 *  send queries, and the schema of the results tables (names only, nothing secret). */
export async function GET() {
  try {
    const c = config();
    const [setRows, colRows, faultRows] = await Promise.all([
      rows<{ set_name: string; problem: string }>(`SELECT set_name, problem FROM ${c.problemSets} FINAL ORDER BY set_name, problem`),
      rows<{ table: string; name: string; type: string }>(
        `SELECT table, name, type FROM system.columns WHERE database = {db:String} AND table IN ({tables:Array(String)}) ORDER BY table, position`,
        { db: c.database, tables: [c.attempts, c.toolCalls, c.problemSets].map((t) => t.split(".")[1]) },
      ),
      // alias must not shadow the column used in WHERE (ClickHouse would see an aggregate in WHERE)
      rows<{ problem: string; fault_text: string }>(`SELECT problem, anyLast(fault) AS fault_text FROM ${c.attempts} FINAL WHERE fault != '' GROUP BY problem`),
    ]);
    const faults: Record<string, string> = {};
    for (const r of faultRows) faults[r.problem] = r.fault_text;
    const sets: Record<string, string[]> = {};
    for (const r of setRows) (sets[r.set_name] ??= []).push(r.problem);
    const schema: Record<string, [string, string][]> = {};
    for (const r of colRows) (schema[r.table] ??= []).push([r.name, r.type]);
    return NextResponse.json({
      ...constants,
      sets,
      faults,
      ch: { proxy: "/api/sql", database: c.database, tables: { scored: c.scored, calls: c.toolCalls }, schema },
    }, { headers: { "Cache-Control": "private, max-age=60" } });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
