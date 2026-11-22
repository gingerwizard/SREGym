import "server-only";

/** Server-side access to the ClickHouse results database. Credentials and table names come from the environment and
 *  never reach the browser; every API route goes through here. */

const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;

function ident(value: string | undefined, fallback: string, what: string): string {
  const v = (value ?? "").trim() || fallback;
  if (!IDENT.test(v)) throw new Error(`${what} is not a valid identifier`);
  return v;
}

export function config() {
  const url = (process.env.CLICKHOUSE_URL ?? "").replace(/\/$/, "");
  const user = process.env.CLICKHOUSE_USER ?? "";
  const password = process.env.CLICKHOUSE_PASSWORD ?? "";
  if (!url || !user) throw new Error("CLICKHOUSE_URL and CLICKHOUSE_USER must be set");
  const database = ident(process.env.CLICKHOUSE_DATABASE, "sregym_results", "CLICKHOUSE_DATABASE");
  return {
    url, user, password, database,
    attempts: `${database}.${ident(process.env.RESULTS_ATTEMPTS_TABLE, "attempts", "RESULTS_ATTEMPTS_TABLE")}`,
    toolCalls: `${database}.${ident(process.env.RESULTS_TOOL_CALLS_TABLE, "tool_calls", "RESULTS_TOOL_CALLS_TABLE")}`,
    problemSets: `${database}.${ident(process.env.RESULTS_PROBLEM_SETS_TABLE, "problem_sets", "RESULTS_PROBLEM_SETS_TABLE")}`,
    scored: `${database}.${ident(process.env.RESULTS_SCORED_VIEW, "attempts_scored", "RESULTS_SCORED_VIEW")}`,
  };
}

export type Params = Record<string, string | number | string[]>;

const BASE_SETTINGS: Record<string, string> = {
  output_format_json_quote_64bit_integers: "0",
  max_execution_time: "60",
  readonly: "1",
};

/** Run a query; `params` are bound as ClickHouse query parameters ({name:Type} in the SQL). Returns the raw body. */
export async function query(sql: string, params: Params = {}, settings: Record<string, string> = {}, format = "JSONEachRow"): Promise<string> {
  const c = config();
  const qs = new URLSearchParams({ ...BASE_SETTINGS, ...settings, default_format: format });
  // ClickHouse reads Array(String) parameters as array literals with single-quoted elements, not JSON
  const lit = (x: string) => "'" + x.replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
  for (const [k, v] of Object.entries(params)) qs.set(`param_${k}`, Array.isArray(v) ? "[" + v.map(lit).join(",") + "]" : String(v));
  const res = await fetch(`${c.url}/?${qs}`, {
    method: "POST",
    headers: { "Content-Type": "text/plain", Authorization: "Basic " + Buffer.from(`${c.user}:${c.password}`).toString("base64") },
    body: sql,
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`ClickHouse HTTP ${res.status}: ${text.slice(0, 800)}`);
  return text;
}

/** Rows of a query in JSONEachRow form. */
export async function rows<T = Record<string, unknown>>(sql: string, params: Params = {}, settings: Record<string, string> = {}): Promise<T[]> {
  const text = await query(sql, params, settings, "JSONEachRow");
  return text.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l) as T);
}
