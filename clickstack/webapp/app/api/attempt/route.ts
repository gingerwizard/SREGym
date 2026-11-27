import { NextRequest, NextResponse } from "next/server";
import { config, rows } from "@/lib/clickhouse";

export const dynamic = "force-dynamic";

/** One attempt's long texts and its tool calls in order, keyed by the run directory. */
export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id") ?? "";
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
  try {
    const c = config();
    const [head, calls] = await Promise.all([
      rows<Record<string, unknown>>(`SELECT submission, judge_reasoning, checks, dims, fault FROM ${c.attempts} FINAL WHERE run_dir = {id:String} LIMIT 1`, { id }),
      rows<Record<string, any>>(`
        SELECT i, kind, tool, area, args, sql, kubectl_tokens, probe, scripted, error, error_text, dur_s, pre_text
        FROM ${c.toolCalls} FINAL WHERE run_dir = {id:String} ORDER BY i`, { id }, { max_result_rows: "0", max_result_bytes: "0" }),
    ]);
    const steps = calls.map((s) => ({ i: s.i, kind: s.kind, tool: s.tool, area: s.area, args: s.args, sql: s.sql || null, kv: s.kubectl_tokens, probe: s.probe,
      scripted: s.scripted, error: s.error, error_text: s.error_text, dur_s: s.dur_s, pre_text: s.pre_text }));
    return NextResponse.json({ ...(head[0] ?? {}), steps }, { headers: { "Cache-Control": "private, max-age=300" } });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
