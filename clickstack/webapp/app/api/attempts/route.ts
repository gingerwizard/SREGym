import { NextResponse } from "next/server";
import { config, rows } from "@/lib/clickhouse";

export const dynamic = "force-dynamic";

/** Every attempt, in the shape the report core expects, without the long texts, the judge checklist and the tool
 *  traces (those come from /api/attempt and /api/queries on demand); the fault text per problem comes with /api/config. */
export async function GET() {
  try {
    const c = config();
    const data = await rows<Record<string, any>>(`
      SELECT campaign, cluster, toString(run_started_at) AS run_started_at, run_dir, model, harness, agent, family, tier, kubectl, problem, attempt,
             in_progress, diagnosis, composite, mitigation, mode, ttd_s, ttm_s, tokens_total, tokens_input, tokens_output, tokens_cached,
             cost_usd, agent_wall_s, turns, n_mcp, n_kubectl, n_shell, n_sql, n_errors, n_probe, n_fix, n_scripted, n_cs, n_sqlmcp, n_obsmcp,
             tools, tool_err, kverbs, areas, shell_kinds, run_status, timed_out, deploy_failed, incomplete_reason, unscored,
             blocked_requests, skill_read, mcp_version, judge_model
      FROM ${c.attempts} FINAL
      ORDER BY campaign, agent, model, problem, attempt`, {}, { max_result_rows: "0", max_result_bytes: "0" });
    for (const a of data) {
      a.tokens = a.tokens_total == null ? null : { total_tokens: a.tokens_total, input_tokens: a.tokens_input, output_tokens: a.tokens_output, cached_input_tokens: a.tokens_cached };
      delete a.tokens_total; delete a.tokens_input; delete a.tokens_output; delete a.tokens_cached;
      a.run_started_at = a.run_started_at === "\\N" ? null : a.run_started_at;
    }
    return NextResponse.json(data, { headers: { "Cache-Control": "private, max-age=60" } });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
