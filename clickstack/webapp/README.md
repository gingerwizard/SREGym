# SREGym × ClickStack results app

Next.js app serving the benchmark report from the ClickHouse results database that `clickstack/load_results.py`
fills. The analysis and rendering logic is `lib/report-core.js`, shared with the single-file report that
`clickstack/report.py` builds; this app adds server-side data access so no credential reaches the browser.

- `app/api/attempts` every attempt (no long texts), `app/api/attempt?id=` one attempt's texts and tool calls,
  `app/api/queries` the agents' SQL for a set of attempts, `app/api/sql` read-only free SQL for the SQL tab,
  `app/api/config` vocabulary, problem sets and schema.
- Configuration is environment only (see `.env.example`): ClickHouse URL, user, password, database and table names.

Local: `cp .env.example .env.local`, fill it in, `npm install`, `npm run dev`.
Deploy: `vercel --scope daleclickhousecs-projects` from this directory (the project's environment variables hold
the credentials); Vercel Authentication should stay on, the data includes agent transcripts.
